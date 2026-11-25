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

import re
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
    LancamentoConta,
    Natureza,
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


CODIGO_DE_CATEGORIA = re.compile(r'^\d+(\.\d+)*$')


def _filtro_de_categoria(params) -> Q | None:
    """`?categoria=2.03` ou `?categoria=2.03,1.01.02` (D-41). Um grupo traz
    as subcategorias: compara pelo código exato ou pelo código seguido de
    ponto, para `1.01` não pegar `1.010`."""
    bruto = params.get('categoria')
    if not bruto:
        return None
    codigos = [c.strip() for c in bruto.split(',') if c.strip()]
    if not codigos or not all(CODIGO_DE_CATEGORIA.match(c) for c in codigos):
        raise ValidationError({'categoria': 'Use códigos de categoria do Omie separados por vírgula (ex.: 2.03,1.01.02).'})
    filtro = Q()
    for codigo in codigos:
        filtro |= Q(codigo_categoria=codigo) | Q(codigo_categoria__startswith=f'{codigo}.')
    return filtro


# --- consultas-base do fluxo (D-36, D-40) -------------------------------------
# Usadas pelo fluxo de caixa e pelo fluxo por categoria, para os dois
# seguirem exatamente as mesmas regras.


def _lancamentos_realizados(empresa_id: int, data_de: date, data_ate: date, categoria: Q | None):
    """Linhas de conta corrente pagas no período (o realizado)."""
    qs = LancamentoConta.objects.da_empresa(empresa_id).filter(
        data_pagamento__gte=data_de, data_pagamento__lte=data_ate,
    )
    return qs.filter(categoria) if categoria else qs


def _titulos_em_aberto(empresa_id: int, data_de: date, data_ate: date, categoria: Q | None):
    """Títulos não liquidados com vencimento no período, anotados com o que
    falta pagar/receber (`em_aberto`)."""
    qs = (
        Titulo.objects.da_empresa(empresa_id)
        .sem_cancelados()
        .filter(
            tipo__in=[Titulo.Tipo.PAGAR, Titulo.Tipo.RECEBER],
            liquidado=False,
            data_vencimento__gte=data_de,
            data_vencimento__lte=data_ate,
        )
        .annotate(em_aberto=VALOR_EM_ABERTO)
    )
    return qs.filter(categoria) if categoria else qs


def _previsoes(empresa_id: int, data_de: date, data_ate: date, categoria: Q | None):
    """Previsões de contrato ainda válidas com vencimento no período."""
    qs = (
        Titulo.objects.da_empresa(empresa_id)
        .sem_cancelados()
        .previsoes_validas()
        .filter(data_vencimento__gte=data_de, data_vencimento__lte=data_ate)
    )
    return qs.filter(categoria) if categoria else qs


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

        categoria = _filtro_de_categoria(params)
        if categoria:
            qs = qs.filter(categoria)

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

    Parâmetros: `data_de`, `data_ate` (padrão: o mês atual),
    `granularidade` (`dia`, `semana` ou `mes`; padrão `mes`) e `categoria`.
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

        categoria = _filtro_de_categoria(params)

        realizado = (
            _lancamentos_realizados(empresa_id, data_de, data_ate, categoria)
            .annotate(periodo=truncar('data_pagamento'))
            .values('periodo')
            .annotate(**_entradas_e_saidas('valor'))
        )
        previsto_titulos = (
            _titulos_em_aberto(empresa_id, data_de, data_ate, categoria)
            .annotate(periodo=truncar('data_vencimento'))
            .values('periodo')
            .annotate(**_entradas_e_saidas('em_aberto'))
        )
        previsto_previsoes = (
            _previsoes(empresa_id, data_de, data_ate, categoria)
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
