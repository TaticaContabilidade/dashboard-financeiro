"""Conversão dos cadastros e do saldo (D-38: sem CPF/CNPJ, contato ou dados bancários)."""

from datetime import date
from decimal import Decimal

import pytest

from apps.financeiro.models import Categoria, ClienteFornecedor, ContaCorrente, SaldoConta
from apps.omie.conversao import (
    LinhaIgnorada,
    converter_categoria,
    converter_cliente,
    converter_conta_corrente,
    converter_saldo,
)

CONTA = {
    'nCodCC': 10, 'descricao': 'Banco X', 'tipo_conta_corrente': 'CC', 'codigo_banco': '341', 'inativo': 'N',
    'nao_fluxo': 'S', 'saldo_inicial': 1500.5, 'saldo_data': '01/01/2026',
    'codigo_agencia': '0001', 'numero_conta_corrente': '12345-6', 'telefone': '82999990000',
}
CLIENTE = {
    'codigo_cliente_omie': 77, 'razao_social': 'Fornecedor LTDA', 'nome_fantasia': 'Forn', 'inativo': 'S',
    'cnpj_cpf': '12.345.678/0001-99', 'email': 'a@b.c', 'endereco': 'Rua 1', 'telefone1_numero': '999',
}


def test_conta_corrente():
    c = converter_conta_corrente(CONTA)
    assert c.modelo is ContaCorrente
    assert c.chave == {'codigo': 10}
    assert c.campos['fora_do_fluxo'] is True
    assert c.campos['ativa'] is True
    assert c.campos['saldo_inicial'] == Decimal('1500.50')
    assert c.campos['data_saldo_inicial'] == date(2026, 1, 1)


def test_categoria():
    c = converter_categoria({'codigo': '2.01.03', 'descricao': 'Aluguel', 'categoria_superior': '2.01',
                             'conta_receita': 'N', 'conta_despesa': 'S'})
    assert c.modelo is Categoria
    assert c.chave == {'codigo': '2.01.03'}
    assert c.campos == {'descricao': 'Aluguel', 'codigo_superior': '2.01', 'receita': False, 'despesa': True}


def test_cliente():
    c = converter_cliente(CLIENTE)
    assert c.modelo is ClienteFornecedor
    assert c.chave == {'codigo': 77}
    assert c.campos == {'razao_social': 'Fornecedor LTDA', 'nome_fantasia': 'Forn', 'ativo': False}


@pytest.mark.parametrize('conversor, item', [(converter_conta_corrente, CONTA), (converter_cliente, CLIENTE)])
def test_dados_pessoais_e_bancarios_sao_descartados(conversor, item):
    """D-38: só o necessário para mostrar nomes no dashboard."""
    c = conversor(item)
    guardado = repr(c.chave) + repr(c.campos)
    for valor in ('12.345.678/0001-99', 'a@b.c', 'Rua 1', '0001', '12345-6', '82999990000'):
        assert valor not in guardado, valor


def test_saldo_usa_conta_e_dia_consultados():
    resposta = {'nCodCC': 999, 'nSaldoAtual': 100.1, 'nSaldoDisponivel': 50, 'nLimiteCreditoTotal': None}
    c = converter_saldo(resposta, 10, date(2026, 11, 18))
    assert c.modelo is SaldoConta
    assert c.chave == {'codigo_conta_corrente': 10, 'data': date(2026, 11, 18)}
    assert c.campos['saldo_atual'] == Decimal('100.10')
    assert c.campos['saldo_disponivel'] == Decimal('50.00')
    assert c.campos['limite_credito'] is None


@pytest.mark.parametrize('conversor, item, motivo', [
    (converter_conta_corrente, {'descricao': 'x'}, 'sem nCodCC'),
    (converter_categoria, {'descricao': 'x'}, 'sem codigo'),
    (converter_cliente, {'razao_social': 'x'}, 'sem codigo_cliente_omie'),
])
def test_cadastro_sem_codigo_e_ignorado(conversor, item, motivo):
    with pytest.raises(LinhaIgnorada, match=motivo):
        conversor(item)


def test_saldo_sem_conta_e_ignorado():
    with pytest.raises(LinhaIgnorada):
        converter_saldo({}, 0, date(2026, 11, 18))
