"""
Views da API do dashboard financeiro (RF-01 a RF-04; wiki: API do Dashboard).

Toda view sobre dados financeiros usa `EscopoEmpresaMixin`, para cada
empresa ver só os próprios dados (D-32). Regras de cálculo (D-36, D-40):

- títulos cancelados no Omie ficam fora de tudo;
- o que falta pagar/receber é `valor_aberto` (descontada a baixa parcial),
  nunca `valor_titulo`, para a baixa parcial não contar duas vezes;
- o fluxo realizado soma `LancamentoConta.valor` (o `nValPago` da linha),
  nunca `valor_movimento`.

Valores em dinheiro saem sempre como texto com 2 casas ("1234.50"), igual
aos campos do serializer: nunca float.
"""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Case, CharField, Count, F, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce, TruncDay, TruncMonth, TruncWeek
from django.utils import timezone
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.acesso.escopo import EscopoEmpresaMixin
from apps.financeiro.models import (
    Categoria,
    ClienteFornecedor,
    ContaCorrente,
    LancamentoConta,
    Natureza,
    SaldoConta,
    Titulo,
)
from apps.financeiro.serializers import TituloSerializer
from apps.omie.models import ExecucaoSincronizacao

ZERO = Decimal('0')

# O que ainda falta pagar/receber. O Omie manda `nValAberto` nos títulos;
# se vier vazio, vale o valor do título inteiro.
VALOR_EM_ABERTO = Coalesce(F('valor_aberto'), F('valor_titulo'))

SITUACOES = ('a_vencer', 'vencido', 'pago')


# --- utilidades -----------------------------------------------------------------


class PaginacaoPadrao(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


def _dinheiro(valor: Decimal | None) -> str:
    return f'{(valor or ZERO):.2f}'


def _data_do_parametro(params, nome: str) -> date | None:
    """`AAAA-MM-DD` → date. Data inválida é 400, nunca ignorada em silêncio
    (o React descobre o erro na hora em vez de mostrar outro período)."""
    valor = params.get(nome)
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise ValidationError({nome: 'Data inválida; use o formato AAAA-MM-DD.'})


def _periodo_do_parametro(params, padrao_de=None, padrao_ate=None) -> tuple[date | None, date | None]:
    data_de = _data_do_parametro(params, 'data_de') or padrao_de
    data_ate = _data_do_parametro(params, 'data_ate') or padrao_ate
    if data_de and data_ate and data_de > data_ate:
        raise ValidationError({'data_ate': 'A data final é anterior à inicial.'})
    return data_de, data_ate


# --- contas a pagar / a receber (RF-01, RF-02) ---------------------------------


class _BaseTitulosView(EscopoEmpresaMixin, generics.ListAPIView):
    """Base de contas a pagar e a receber.

    Situação derivada (pago > vencido > a_vencer), nome do cliente/
    fornecedor e descrição da categoria resolvidos, e um `resumo` com
    quantidade e total por situação, somado sobre TODOS os títulos que
    passam pelos filtros (não só a página).

    Total do resumo: `pago` soma o que foi pago; `vencido` e `a_vencer`
    somam o que ainda está em aberto.
    """

    queryset = Titulo.objects.all()
    serializer_class = TituloSerializer
    pagination_class = PaginacaoPadrao
    tipo_titulo: str  # definido nas subclasses

    def get_queryset(self):
        hoje = timezone.localdate()
        qs = super().get_queryset().sem_cancelados().filter(tipo=self.tipo_titulo)

        qs = qs.annotate(
            situacao=Case(
                When(liquidado=True, then=Value('pago')),
                When(data_vencimento__lt=hoje, then=Value('vencido')),
                default=Value('a_vencer'),
                output_field=CharField(),
            ),
            nome_cliente_fornecedor=Subquery(
                ClienteFornecedor.objects.filter(
                    empresa_id=OuterRef('empresa_id'),
                    codigo=OuterRef('codigo_cliente'),
                ).values('razao_social')[:1]
            ),
            descricao_categoria=Subquery(
                Categoria.objects.filter(
                    empresa_id=OuterRef('empresa_id'),
                    codigo=OuterRef('codigo_categoria'),
                ).values('descricao')[:1]
            ),
        )

        params = self.request.query_params
        data_de, data_ate = _periodo_do_parametro(params)
        if data_de:
            qs = qs.filter(data_vencimento__gte=data_de)
        if data_ate:
            qs = qs.filter(data_vencimento__lte=data_ate)

        situacao = params.get('situacao')
        if situacao:
            if situacao not in SITUACOES:
                raise ValidationError({'situacao': f'Use uma de: {", ".join(SITUACOES)}.'})
            qs = qs.filter(situacao=situacao)

        return qs.order_by('data_vencimento', 'codigo_titulo')

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        # `.order_by()` vazio é obrigatório: o Django põe os campos da
        # ordenação no GROUP BY, e o resumo viraria um grupo por título.
        linhas = queryset.order_by().values('situacao').annotate(
            quantidade=Count('id'),
            total=Coalesce(
                Sum(Case(When(situacao='pago', then=F('valor_pago')), default=VALOR_EM_ABERTO)),
                ZERO,
            ),
        )
        resumo = {sit: {'quantidade': 0, 'total': _dinheiro(ZERO)} for sit in SITUACOES}
        for linha in linhas:
            resumo[linha['situacao']] = {
                'quantidade': linha['quantidade'],
                'total': _dinheiro(linha['total']),
            }

        pagina = self.paginate_queryset(queryset)
        resposta = self.get_paginated_response(self.get_serializer(pagina, many=True).data)
        resposta.data['resumo'] = resumo
        return resposta


class ContasAPagarView(_BaseTitulosView):
    """GET /api/financeiro/contas-a-pagar/ (RF-01)"""

    tipo_titulo = Titulo.Tipo.PAGAR


class ContasAReceberView(_BaseTitulosView):
    """GET /api/financeiro/contas-a-receber/ (RF-02)"""

    tipo_titulo = Titulo.Tipo.RECEBER


# --- fluxo de caixa (RF-03) ------------------------------------------------------


TRUNCAR = {
    'dia': TruncDay,
    'semana': TruncWeek,
    'mes': TruncMonth,
}


def _fim_periodo(inicio: date, granularidade: str) -> date:
    """Último dia do período que começa em `inicio` (já truncado)."""
    if granularidade == 'dia':
        return inicio
    if granularidade == 'semana':
        return inicio + timedelta(days=6)
    _, ultimo_dia = monthrange(inicio.year, inicio.month)
    return inicio.replace(day=ultimo_dia)


def _entradas_e_saidas(campo):
    return {
        'entradas': Coalesce(Sum(campo, filter=Q(natureza=Natureza.RECEBER)), ZERO),
        'saidas': Coalesce(Sum(campo, filter=Q(natureza=Natureza.PAGAR)), ZERO),
    }


class FluxoDeCaixaView(EscopoEmpresaMixin, APIView):
    """GET /api/financeiro/fluxo-de-caixa/ (RF-03)

    - Realizado: o que passou na conta corrente, por data de pagamento.
    - Previsto: o que falta pagar/receber dos títulos em aberto, por
      vencimento, mais as previsões de contrato ainda válidas (D-36).

    Parâmetros: `data_de`, `data_ate` (padrão: o mês atual) e
    `granularidade` (`dia`, `semana` ou `mes`; padrão `mes`).
    """

    def get(self, request: Request):
        empresa_id = self.empresa_id()
        hoje = timezone.localdate()
        params = request.query_params

        granularidade = params.get('granularidade') or 'mes'
        if granularidade not in TRUNCAR:
            raise ValidationError({'granularidade': f'Use uma de: {", ".join(TRUNCAR)}.'})
        truncar = TRUNCAR[granularidade]

        data_de, data_ate = _periodo_do_parametro(
            params, padrao_de=hoje.replace(day=1), padrao_ate=_fim_periodo(hoje, 'mes'),
        )

        realizado = (
            LancamentoConta.objects.da_empresa(empresa_id)
            .filter(data_pagamento__gte=data_de, data_pagamento__lte=data_ate)
            .annotate(periodo=truncar('data_pagamento'))
            .values('periodo')
            .annotate(**_entradas_e_saidas('valor'))
        )

        previsto_titulos = (
            Titulo.objects.da_empresa(empresa_id)
            .sem_cancelados()
            .filter(
                tipo__in=[Titulo.Tipo.PAGAR, Titulo.Tipo.RECEBER],
                liquidado=False,
                data_vencimento__gte=data_de,
                data_vencimento__lte=data_ate,
            )
            .annotate(periodo=truncar('data_vencimento'), em_aberto=VALOR_EM_ABERTO)
            .values('periodo')
            .annotate(**_entradas_e_saidas('em_aberto'))
        )

        previsto_previsoes = (
            Titulo.objects.da_empresa(empresa_id)
            .sem_cancelados()
            .previsoes_validas()
            .filter(data_vencimento__gte=data_de, data_vencimento__lte=data_ate)
            .annotate(periodo=truncar('data_vencimento'))
            .values('periodo')
            .annotate(**_entradas_e_saidas('valor_titulo'))
        )

        periodos = _montar_periodos(
            realizado=realizado,
            previstos=[previsto_titulos, previsto_previsoes],
            granularidade=granularidade,
        )

        totais = {'realizado': _vazio(), 'previsto': _vazio()}
        for item in periodos.values():
            for lado in totais:
                totais[lado]['entradas'] += item[lado]['entradas']
                totais[lado]['saidas'] += item[lado]['saidas']

        return Response({
            'data_de': data_de,
            'data_ate': data_ate,
            'granularidade': granularidade,
            'periodos': [
                {'inicio': inicio, 'fim': _fim_periodo(inicio, granularidade), **_formatar(item)}
                for inicio, item in sorted(periodos.items())
            ],
            'totais': _formatar(totais),
        })


def _vazio():
    return {'entradas': ZERO, 'saidas': ZERO}


def _montar_periodos(*, realizado, previstos, granularidade) -> dict:
    """Junta as consultas num dicionário {início do período: valores}."""
    dados: dict[date, dict] = {}

    def somar(linhas, lado):
        for linha in linhas:
            item = dados.setdefault(_como_data(linha['periodo']), {'realizado': _vazio(), 'previsto': _vazio()})
            item[lado]['entradas'] += linha['entradas']
            item[lado]['saidas'] += linha['saidas']

    somar(realizado, 'realizado')
    for consulta in previstos:
        somar(consulta, 'previsto')
    return dados


def _formatar(item: dict) -> dict:
    """Valores em texto e o saldo (entradas − saídas) de cada lado."""
    return {
        lado: {
            'entradas': _dinheiro(valores['entradas']),
            'saidas': _dinheiro(valores['saidas']),
            'saldo': _dinheiro(valores['entradas'] - valores['saidas']),
        }
        for lado, valores in item.items()
    }


def _como_data(valor) -> date:
    """Trunc* sobre DateField devolve date, mas sobre DateTimeField devolve
    datetime. Normaliza para date."""
    return valor.date() if hasattr(valor, 'date') else valor


# --- saldo por conta corrente (RF-03) -------------------------------------------


CAMPOS_DE_SALDO = ('saldo_atual', 'saldo_conciliado', 'saldo_disponivel', 'saldo_atual_previsto', 'limite_credito')


class SaldosView(EscopoEmpresaMixin, APIView):
    """GET /api/financeiro/saldos/ (RF-03)

    O saldo de cada conta corrente numa data (`?data=AAAA-MM-DD`; padrão:
    hoje), vindo do `ListarExtrato` do Omie. A sincronização grava o saldo
    do dia das contas ativas; para cada conta vale o último saldo gravado
    até a data pedida, e `data_saldo` diz de quando ele é.

    Lista as contas ativas e as inativas que tenham saldo até a data. Os
    totais vêm em dois cortes, porque a regra das contas fora do fluxo
    ainda não foi decidida (backlog 4.1b): todas as contas e só as que
    entram no fluxo.
    """

    def get(self, request: Request):
        empresa_id = self.empresa_id()
        data = _data_do_parametro(request.query_params, 'data') or timezone.localdate()

        # Postgres DISTINCT ON: a linha mais recente de cada conta até a data.
        ultimos = {
            saldo.codigo_conta_corrente: saldo
            for saldo in (
                SaldoConta.objects.da_empresa(empresa_id)
                .filter(data__lte=data)
                .order_by('codigo_conta_corrente', '-data')
                .distinct('codigo_conta_corrente')
            )
        }
        contas = {
            conta.codigo: conta
            for conta in ContaCorrente.objects.da_empresa(empresa_id).filter(
                Q(ativa=True) | Q(codigo__in=ultimos.keys())
            )
        }

        linhas = []
        totais = {'todas': ZERO, 'no_fluxo': ZERO}
        for codigo in contas.keys() | ultimos.keys():
            conta, saldo = contas.get(codigo), ultimos.get(codigo)
            fora_do_fluxo = conta.fora_do_fluxo if conta else False
            atual = saldo.saldo_atual if saldo else None
            if atual is not None:
                totais['todas'] += atual
                if not fora_do_fluxo:
                    totais['no_fluxo'] += atual
            linhas.append({
                'codigo': codigo,
                'descricao': conta.descricao if conta else '',
                'tipo': conta.tipo if conta else '',
                'ativa': conta.ativa if conta else False,
                'fora_do_fluxo': fora_do_fluxo,
                'data_saldo': saldo.data if saldo else None,
                **{
                    campo: _dinheiro(getattr(saldo, campo)) if saldo and getattr(saldo, campo) is not None else None
                    for campo in CAMPOS_DE_SALDO
                },
            })

        linhas.sort(key=lambda linha: (not linha['ativa'], linha['descricao'].lower(), linha['codigo']))
        return Response({
            'data': data,
            'contas': linhas,
            'totais': {
                'saldo_atual': _dinheiro(totais['todas']),
                'saldo_atual_no_fluxo': _dinheiro(totais['no_fluxo']),
            },
        })


# --- última atualização (RF-04) --------------------------------------------------


class UltimaAtualizacaoView(EscopoEmpresaMixin, APIView):
    """GET /api/financeiro/ultima-atualizacao/ (RF-04)"""

    def get(self, request: Request):
        execucao = (
            ExecucaoSincronizacao.objects.da_empresa(self.empresa_id())
            .ultima_bem_sucedida()
        )
        if execucao is None or execucao.finalizada_em is None:
            return Response({'ultima_sincronizacao': None, 'atualizado_ha': None})

        return Response({
            'ultima_sincronizacao': execucao.finalizada_em,
            'atualizado_ha': _tempo_relativo(execucao.finalizada_em),
        })


def _tempo_relativo(momento) -> str:
    """'agora', 'há 5 min', 'há 2 horas', 'há 3 dias' (RF-04)."""
    minutos = int((timezone.now() - momento).total_seconds()) // 60
    if minutos < 1:
        return 'agora'
    if minutos < 60:
        return f'há {minutos} min'
    horas = minutos // 60
    if horas < 24:
        return 'há 1 hora' if horas == 1 else f'há {horas} horas'
    dias = horas // 24
    return 'há 1 dia' if dias == 1 else f'há {dias} dias'
