"""Conversão do ListarMovimentos para as tabelas (D-36). Sem banco."""

from datetime import date
from decimal import Decimal

import pytest

from apps.financeiro.models import LancamentoConta, Natureza, Titulo
from apps.omie.conversao import LinhaIgnorada, converter_movimento


def mov(detalhes: dict, resumo: dict | None = None) -> dict:
    return {'detalhes': detalhes, 'resumo': resumo or {}}


@pytest.mark.parametrize('grupo, tipo, natureza', [
    ('CONTA_A_PAGAR', Titulo.Tipo.PAGAR, Natureza.PAGAR),
    ('CONTA_A_RECEBER', Titulo.Tipo.RECEBER, Natureza.RECEBER),
    ('PREVISAO_CONTRATO', Titulo.Tipo.PREVISAO, Natureza.RECEBER),
])
def test_titulos_e_previsoes_viram_titulo(grupo, tipo, natureza):
    c = converter_movimento(mov(
        {'cGrupo': grupo, 'nCodTitulo': 10, 'cStatus': 'A VENCER', 'dDtVenc': '05/02/2026',
         'nValorTitulo': 1500, 'nCodCtr': 7, 'cNumParcela': '001/012'},
        {'nValAberto': 1500.5, 'cLiquidado': 'N'},
    ))
    assert c.modelo is Titulo
    assert c.chave == {'tipo': tipo, 'codigo_titulo': 10}
    assert c.campos['natureza'] == natureza
    assert c.campos['data_vencimento'] == date(2026, 2, 5)
    assert c.campos['valor_titulo'] == Decimal('1500.00')
    assert c.campos['valor_aberto'] == Decimal('1500.50')
    assert c.campos['codigo_contrato'] == 7
    assert c.campos['liquidado'] is False


def test_conta_corrente_vira_lancamento_com_chave_movimento_e_baixa():
    c = converter_movimento(mov(
        {'cGrupo': 'CONTA_CORRENTE_PAG', 'nCodMovCC': 900, 'nCodBaixa': 55, 'nCodTitulo': 10,
         'nValorMovCC': 3000, 'dDtPagamento': '10/02/2026', 'cOrigem': 'BAXP'},
        {'nValPago': 1000},
    ))
    assert c.modelo is LancamentoConta
    assert c.chave == {'natureza': Natureza.PAGAR, 'codigo_movimento': 900, 'codigo_baixa': 55}
    assert c.campos['valor'] == Decimal('1000.00')            # o que o realizado soma
    assert c.campos['valor_movimento'] == Decimal('3000.00')  # total do lote, nunca somar
    assert c.campos['codigo_titulo'] == 10


def test_baixas_do_mesmo_lote_tem_chaves_diferentes():
    base = {'cGrupo': 'CONTA_CORRENTE_PAG', 'nCodMovCC': 900, 'nValorMovCC': 3000}
    chaves = {
        tuple(converter_movimento(mov({**base, 'nCodBaixa': b}, {'nValPago': 1000})).chave.values())
        for b in (1, 2, 3)
    }
    assert len(chaves) == 3


def test_lancamento_direto_sem_baixa_e_baixa_sem_movimento():
    direto = converter_movimento(mov({'cGrupo': 'CONTA_CORRENTE_REC', 'nCodMovCC': 901, 'cOrigem': 'EXTR'}))
    assert direto.chave == {'natureza': Natureza.RECEBER, 'codigo_movimento': 901, 'codigo_baixa': 0}
    # as 5 linhas do teste real: baixa sem movimento bancário e sem valor
    sem_mov = converter_movimento(mov({'cGrupo': 'CONTA_CORRENTE_PAG', 'nCodBaixa': 77}))
    assert sem_mov.chave == {'natureza': Natureza.PAGAR, 'codigo_movimento': 0, 'codigo_baixa': 77}
    assert sem_mov.campos['valor'] is None


@pytest.mark.parametrize('bruto, esperado', [
    (0.1 + 0.2, Decimal('0.30')),   # erro binário do float some
    (1234.565, Decimal('1234.57')),
    (100, Decimal('100.00')),
    (0, Decimal('0.00')),           # zero é zero, não nulo
    (None, None),
    ('', None),
])
def test_valores_viram_decimal_com_2_casas(bruto, esperado):
    c = converter_movimento(mov({'cGrupo': 'CONTA_A_PAGAR', 'nCodTitulo': 1, 'nValorTitulo': bruto}))
    assert c.campos['valor_titulo'] == esperado


def test_juros_do_resumo_com_reserva_nos_detalhes():
    c = converter_movimento(mov(
        {'cGrupo': 'CONTA_A_RECEBER', 'nCodTitulo': 1, 'nJuros': 5, 'nMulta': 2},
        {'nJuros': None, 'nMulta': 3},
    ))
    assert c.campos['juros'] == Decimal('5.00')
    assert c.campos['multa'] == Decimal('3.00')


@pytest.mark.parametrize('detalhes, motivo', [
    ({'cGrupo': 'GRUPO_NOVO_DO_OMIE', 'nCodTitulo': 1}, 'cGrupo desconhecido'),
    ({'nCodTitulo': 1}, 'cGrupo desconhecido'),
    ({'cGrupo': 'CONTA_A_PAGAR'}, 'sem nCodTitulo'),
    ({'cGrupo': 'CONTA_A_PAGAR', 'nCodTitulo': 0}, 'sem nCodTitulo'),
    ({'cGrupo': 'CONTA_CORRENTE_PAG'}, 'sem nCodMovCC nem nCodBaixa'),
    ({'cGrupo': 'CONTA_A_PAGAR', 'nCodTitulo': 1, 'dDtVenc': '2026-02-05'}, 'data em formato inválido'),
    ({'cGrupo': 'CONTA_A_PAGAR', 'nCodTitulo': 1, 'dDtVenc': '31/02/2026'}, 'data em formato inválido'),
    ({'cGrupo': 'CONTA_A_PAGAR', 'nCodTitulo': 'abc'}, 'código em formato inválido'),
    ({'cGrupo': 'CONTA_A_PAGAR', 'nCodTitulo': 1, 'nValorTitulo': 'R$ 10'}, 'valor em formato inválido'),
])
def test_linha_ignorada_com_motivo(detalhes, motivo):
    with pytest.raises(LinhaIgnorada, match=motivo):
        converter_movimento(mov(detalhes))


def test_motivo_nunca_carrega_valor_do_omie():
    with pytest.raises(LinhaIgnorada) as erro:
        converter_movimento(mov({'cGrupo': 'CONTA_A_PAGAR', 'nCodTitulo': 1, 'nValorTitulo': '987654,32-SENTINELA'}))
    assert 'SENTINELA' not in erro.value.motivo
    assert '987654' not in str(erro.value)


def test_textos_sao_cortados_no_tamanho_da_coluna():
    c = converter_movimento(mov({'cGrupo': 'CONTA_A_PAGAR', 'nCodTitulo': 1, 'cStatus': 'X' * 100}))
    assert len(c.campos['status']) == 30
