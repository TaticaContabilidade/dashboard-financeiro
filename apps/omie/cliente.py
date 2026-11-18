"""
Cliente HTTP da API do Omie (wiki: Integração Omie).

    cliente = ClienteOmie.da_conta(conta_omie)
    for movimento in cliente.listar_movimentos({'dDtAltDe': '09/11/2026', 'dDtAltAte': '10/11/2026'}):
        ...

- Toda chamada: POST JSON em https://app.omie.com.br/api/v1/<serviço>/
  com {call, app_key, app_secret, param: [ {...} ]}.
- Trava de taxa global (`limitador.py`) antes de cada requisição.
- Re-tenta sozinho só falha passageira de rede/5xx (poucas vezes, com
  espera crescente). Limite, redundância, bloqueio, credencial e erro
  permanente sobem como exceção (`erros.py`) para a sincronização decidir.
- "Não existem registros" vira lista vazia.
- Nunca loga credenciais nem o conteúdo das respostas (RNF-03/RNF-04):
  só serviço, método, página, status e duração.
"""

import logging
import time
from collections.abc import Callable, Iterator

import requests

from .erros import ErroOmie, ErroTransitorio, SemRegistros, classificar
from .limitador import Limitador, limitador_global

logger = logging.getLogger(__name__)

URL_BASE = 'https://app.omie.com.br/api/v1/'
TIMEOUT_SEGUNDOS = 60
REGISTROS_POR_PAGINA = 100  # documentação conflitante (50/100/500): confirmar no teste real
ESPERAS_RETENTATIVA = (2.0, 5.0)  # 2 re-tentativas para falha passageira

# (status_http, corpo_json_ou_None)
Transporte = Callable[[str, dict, float], tuple[int, dict | None]]


def _transporte_requests(sessao: requests.Session) -> Transporte:
    def enviar(url: str, corpo: dict, timeout: float) -> tuple[int, dict | None]:
        try:
            resposta = sessao.post(url, json=corpo, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as erro:
            raise ErroTransitorio(f'Falha de rede ao chamar o Omie: {type(erro).__name__}.') from erro
        try:
            dados = resposta.json()
        except ValueError:
            dados = None
        return resposta.status_code, dados

    return enviar


class ClienteOmie:
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        *,
        transporte: Transporte | None = None,
        limitador: Limitador | None = None,
        dormir: Callable[[float], None] = time.sleep,
        url_base: str = URL_BASE,
    ):
        if not app_key or not app_secret:
            raise ValueError('app_key e app_secret são obrigatórios.')
        self._app_key = app_key
        self._app_secret = app_secret
        self._transporte = transporte or _transporte_requests(requests.Session())
        self._limitador = limitador or limitador_global
        self._dormir = dormir
        self._url_base = url_base

    def __repr__(self) -> str:  # nunca mostrar credenciais
        return '<ClienteOmie>'

    @classmethod
    def da_conta(cls, conta, **kwargs) -> 'ClienteOmie':
        app_key, app_secret = conta.credenciais()
        return cls(app_key, app_secret, **kwargs)

    # --- chamada única ---------------------------------------------------

    def chamar(self, servico: str, metodo: str, param: dict) -> dict:
        """Uma chamada. Devolve o JSON de resposta ou lança ErroOmie /
        SemRegistros."""
        url = f'{self._url_base}{servico.strip("/")}/'
        corpo = {'call': metodo, 'app_key': self._app_key, 'app_secret': self._app_secret, 'param': [param]}
        esperas = list(ESPERAS_RETENTATIVA)

        while True:
            self._limitador.aguardar_vez(self._app_key, metodo)
            inicio = time.monotonic()
            try:
                status, dados = self._transporte(url, corpo, TIMEOUT_SEGUNDOS)
                erro = classificar(status, dados)
            except ErroOmie as falha:
                status, erro = None, falha
            duracao = time.monotonic() - inicio

            logger.info(
                'omie %s.%s pagina=%s status=%s %.2fs%s',
                servico, metodo, _pagina(param), status, duracao,
                f' erro={type(erro).__name__}' if erro else '',
            )
            if erro is None:
                return dados
            if type(erro) is ErroTransitorio and esperas:
                # Só falha passageira "pura" (rede/5xx). Limite e
                # redundância também são transitórios, mas re-tentar na
                # hora só piora — sobem para a sincronização.
                self._dormir(esperas.pop(0))
                continue
            raise erro

    # --- paginação -------------------------------------------------------

    def listar(
        self,
        servico: str,
        metodo: str,
        param: dict,
        *,
        chave_lista: str,
        campo_pagina: str = 'pagina',
        campo_por_pagina: str = 'registros_por_pagina',
        campo_total_paginas: str = 'total_de_paginas',
        por_pagina: int = REGISTROS_POR_PAGINA,
    ) -> Iterator[dict]:
        """Percorre todas as páginas e devolve os registros um a um."""
        pagina = 1
        while True:
            pedido = {**param, campo_pagina: pagina, campo_por_pagina: por_pagina}
            try:
                resposta = self.chamar(servico, metodo, pedido)
            except SemRegistros:
                return
            yield from resposta.get(chave_lista) or []
            total = int(resposta.get(campo_total_paginas) or 0)
            if pagina >= total:
                return
            pagina += 1

    # --- atalhos dos métodos mapeados (wiki: Integração Omie) ------------

    def listar_movimentos(self, filtros: dict | None = None, **kwargs) -> Iterator[dict]:
        return self.listar(
            'financas/mf', 'ListarMovimentos', filtros or {},
            chave_lista='movimentos', campo_pagina='nPagina',
            campo_por_pagina='nRegPorPagina', campo_total_paginas='nTotPaginas', **kwargs,
        )

    def listar_contas_pagar(self, filtros: dict | None = None, **kwargs) -> Iterator[dict]:
        return self.listar(
            'financas/contapagar', 'ListarContasPagar', filtros or {}, chave_lista='conta_pagar_cadastro', **kwargs
        )

    def listar_contas_receber(self, filtros: dict | None = None, **kwargs) -> Iterator[dict]:
        return self.listar(
            'financas/contareceber', 'ListarContasReceber', filtros or {},
            chave_lista='conta_receber_cadastro', **kwargs,
        )

    def listar_contas_correntes(self, filtros: dict | None = None, **kwargs) -> Iterator[dict]:
        return self.listar(
            'geral/contacorrente', 'ListarContasCorrentes', filtros or {},
            chave_lista='ListarContasCorrentes', **kwargs,
        )

    def listar_categorias(self, filtros: dict | None = None, **kwargs) -> Iterator[dict]:
        return self.listar(
            'geral/categorias', 'ListarCategorias', filtros or {}, chave_lista='categoria_cadastro', **kwargs
        )

    def listar_clientes(self, filtros: dict | None = None, **kwargs) -> Iterator[dict]:
        return self.listar('geral/clientes', 'ListarClientes', filtros or {}, chave_lista='clientes_cadastro', **kwargs)

    def saldo_conta_corrente(self, codigo_conta: int, data_inicial: str, data_final: str) -> dict:
        return self.chamar(
            'financas/extrato', 'ListarExtrato',
            {'nCodCC': codigo_conta, 'dPeriodoInicial': data_inicial, 'dPeriodoFinal': data_final,
             'cExibirApenasSaldo': 'S'},
        )


def _pagina(param: dict):
    return param.get('pagina', param.get('nPagina', '-'))
