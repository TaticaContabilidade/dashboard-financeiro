"""
Testa a conexão com o Omie de uma empresa e responde as dúvidas abertas
do mapeamento (wiki: Integração Omie, "Pendências"):

    python manage.py configurar_conta_omie --empresa-id 1   # se ainda não cadastrou
    python manage.py testar_conta_omie --empresa-id 1

Imprime SÓ contagens, nomes de campos, tipos e nomes de status — nunca
valores, nomes de clientes/fornecedores, CNPJ ou descrições (RNF-04). A
saída pode ser colada na conversa/wiki sem expor dado financeiro.

São ~10 chamadas ao Omie (mais as páginas), todas de leitura, com
parâmetros diferentes entre si (evita a regra de consulta repetida em
< 60 s). A etapa 8 não chama o Omie: reaproveita os movimentos da etapa 4.
"""

from collections import Counter
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.omie.cliente import ClienteOmie
from apps.omie.erros import ErroCredencial, ErroOmie, SemRegistros
from apps.omie.models import ContaOmie


def estrutura(valor, nivel: int = 0, max_nivel: int = 3) -> list[str]:
    """Nomes de campos e tipos, sem nenhum valor."""
    linhas = []
    recuo = '  ' * nivel
    if isinstance(valor, dict):
        for chave in sorted(valor):
            item = valor[chave]
            tipo = type(item).__name__
            if isinstance(item, (dict, list)) and nivel < max_nivel:
                linhas.append(f'{recuo}{chave}: {tipo}')
                linhas.extend(estrutura(item, nivel + 1, max_nivel))
            else:
                linhas.append(f'{recuo}{chave}: {tipo}')
    elif isinstance(valor, list) and valor:
        linhas.append(f'{recuo}[{len(valor)} itens; estrutura do 1º]')
        linhas.extend(estrutura(valor[0], nivel, max_nivel))
    return linhas


def _br(dia: date) -> str:
    return dia.strftime('%d/%m/%Y')


def _data_omie(texto) -> date | None:
    try:
        d, m, a = str(texto).split('/')
        return date(int(a), int(m), int(d))
    except (ValueError, AttributeError):
        return None


class Command(BaseCommand):
    help = 'Testa a conta Omie de uma empresa e mostra estrutura/contagens (sem valores).'

    def add_arguments(self, parser):
        parser.add_argument('--empresa-id', type=int, required=True)
        parser.add_argument('--dias', type=int, default=7, help='janela de alteração dos movimentos (padrão 7)')

    def handle(self, *args, empresa_id, dias, **opcoes):
        conta = ContaOmie.objects.da_empresa(empresa_id).first()
        if conta is None:
            raise CommandError(f'A empresa {empresa_id} não tem conta Omie. Rode configurar_conta_omie antes.')
        self.cliente = ClienteOmie.da_conta(conta)
        self.hoje = timezone.localdate()
        self.dias = dias

        etapas = [
            ('1. Contas correntes', self.contas_correntes),
            ('2. Tamanho máximo de página (ListarMovimentos)', self.tamanho_pagina),
            (f'3. Movimentos alterados nos últimos {dias} dias', self.movimentos),
            ('4. Regra dos 30 dias (alterados nos últimos 90 dias)', self.regra_30_dias),
            ('5. Contas a pagar (1 registro, só estrutura)', self.contas_pagar),
            ('6. Saldos (ListarExtrato, 1ª conta ativa)', self.saldos),
            ('7. Categorias', self.categorias),
            ('8. Chave única e tipos dos movimentos (reusa a etapa 4, sem chamar o Omie)', self.chave_movimentos),
        ]
        self.conta_corrente = None
        self.movimentos_90 = None
        self.por_pagina = 100
        for titulo, etapa in etapas:
            self.stdout.write(self.style.MIGRATE_HEADING(f'\n{titulo}'))
            try:
                etapa()
            except ErroCredencial as erro:
                self._erro(erro)
                raise CommandError('Credencial recusada pelo Omie — testes interrompidos.') from erro
            except ErroOmie as erro:
                self._erro(erro)
        self.stdout.write(self.style.SUCCESS('\nFim. Cole esta saída na conversa para fecharmos o mapeamento.'))

    # --- saída -------------------------------------------------------------

    def _erro(self, erro: ErroOmie):
        self.stdout.write(self.style.ERROR(
            f'  {type(erro).__name__}: {erro} | status={erro.status} '
            f'faultcode={erro.faultcode!r} faultstring={erro.faultstring[:200]!r}'
        ))

    def _linhas(self, linhas):
        for linha in linhas:
            self.stdout.write(f'  {linha}')

    # --- etapas -------------------------------------------------------------

    def contas_correntes(self):
        contas = list(self.cliente.listar_contas_correntes())
        ativas = [c for c in contas if c.get('inativo') != 'S']
        self._linhas([f'total: {len(contas)} | ativas: {len(ativas)}'])
        if contas:
            self._linhas(['estrutura:', *estrutura(contas[0], 1)])
        if ativas:
            self.conta_corrente = ativas[0].get('nCodCC')

    def tamanho_pagina(self):
        filtros = {'dDtAltDe': _br(self.hoje - timedelta(days=90)), 'dDtAltAte': _br(self.hoje)}
        for tentativa in (500, 100, 50):
            try:
                resposta = self.cliente.chamar(
                    'financas/mf', 'ListarMovimentos', {**filtros, 'nPagina': 1, 'nRegPorPagina': tentativa}
                )
            except SemRegistros:
                self._linhas([f'pedido {tentativa}: sem registros no período — não dá para concluir'])
                return
            except ErroOmie as erro:
                self._linhas([f'pedido {tentativa}: recusado ({type(erro).__name__}: {erro.faultstring[:120]!r})'])
                continue
            veio = len(resposta.get('movimentos') or [])
            total = resposta.get('nTotRegistros')
            self._linhas([f'pedido {tentativa}: vieram {veio} | nRegistros={resposta.get("nRegistros")} '
                          f'| nTotRegistros={total} | nTotPaginas={resposta.get("nTotPaginas")}'])
            if veio == tentativa or (isinstance(total, int) and veio < tentativa and veio == total):
                self.por_pagina = tentativa
                self._linhas([f'=> usar {tentativa} por página'])
                return
            # veio menos que o pedido e há mais registros: o Omie cortou
            self.por_pagina = max(veio, 1)
            self._linhas([f'=> Omie limitou a {veio} por página'])
            return

    def movimentos(self):
        filtros = {'dDtAltDe': _br(self.hoje - timedelta(days=self.dias)), 'dDtAltAte': _br(self.hoje)}
        lista = list(self.cliente.listar_movimentos(filtros, por_pagina=self.por_pagina))
        detalhes = [m.get('detalhes') or {} for m in lista]
        self._linhas([
            f'total: {len(lista)}',
            f'por natureza (P=pagar, R=receber): {dict(Counter(d.get("cNatureza") for d in detalhes))}',
            f'por status: {dict(Counter(d.get("cStatus") for d in detalhes))}',
            f'com data de pagamento: {sum(1 for d in detalhes if d.get("dDtPagamento"))}',
            f'com conta corrente (nCodCC): {sum(1 for d in detalhes if d.get("nCodCC"))}',
        ])
        if lista:
            self._linhas(['estrutura de um movimento:', *estrutura(lista[0], 1)])

    def regra_30_dias(self):
        filtros = {'dDtAltDe': _br(self.hoje - timedelta(days=90)), 'dDtAltAte': _br(self.hoje - timedelta(days=1))}
        lista = list(self.cliente.listar_movimentos(filtros, por_pagina=self.por_pagina))
        self.movimentos_90 = lista
        limite = self.hoje - timedelta(days=30)
        emissoes = [_data_omie((m.get('detalhes') or {}).get('dDtEmissao')) for m in lista]
        antigos = sum(1 for e in emissoes if e and e < limite)
        self._linhas([
            f'total: {len(lista)} | emitidos há mais de 30 dias: {antigos}',
            '=> se for 0 com total alto, o filtro de alteração provavelmente cai na regra dos 30 dias'
            if lista and not antigos else '=> o filtro de alteração traz títulos antigos (sem a regra dos 30 dias)'
            if antigos else '=> sem dados suficientes para concluir',
        ])

    def contas_pagar(self):
        try:
            resposta = self.cliente.chamar(
                'financas/contapagar', 'ListarContasPagar', {'pagina': 1, 'registros_por_pagina': 1}
            )
        except SemRegistros:
            self._linhas(['sem contas a pagar'])
            return
        lista = resposta.get('conta_pagar_cadastro') or []
        self._linhas([f'total na conta: {resposta.get("total_de_registros")}'])
        if lista:
            self._linhas(['estrutura:', *estrutura(lista[0], 1)])

    def saldos(self):
        if not self.conta_corrente:
            self._linhas(['nenhuma conta corrente ativa — pulado'])
            return
        resposta = self.cliente.saldo_conta_corrente(self.conta_corrente, _br(self.hoje), _br(self.hoje))
        campos = sorted(k for k in resposta if k.startswith('nSaldo'))
        self._linhas([f'campos de saldo: {campos}', f'demais campos: {sorted(set(resposta) - set(campos))}'])

    def categorias(self):
        lista = list(self.cliente.listar_categorias())
        self._linhas([
            f'total: {len(lista)} | receita: {sum(1 for c in lista if c.get("conta_receita") == "S")} '
            f'| despesa: {sum(1 for c in lista if c.get("conta_despesa") == "S")}'
        ])

    def chave_movimentos(self):
        """Responde, só com contagens: o `nCodTitulo` se repete? Que
        combinação de campos identifica uma linha? Que códigos aparecem em
        cGrupo/cOrigem/cTipo? Os valores vêm como int ou float?"""
        if not self.movimentos_90:
            self._linhas(['sem movimentos da etapa 4 — pulado'])
            return
        detalhes = [m.get('detalhes') or {} for m in self.movimentos_90]
        resumos = [m.get('resumo') or {} for m in self.movimentos_90]
        total = len(detalhes)

        sem_titulo = sum(1 for d in detalhes if not d.get('nCodTitulo'))
        por_titulo = Counter(d.get('nCodTitulo') for d in detalhes if d.get('nCodTitulo'))
        repetidos = {cod: n for cod, n in por_titulo.items() if n > 1}
        self._linhas([
            f'linhas: {total} | sem nCodTitulo (0/vazio): {sem_titulo} | nCodTitulo distintos: {len(por_titulo)}',
            f'nCodTitulo que aparecem mais de 1 vez: {len(repetidos)} '
            f'(maior repetição: {max(repetidos.values()) if repetidos else 0})',
        ])

        # Candidatas a chave única: quantas linhas colidem em cada uma.
        candidatas = {
            'nCodTitulo': ('nCodTitulo',),
            'nCodTitulo+cGrupo': ('nCodTitulo', 'cGrupo'),
            'nCodTitulo+dDtPagamento': ('nCodTitulo', 'dDtPagamento'),
            'nCodTitulo+cGrupo+dDtPagamento': ('nCodTitulo', 'cGrupo', 'dDtPagamento'),
            'nCodTitulo+nCodCC+cGrupo+dDtPagamento': ('nCodTitulo', 'nCodCC', 'cGrupo', 'dDtPagamento'),
        }
        self._linhas(['linhas que colidem com outra em cada chave candidata (0 = chave única):'])
        for nome, campos in candidatas.items():
            chaves = Counter(tuple(d.get(c) for c in campos) for d in detalhes)
            colisoes = sum(n for n in chaves.values() if n > 1)
            self._linhas([f'  {nome}: {colisoes}'])

        if repetidos:
            linhas_rep = [d for d in detalhes if d.get('nCodTitulo') in repetidos]
            self._linhas([
                'nas linhas com nCodTitulo repetido:',
                f'  por cGrupo: {dict(Counter(_codigo(d.get("cGrupo")) for d in linhas_rep))}',
                f'  por cStatus: {dict(Counter(_codigo(d.get("cStatus")) for d in linhas_rep))}',
                f'  com dDtPagamento: {sum(1 for d in linhas_rep if d.get("dDtPagamento"))}',
            ])

        self._linhas([
            f'por cGrupo: {dict(Counter(_codigo(d.get("cGrupo")) for d in detalhes))}',
            f'por cOrigem: {dict(Counter(_codigo(d.get("cOrigem")) for d in detalhes))}',
            f'por cTipo: {dict(Counter(_codigo(d.get("cTipo")) for d in detalhes))}',
            f'cGrupo x natureza: {dict(Counter((_codigo(d.get("cGrupo")), d.get("cNatureza")) for d in detalhes))}',
            f'com nCodTitRepet (título recorrente): {sum(1 for d in detalhes if d.get("nCodTitRepet"))}',
        ])

        tipos = Counter()
        for d, r in zip(detalhes, resumos):
            for campo, valor in (('nValorTitulo', d.get('nValorTitulo')), ('nValPago', r.get('nValPago')),
                                 ('nValAberto', r.get('nValAberto'))):
                tipos[(campo, type(valor).__name__)] += 1
        self._linhas([f'tipos dos valores: {dict(sorted(tipos.items()))}'])


def _codigo(valor) -> str:
    """Códigos curtos (ex.: CONTA_A_PAGAR, PAGO) passam; qualquer texto
    comprido vira marcador, para um campo livre nunca vazar na saída."""
    texto = '' if valor is None else str(valor)
    if len(texto) > 25:
        return '<texto longo>'
    return texto
