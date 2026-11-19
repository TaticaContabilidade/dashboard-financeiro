"""
Sincronização do Omie de UMA empresa para o nosso banco (wiki:
Sincronização; D-12, D-36, D-37; RF-06, RF-07, RNF-09 a RNF-12).

    sincronizador = Sincronizador(conta_omie)
    execucao = sincronizador.executar()                    # incremental
    execucao = sincronizador.executar(reconciliacao=True)  # diária

**Incremental** (a cada ~5 min): movimentos alterados desde o ponto de
corte (o dia da última execução bem-sucedida, relido de propósito) até
hoje, mais o saldo de hoje das contas ativas. Sem execução anterior, faz a
carga completa (reconciliação).

**Reconciliação** (uma vez por dia): relê cadastros e todos os movimentos e
apaga do nosso banco o que sumiu do Omie (ex.: previsão faturada, título
excluído). Só apaga depois de ler TUDO com sucesso, e nunca uma fatia
grande demais de uma vez (`LIMITE_EXCLUSAO`): isso indica leitura
incompleta, não exclusão real.

Regras de segurança:
- Uma empresa por vez, com lock próprio no Postgres: se a rodada anterior
  ainda está nela, esta pula a empresa (RNF-10).
- O ponto de corte só avança com sucesso (RNF-11).
- Credencial recusada suspende a empresa; bloqueio do Omie (425) e erro
  permanente pausam por 30 min; erro passageiro só registra (RNF-09).
- Nada de valor do Omie no log nem na execução: só contagens, motivos e o
  fault (RNF-04).
"""

import logging
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, timedelta

from django.db import connection, transaction
from django.utils import timezone

from apps.financeiro.models import (
    Categoria,
    ClienteFornecedor,
    ContaCorrente,
    LancamentoConta,
    SaldoConta,
    Titulo,
)

from .cliente import ClienteOmie
from .conversao import (
    Convertido,
    LinhaIgnorada,
    converter_categoria,
    converter_cliente,
    converter_conta_corrente,
    converter_movimento,
    converter_saldo,
)
from .erros import ErroBloqueio, ErroCredencial, ErroOmie, ErroPermanente, SemRegistros
from .models import ContaOmie, ExecucaoSincronizacao

logger = logging.getLogger(__name__)

# Carga completa: o filtro de alteração traz títulos antigos sem a regra
# dos 30 dias (D-34). Data bem anterior a qualquer uso do Omie.
INICIO_DOS_TEMPOS = date(2000, 1, 1)
PAUSA = timedelta(minutes=30)
TAMANHO_LOTE = 500
# Reconciliação não apaga mais que 20% de uma tabela de uma vez (acima de
# 50 linhas): seria sinal de leitura incompleta.
LIMITE_EXCLUSAO = 0.20
MINIMO_PARA_LIMITE = 50
NAMESPACE_LOCK = 7301

# Nome curto de cada tabela nas contagens, e os campos da chave única.
TABELAS = {
    Titulo: ('titulos', ['tipo', 'codigo_titulo']),
    LancamentoConta: ('lancamentos', ['natureza', 'codigo_movimento', 'codigo_baixa']),
    ContaCorrente: ('contas_correntes', ['codigo']),
    Categoria: ('categorias', ['codigo']),
    ClienteFornecedor: ('clientes', ['codigo']),
    SaldoConta: ('saldos', ['codigo_conta_corrente', 'data']),
}


@contextmanager
def lock_da_empresa(empresa_id: int):
    """`pg_try_advisory_lock` por empresa. Entrega True se conseguiu o lock
    (e o solta no fim), False se outra sessão já está nessa empresa."""
    chave = (NAMESPACE_LOCK << 32) | int(empresa_id)
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [chave])
        conseguiu = cursor.fetchone()[0]
    try:
        yield conseguiu
    finally:
        if conseguiu:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [chave])


def _br(dia: date) -> str:
    return dia.strftime('%d/%m/%Y')


class Sincronizador:
    def __init__(self, conta: ContaOmie, *, cliente=None, agora=None):
        self.conta = conta
        self.empresa_id = conta.empresa_id
        self._cliente = cliente
        self.agora = agora or timezone.now()
        self.hoje = timezone.localdate(self.agora)

    @property
    def cliente(self):
        if self._cliente is None:
            self._cliente = ClienteOmie.da_conta(self.conta)
        return self._cliente

    # --- entrada -------------------------------------------------------------

    def executar(self, *, reconciliacao: bool = False) -> ExecucaoSincronizacao | None:
        """Roda uma execução e devolve o registro dela, ou None se outra
        rodada já está nesta empresa."""
        with lock_da_empresa(self.empresa_id) as conseguiu:
            if not conseguiu:
                logger.info('sincronização empresa=%s pulada: outra rodada em andamento', self.empresa_id)
                return None
            corte = ExecucaoSincronizacao.objects.da_empresa(self.empresa_id).ultima_bem_sucedida()
            if corte is None or corte.alterados_ate is None:
                reconciliacao = True  # primeira vez: carga completa
            tipo = ExecucaoSincronizacao.Tipo.RECONCILIACAO if reconciliacao else ExecucaoSincronizacao.Tipo.INCREMENTAL
            desde = INICIO_DOS_TEMPOS if reconciliacao else corte.alterados_ate
            execucao = ExecucaoSincronizacao.objects.create(
                empresa_id=self.empresa_id, tipo=tipo, alterados_desde=desde, alterados_ate=self.hoje,
            )
            self.contagens = defaultdict(Counter)
            try:
                if reconciliacao:
                    self._reconciliar(desde)
                else:
                    self._incremental(desde)
            except Exception as erro:  # noqa: BLE001 — registra qualquer falha e decide o que fazer
                self._falhou(execucao, erro)
                if not isinstance(erro, ErroOmie):
                    raise  # bug nosso: registra e sobe, para o comando sinalizar
            else:
                self._concluiu(execucao)
            return execucao

    # --- rodadas -------------------------------------------------------------

    def _incremental(self, desde: date):
        self._movimentos(desde)
        self._saldos()

    def _reconciliar(self, desde: date):
        vistos = {}
        vistos[ContaCorrente] = self._gravar_todos(self.cliente.listar_contas_correntes(), converter_conta_corrente)
        vistos[Categoria] = self._gravar_todos(self.cliente.listar_categorias(), converter_categoria)
        vistos[ClienteFornecedor] = self._gravar_todos(self.cliente.listar_clientes(), converter_cliente)
        movimentos = self._movimentos(desde)
        vistos[Titulo] = movimentos[Titulo]
        vistos[LancamentoConta] = movimentos[LancamentoConta]
        self._saldos()
        # Só aqui, com tudo lido sem erro, as exclusões são aplicadas.
        for modelo, chaves in vistos.items():
            self._excluir_sumidos(modelo, chaves)

    # --- leitura e gravação --------------------------------------------------

    def _movimentos(self, desde: date) -> dict:
        filtros = {'dDtAltDe': _br(desde), 'dDtAltAte': _br(self.hoje)}
        return self._gravar_varios(self.cliente.listar_movimentos(filtros), converter_movimento)

    def _gravar_todos(self, itens, conversor) -> set:
        """Para listas de um modelo só (cadastros). Devolve as chaves vistas."""
        vistos = self._gravar_varios(itens, conversor)
        return set().union(*vistos.values()) if vistos else set()

    def _gravar_varios(self, itens, conversor) -> dict:
        """Converte e grava em lotes. Devolve {modelo: chaves vistas}."""
        vistos = defaultdict(set)
        lote = defaultdict(dict)  # modelo -> {chave: Convertido}; repetida no lote: fica a última
        for item in itens:
            try:
                convertido = conversor(item)
            except LinhaIgnorada as erro:
                self.contagens['ignorados'][erro.motivo] += 1
                continue
            chave = tuple(convertido.chave[campo] for campo in TABELAS[convertido.modelo][1])
            vistos[convertido.modelo].add(chave)
            lote[convertido.modelo][chave] = convertido
            if sum(len(v) for v in lote.values()) >= TAMANHO_LOTE:
                self._gravar_lote(lote)
                lote = defaultdict(dict)
        self._gravar_lote(lote)
        return vistos

    def _gravar_lote(self, lote: dict):
        for modelo, convertidos in lote.items():
            if not convertidos:
                continue
            nome, campos_chave = TABELAS[modelo]
            objetos = [modelo(empresa_id=self.empresa_id, **c.chave, **c.campos) for c in convertidos.values()]
            campos_valor = [f for f in next(iter(convertidos.values())).campos] + ['sincronizado_em']
            with transaction.atomic():
                modelo.objects.bulk_create(
                    objetos,
                    update_conflicts=True,
                    unique_fields=['empresa_id', *campos_chave],
                    update_fields=campos_valor,
                )
            self.contagens[nome]['gravados'] += len(objetos)

    def _saldos(self):
        contas = ContaCorrente.objects.da_empresa(self.empresa_id).filter(ativa=True).values_list('codigo', flat=True)
        lote = defaultdict(dict)
        for codigo in contas:
            try:
                resposta = self.cliente.saldo_conta_corrente(codigo, _br(self.hoje), _br(self.hoje))
            except SemRegistros:
                continue
            convertido: Convertido = converter_saldo(resposta, codigo, self.hoje)
            lote[SaldoConta][(codigo, self.hoje)] = convertido
        self._gravar_lote(lote)

    def _excluir_sumidos(self, modelo, chaves_vistas: set):
        nome, campos_chave = TABELAS[modelo]
        atuais = modelo.objects.da_empresa(self.empresa_id).values_list('id', *campos_chave)
        sumidos = [linha[0] for linha in atuais if tuple(linha[1:]) not in chaves_vistas]
        if not sumidos:
            return
        total = modelo.objects.da_empresa(self.empresa_id).count()
        if total >= MINIMO_PARA_LIMITE and len(sumidos) > total * LIMITE_EXCLUSAO:
            self.contagens['exclusoes_bloqueadas'][nome] = len(sumidos)
            logger.warning(
                'reconciliação empresa=%s: %s de %s linhas de %s sumiram do Omie; exclusão NÃO aplicada '
                '(acima do limite, provável leitura incompleta)', self.empresa_id, len(sumidos), total, nome,
            )
            return
        modelo.objects.da_empresa(self.empresa_id).filter(id__in=sumidos).delete()
        self.contagens['excluidos'][nome] = len(sumidos)

    # --- fim da execução -----------------------------------------------------

    def _concluiu(self, execucao: ExecucaoSincronizacao):
        execucao.situacao = ExecucaoSincronizacao.Situacao.SUCESSO
        execucao.finalizada_em = timezone.now()
        execucao.contagens = {k: dict(v) for k, v in self.contagens.items()}
        execucao.save()
        ContaOmie.objects.filter(pk=self.conta.pk).update(ultima_sincronizacao_em=execucao.finalizada_em)
        logger.info('sincronização empresa=%s %s ok: %s', self.empresa_id, execucao.tipo, execucao.contagens)

    def _falhou(self, execucao: ExecucaoSincronizacao, erro: Exception):
        execucao.situacao = ExecucaoSincronizacao.Situacao.ERRO
        execucao.finalizada_em = timezone.now()
        execucao.contagens = {k: dict(v) for k, v in self.contagens.items()}
        execucao.erro_tipo = type(erro).__name__[:40]
        execucao.erro_detalhe = _detalhe(erro)
        execucao.save()

        mudancas = {}
        if isinstance(erro, ErroCredencial):
            mudancas = {'suspensa_em': execucao.finalizada_em,
                        'motivo_suspensao': 'Credencial recusada pelo Omie. Rode configurar_conta_omie.'}
        elif isinstance(erro, (ErroBloqueio, ErroPermanente)):
            mudancas = {'pausada_ate': execucao.finalizada_em + PAUSA}
        if mudancas:
            ContaOmie.objects.filter(pk=self.conta.pk).update(**mudancas)
        logger.warning('sincronização empresa=%s %s falhou: %s %s', self.empresa_id, execucao.tipo,
                       execucao.erro_tipo, 'suspensa' if 'suspensa_em' in mudancas else
                       'pausada 30 min' if mudancas else 'tenta na próxima rodada')


def _detalhe(erro: Exception) -> str:
    """Mensagem nossa + status/fault do Omie. Erro que não é do Omie (bug)
    guarda só a classe: a mensagem de uma exceção qualquer pode conter
    dado."""
    if isinstance(erro, ErroOmie):
        return (f'{erro} | status={erro.status} faultcode={erro.faultcode!r} '
                f'faultstring={erro.faultstring[:300]!r}')[:1000]
    return f'erro inesperado ({type(erro).__name__}); ver o log do processo'
