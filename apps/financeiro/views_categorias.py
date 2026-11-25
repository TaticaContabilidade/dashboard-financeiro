"""
Categorias e fluxo por categoria (RF-03; D-41, D-42; wiki: API do Dashboard).

O código de categoria do Omie tem estrutura fixa: `1.xx` receitas, `2.xx`
despesas e `0.xx` transferências; o 2º nível (`2.03`) é o grupo e o 3º
(`2.03.01`) a subcategoria, onde os lançamentos ficam. A numeração que o
usuário vê no Omie ("3.1 - Despesas com Pessoal") é o plano de contas
escrito na descrição, mostrada como está.

Limitação aceita (D-42): o rateio entre categorias não é sincronizado. Um
título rateado entra inteiro na categoria que o Omie põe no movimento.
"""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.acesso.escopo import EscopoEmpresaMixin
from apps.financeiro.models import Categoria
from apps.financeiro.views import (
    ZERO,
    _dinheiro,
    _entradas_e_saidas,
    _fim_periodo,
    _lancamentos_realizados,
    _periodo_do_parametro,
    _previsoes,
    _titulos_em_aberto,
)

RAIZ_DO_TIPO = {'receitas': '1', 'despesas': '2'}
TIPO_DA_RAIZ = {'0': 'transferencia', '1': 'receita', '2': 'despesa'}
BASES = ('realizado', 'previsto')
SEM_CADASTRO = 'Categoria não cadastrada'


def _grupo(codigo: str) -> str:
    """'2.03.01' -> '2.03'."""
    return '.'.join(codigo.split('.')[:2])


# --- árvore de categorias ---------------------------------------------------------


class CategoriasView(EscopoEmpresaMixin, APIView):
    """GET /api/financeiro/categorias/

    Árvore das categorias da empresa, para o seletor do filtro
    `?categoria=`. Cada nó tem as filhas em `subcategorias`.
    """

    def get(self, request: Request):
        categorias = list(
            Categoria.objects.da_empresa(self.empresa_id())
            .order_by('codigo')
            .values('codigo', 'descricao', 'codigo_superior')
        )
        nos = {
            c['codigo']: {
                'codigo': c['codigo'],
                'descricao': c['descricao'].strip(),
                'tipo': TIPO_DA_RAIZ.get(c['codigo'].split('.')[0], ''),
                'subcategorias': [],
            }
            for c in categorias
        }
        raizes = []
        for c in categorias:
            pai = nos.get(c['codigo_superior'])
            (pai['subcategorias'] if pai else raizes).append(nos[c['codigo']])
        return Response({'categorias': raizes})


# --- fluxo por categoria ----------------------------------------------------------


class FluxoPorCategoriaView(EscopoEmpresaMixin, APIView):
    """GET /api/financeiro/fluxo-de-caixa/por-categoria/

    Para o gráfico de gastos/receitas por categoria. Parâmetros:
    `data_de`, `data_ate` (padrão: o mês atual), `tipo` (`despesas` ou
    `receitas`; padrão `despesas`) e `base` (`realizado` ou `previsto`;
    padrão `realizado`).

    Mesmas regras do fluxo de caixa (D-36, D-40). Valor líquido: numa
    categoria de despesa, o dinheiro que voltou (estorno) é descontado.
    Transferências (`0.xx`) ficam fora. Grupos e subcategorias do maior
    para o menor, com o percentual sobre o total.
    """

    def get(self, request: Request):
        empresa_id = self.empresa_id()
        hoje = timezone.localdate()
        params = request.query_params

        tipo = params.get('tipo') or 'despesas'
        if tipo not in RAIZ_DO_TIPO:
            raise ValidationError({'tipo': f'Use uma de: {", ".join(RAIZ_DO_TIPO)}.'})
        base = params.get('base') or 'realizado'
        if base not in BASES:
            raise ValidationError({'base': f'Use uma de: {", ".join(BASES)}.'})
        data_de, data_ate = _periodo_do_parametro(
            params, padrao_de=hoje.replace(day=1), padrao_ate=_fim_periodo(hoje, 'mes'),
        )

        raiz = RAIZ_DO_TIPO[tipo]
        do_tipo = Q(codigo_categoria__startswith=f'{raiz}.')
        if base == 'realizado':
            consultas = [(_lancamentos_realizados(empresa_id, data_de, data_ate, do_tipo), 'valor')]
        else:
            consultas = [
                (_titulos_em_aberto(empresa_id, data_de, data_ate, do_tipo), 'em_aberto'),
                (_previsoes(empresa_id, data_de, data_ate, do_tipo), 'valor_titulo'),
            ]

        # Receita soma entradas; despesa soma saídas. O lado contrário
        # (estorno) é descontado.
        por_categoria: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for consulta, campo in consultas:
            linhas = (
                consulta.order_by()
                .values('codigo_categoria')
                .annotate(**_entradas_e_saidas(campo))
            )
            for linha in linhas:
                liquido = (linha['entradas'] - linha['saidas']) if raiz == '1' else (linha['saidas'] - linha['entradas'])
                por_categoria[linha['codigo_categoria']] += liquido

        descricoes = dict(
            Categoria.objects.da_empresa(empresa_id)
            .filter(Q(codigo__startswith=f'{raiz}.'))
            .values_list('codigo', 'descricao')
        )
        grupos: dict[str, dict] = {}
        for codigo, valor in por_categoria.items():
            if not valor:
                continue
            grupo = grupos.setdefault(_grupo(codigo), {'total': ZERO, 'subcategorias': []})
            grupo['total'] += valor
            grupo['subcategorias'].append((codigo, valor))

        total = sum((g['total'] for g in grupos.values()), ZERO)

        def no(codigo, valor):
            descricao = descricoes.get(codigo)
            return {
                'codigo': codigo,
                'descricao': descricao.strip() if descricao else SEM_CADASTRO,
                'total': _dinheiro(valor),
                'percentual': _dinheiro(valor / total * 100) if total else _dinheiro(ZERO),
            }

        categorias = []
        for codigo, grupo in sorted(grupos.items(), key=lambda item: (-item[1]['total'], item[0])):
            subcategorias = sorted(grupo['subcategorias'], key=lambda item: (-item[1], item[0]))
            categorias.append({
                **no(codigo, grupo['total']),
                'subcategorias': [no(c, v) for c, v in subcategorias],
            })

        return Response({
            'data_de': data_de,
            'data_ate': data_ate,
            'tipo': tipo,
            'base': base,
            'total': _dinheiro(total),
            'categorias': categorias,
        })
