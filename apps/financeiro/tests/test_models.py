"""Tabelas de títulos e lançamentos: chaves por empresa e regra da previsão (D-36)."""

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.financeiro.models import LancamentoConta, Natureza, Titulo
from apps.omie.conversao import converter_movimento

pytestmark = pytest.mark.django_db

TATICA, CLIENTE = 1, 2


def titulo(empresa=TATICA, tipo=Titulo.Tipo.RECEBER, codigo=1, **campos):
    natureza = Natureza.PAGAR if tipo == Titulo.Tipo.PAGAR else Natureza.RECEBER
    return Titulo.objects.create(empresa_id=empresa, tipo=tipo, natureza=natureza, codigo_titulo=codigo, **campos)


def lancamento(empresa=TATICA, movimento=900, baixa=0, **campos):
    return LancamentoConta.objects.create(
        empresa_id=empresa, natureza=Natureza.PAGAR, codigo_movimento=movimento, codigo_baixa=baixa, **campos
    )


def violou(fabrica, **kwargs) -> bool:
    try:
        with transaction.atomic():
            fabrica(**kwargs)
    except IntegrityError:
        return True
    return False


# --- chaves únicas ------------------------------------------------------------

def test_titulo_unico_por_empresa_tipo_e_codigo():
    titulo(codigo=10)
    assert violou(titulo, codigo=10)                           # mesma empresa: duplicado
    assert not violou(titulo, codigo=10, empresa=CLIENTE)      # outra empresa: pode
    assert not violou(titulo, codigo=10, tipo=Titulo.Tipo.PREVISAO)  # outro tipo: pode


def test_titulo_exige_codigo():
    assert violou(titulo, codigo=0)


def test_lancamento_unico_por_movimento_e_baixa():
    lancamento(movimento=900, baixa=1)
    assert violou(lancamento, movimento=900, baixa=1)
    assert not violou(lancamento, movimento=900, baixa=2)      # baixa em lote: mesmo movimento
    assert not violou(lancamento, movimento=900, baixa=1, empresa=CLIENTE)


def test_lancamento_exige_movimento_ou_baixa():
    assert violou(lancamento, movimento=0, baixa=0)
    assert not violou(lancamento, movimento=0, baixa=77)       # baixa sem movimento bancário


def test_conversao_grava_por_upsert_sem_duplicar():
    movimento = {'detalhes': {'cGrupo': 'CONTA_A_PAGAR', 'nCodTitulo': 5, 'cStatus': 'A VENCER',
                              'nValorTitulo': 100}, 'resumo': {}}
    for status in ('A VENCER', 'PAGO'):
        movimento['detalhes']['cStatus'] = status
        c = converter_movimento(movimento)
        c.modelo.objects.update_or_create(empresa_id=TATICA, **c.chave, defaults=c.campos)
    assert Titulo.objects.da_empresa(TATICA).get().status == 'PAGO'


def test_da_empresa_nao_mistura_empresas():
    titulo(codigo=1)
    titulo(codigo=1, empresa=CLIENTE)
    lancamento()
    assert Titulo.objects.da_empresa(TATICA).count() == 1
    assert LancamentoConta.objects.da_empresa(CLIENTE).count() == 0
    with pytest.raises(ValueError):
        Titulo.objects.da_empresa(None)


# --- previsão velha (D-36) ----------------------------------------------------

def previsao(codigo, contrato=7, vencimento=date(2026, 3, 10), empresa=TATICA):
    return titulo(empresa=empresa, tipo=Titulo.Tipo.PREVISAO, codigo=codigo,
                  codigo_contrato=contrato, data_vencimento=vencimento, valor_titulo=Decimal('500'))


def validas(empresa=TATICA):
    return set(Titulo.objects.da_empresa(empresa).previsoes_validas().values_list('codigo_titulo', flat=True))


def test_previsao_sem_titulo_faturado_vale():
    previsao(1)
    assert validas() == {1}


def test_previsao_ignorada_quando_o_contrato_ja_tem_titulo_no_mes():
    previsao(1, vencimento=date(2026, 3, 10))
    titulo(codigo=99, codigo_contrato=7, data_vencimento=date(2026, 3, 25))  # faturado, outro dia do mês
    assert validas() == set()


def test_titulo_de_outro_mes_contrato_ou_tipo_nao_esconde_a_previsao():
    previsao(1, vencimento=date(2026, 3, 10))
    titulo(codigo=91, codigo_contrato=7, data_vencimento=date(2026, 4, 10))   # outro mês
    titulo(codigo=92, codigo_contrato=8, data_vencimento=date(2026, 3, 10))   # outro contrato
    titulo(codigo=93, tipo=Titulo.Tipo.PAGAR, codigo_contrato=7, data_vencimento=date(2026, 3, 10))  # a pagar
    assert validas() == {1}


def test_titulo_de_outra_empresa_nao_esconde_a_previsao():
    """Vazamento entre empresas: o contrato 7 da empresa 2 não pode afetar a 1."""
    previsao(1)
    titulo(empresa=CLIENTE, codigo=99, codigo_contrato=7, data_vencimento=date(2026, 3, 10))
    assert validas(TATICA) == {1}


def test_previsao_sem_contrato_ou_vencimento_vale():
    titulo(tipo=Titulo.Tipo.PREVISAO, codigo=1, codigo_contrato=None, data_vencimento=date(2026, 3, 10))
    titulo(tipo=Titulo.Tipo.PREVISAO, codigo=2, codigo_contrato=7, data_vencimento=None)
    titulo(codigo=99, codigo_contrato=7, data_vencimento=date(2026, 3, 10))
    assert validas() == {1, 2}


def test_previsoes_validas_so_devolve_previsoes():
    titulo(codigo=1)
    previsao(2)
    assert validas() == {2}
