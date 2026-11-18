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
< 60 s). As etapas 8 a 10 não chamam o Omie: reaproveitam os movimentos da
etapa 4.
"""

from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.omie.cliente import ClienteOmie
from apps.omie.conversao import (
    LinhaIgnorada,
    converter_categoria,
    converter_cliente,
    converter_conta_corrente,
)
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
            ('9. Identificador e campos por cGrupo (reusa a etapa 4, sem chamar o Omie)', self.campos_por_grupo),
            ('10. Chave da conta corrente, baixas em lote e previsões (reusa a etapa 4)', self.conta_corrente_e_previsoes),
            ('11. Cadastros: estrutura e conversão para as tabelas (1 chamada nova)', self.cadastros),
        ]
        self.conta_corrente = None
        self.movimentos_90 = None
        self.contas_lista = []
        self.categorias_lista = []
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
        self.contas_lista = contas
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
        self.categorias_lista = lista
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


    def campos_por_grupo(self):
        """A estrutura da etapa 3 é só do 1º movimento; aqui, para cada
        cGrupo: que campos aparecem (e em quantas linhas), qual campo
        `nCod*` identifica a linha sem colisão, e como os grupos se ligam
        pelo nCodTitulo. Só nomes de campos e contagens."""
        if not self.movimentos_90:
            self._linhas(['sem movimentos da etapa 4 — pulado'])
            return
        grupos: dict[str, list[tuple[dict, dict]]] = {}
        for m in self.movimentos_90:
            d, r = m.get('detalhes') or {}, m.get('resumo') or {}
            grupos.setdefault(_codigo(d.get('cGrupo')), []).append((d, r))

        titulos_por_grupo = {
            g: {d.get('nCodTitulo') for d, _ in linhas if d.get('nCodTitulo')} for g, linhas in grupos.items()
        }
        titulos_de_conta = titulos_por_grupo.get('CONTA_A_PAGAR', set()) | titulos_por_grupo.get('CONTA_A_RECEBER', set())

        for grupo in sorted(grupos):
            linhas = grupos[grupo]
            self._linhas([f'{grupo}: {len(linhas)} linhas'])
            for parte, indice in (('detalhes', 0), ('resumo', 1)):
                presenca = Counter()
                for par in linhas:
                    presenca.update(k for k, v in par[indice].items() if v not in (None, '', 0))
                todos = sorted({k for par in linhas for k in par[indice]})
                self._linhas([f'  {parte} (campo: linhas com valor): '
                              + ', '.join(f'{k}:{presenca[k]}' for k in todos)])
            ids = sorted({k for d, _ in linhas for k in d if k.startswith('nCod')})
            colisoes = []
            for campo in ids:
                valores = [d.get(campo) for d, _ in linhas if d.get(campo)]
                contagem = Counter(valores)
                colisoes.append(f'{campo}: preenchido {len(valores)}, colide {sum(n for n in contagem.values() if n > 1)}')
            self._linhas(['  ids (preenchido / linhas que colidem): ' + ' | '.join(colisoes)])
            if grupo not in ('CONTA_A_PAGAR', 'CONTA_A_RECEBER'):
                proprios = titulos_por_grupo.get(grupo, set())
                self._linhas([f'  nCodTitulo distintos: {len(proprios)} | também presentes em CONTA_A_*: '
                              f'{len(proprios & titulos_de_conta)}'])
        

    def conta_corrente_e_previsoes(self):
        """Fecha a chave das linhas de conta corrente (nCodBaixa quando é
        baixa de título, nCodMovCC quando é lançamento direto), testa a
        hipótese de baixa em lote (um movimento bancário pagando vários
        títulos) e se a PREVISAO_CONTRATO convive com o título já faturado.
        Só contagens."""
        if not self.movimentos_90:
            self._linhas(['sem movimentos da etapa 4 — pulado'])
            return
        linhas = [(m.get('detalhes') or {}, m.get('resumo') or {}) for m in self.movimentos_90]
        cc = [(d, r) for d, r in linhas if d.get('cGrupo') in ('CONTA_CORRENTE_PAG', 'CONTA_CORRENTE_REC')]

        def colisoes(chaves) -> int:
            return sum(n for n in Counter(chaves).values() if n > 1)

        sem_mov = [d for d, _ in cc if not d.get('nCodMovCC')]
        self._linhas([
            f'linhas de conta corrente: {len(cc)}',
            f'colisões em cGrupo+nCodMovCC+nCodBaixa: '
            f'{colisoes((d.get("cGrupo"), d.get("nCodMovCC") or 0, d.get("nCodBaixa") or 0) for d, _ in cc)}',
            f'colisões em cGrupo + (nCodBaixa se houver, senão nCodMovCC): '
            f'{colisoes((d.get("cGrupo"), ("B", d["nCodBaixa"]) if d.get("nCodBaixa") else ("M", d.get("nCodMovCC") or 0)) for d, _ in cc)}',
            f'sem nCodMovCC: {len(sem_mov)} (com nCodBaixa: {sum(1 for d in sem_mov if d.get("nCodBaixa"))} '
            f'| por cOrigem: {dict(Counter(_codigo(d.get("cOrigem")) for d in sem_mov))})',
        ])

        por_mov = defaultdict(list)
        for d, r in cc:
            if d.get('nCodMovCC'):
                por_mov[(d.get('cGrupo'), d['nCodMovCC'])].append((d, r))
        lotes = [v for v in por_mov.values() if len(v) > 1]
        simples = [v[0] for v in por_mov.values() if len(v) == 1]
        self._linhas([
            f'nCodMovCC com mais de 1 linha (lote?): {len(lotes)} grupos, {sum(len(v) for v in lotes)} linhas',
            f'  todas as linhas do grupo têm nCodBaixa: {sum(1 for v in lotes if all(d.get("nCodBaixa") for d, _ in v))}',
            f'  nValorMovCC igual em todas as linhas do grupo: '
            f'{sum(1 for v in lotes if len({_dec(d.get("nValorMovCC")) for d, _ in v}) == 1)}',
            f'  soma dos nValPago == nValorMovCC: '
            f'{sum(1 for v in lotes if _soma(r.get("nValPago") for _, r in v) == _dec(v[0][0].get("nValorMovCC")))}',
            f'linhas sozinhas no nCodMovCC: {len(simples)} | nValPago == nValorMovCC: '
            f'{sum(1 for d, r in simples if _dec(r.get("nValPago")) == _dec(d.get("nValorMovCC")))}',
        ])

        previsoes = [d for d, _ in linhas if d.get('cGrupo') == 'PREVISAO_CONTRATO']
        receber = [d for d, _ in linhas if d.get('cGrupo') == 'CONTA_A_RECEBER' and d.get('nCodCtr')]
        venc = [_data_omie(d.get('dDtVenc')) for d in previsoes]
        por_mes = {(d['nCodCtr'], _mes(d.get('dDtVenc'))) for d in receber}
        por_parcela = {(d['nCodCtr'], d.get('cNumParcela')) for d in receber}
        self._linhas([
            f'previsões de contrato: {len(previsoes)} | vencimento passado: {sum(1 for v in venc if v and v < self.hoje)} '
            f'| hoje ou futuro: {sum(1 for v in venc if v and v >= self.hoje)}',
            f'  por cStatus: {dict(Counter(_codigo(d.get("cStatus")) for d in previsoes))}',
            f'  contratos distintos: {len({d.get("nCodCtr") for d in previsoes})} '
            f'| contratos que também têm título a receber: '
            f'{len({d.get("nCodCtr") for d in previsoes} & {d["nCodCtr"] for d in receber})}',
            f'  previsões com título a receber do mesmo contrato no mesmo mês de vencimento: '
            f'{sum(1 for d in previsoes if (d.get("nCodCtr"), _mes(d.get("dDtVenc"))) in por_mes)}',
            f'  previsões com título a receber do mesmo contrato e mesma cNumParcela: '
            f'{sum(1 for d in previsoes if (d.get("nCodCtr"), d.get("cNumParcela")) in por_parcela)}',
        ])


    def cadastros(self):
        """Confere, com os dados reais, que a conversão acha os campos dos
        cadastros (os nomes de categoria e cliente vieram só da
        documentação). Mostra estrutura e contagens; nunca nomes."""
        try:
            resposta = self.cliente.chamar(
                'geral/clientes', 'ListarClientes', {'pagina': 1, 'registros_por_pagina': 50}
            )
            clientes = resposta.get('clientes_cadastro') or []
            self._linhas([f'clientes/fornecedores na conta: {resposta.get("total_de_registros")} '
                          f'(amostra de {len(clientes)})'])
            if clientes:
                self._linhas(['estrutura de um cliente/fornecedor:', *estrutura(clientes[0], 1, max_nivel=1)])
        except SemRegistros:
            clientes = []
            self._linhas(['sem clientes/fornecedores'])

        for nome, lista, conversor in (
            ('contas correntes', self.contas_lista, converter_conta_corrente),
            ('categorias', self.categorias_lista, converter_categoria),
            ('clientes/fornecedores (amostra)', clientes, converter_cliente),
        ):
            convertidos, ignorados, preenchidos = 0, Counter(), Counter()
            for item in lista:
                try:
                    campos = conversor(item).campos
                except LinhaIgnorada as erro:
                    ignorados[erro.motivo] += 1
                    continue
                convertidos += 1
                preenchidos.update(k for k, v in campos.items() if v not in (None, '', False))
            self._linhas([
                f'{nome}: {len(lista)} | convertidos: {convertidos} | ignorados: {dict(ignorados)}',
                f'  campos preenchidos: {dict(sorted(preenchidos.items()))}',
            ])

        # nao_fluxo marcado em quase todas as contas da Tática: antes de usar
        # como filtro, quanto do fluxo realizado ele tiraria?
        contas = {}
        for item in self.contas_lista:
            try:
                c = converter_conta_corrente(item)
            except LinhaIgnorada:
                continue
            contas[c.chave['codigo']] = (c.campos['fora_do_fluxo'], c.campos['ativa'])
        if contas and self.movimentos_90:
            por_marca = Counter()
            for m in self.movimentos_90:
                d = m.get('detalhes') or {}
                if d.get('cGrupo') not in ('CONTA_CORRENTE_PAG', 'CONTA_CORRENTE_REC'):
                    continue
                fora, ativa = contas.get(d.get('nCodCC'), (None, None))
                rotulo = 'conta desconhecida' if fora is None else (
                    f'{"fora do fluxo" if fora else "no fluxo"}, {"ativa" if ativa else "inativa"}')
                por_marca[rotulo] += 1
            self._linhas([
                f'contas por marcação: {dict(Counter(f"{"fora" if f else "no fluxo"}/{"ativa" if a else "inativa"}" for f, a in contas.values()))}',
                f'lançamentos de conta corrente (90 dias) por marcação da conta: {dict(por_marca)}',
            ])


def _dec(valor) -> Decimal | None:
    if valor is None or valor == '':
        return None
    try:
        return Decimal(str(valor)).quantize(Decimal('0.01'))
    except InvalidOperation:
        return None


def _soma(valores) -> Decimal:
    return sum((_dec(v) or Decimal('0') for v in valores), Decimal('0'))


def _mes(texto):
    dia = _data_omie(texto)
    return (dia.year, dia.month) if dia else None


def _codigo(valor) -> str:
    """Códigos curtos (ex.: CONTA_A_PAGAR, PAGO) passam; qualquer texto
    comprido vira marcador, para um campo livre nunca vazar na saída."""
    texto = '' if valor is None else str(valor)
    if len(texto) > 25:
        return '<texto longo>'
    return texto
