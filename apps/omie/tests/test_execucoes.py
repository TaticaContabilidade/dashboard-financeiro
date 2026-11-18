"""Registro das execuções e ponto de corte por empresa (RNF-11, RNF-12)."""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.omie.models import ExecucaoSincronizacao as Execucao

pytestmark = pytest.mark.django_db

TATICA, CLIENTE = 1, 2
AGORA = timezone.now()


def execucao(empresa=TATICA, tipo=Execucao.Tipo.INCREMENTAL, situacao=Execucao.Situacao.SUCESSO,
             minutos_atras=0, ate=date(2026, 11, 18)):
    return Execucao.objects.create(
        empresa_id=empresa, tipo=tipo, situacao=situacao, alterados_ate=ate,
        finalizada_em=AGORA - timedelta(minutes=minutos_atras) if situacao != Execucao.Situacao.EM_ANDAMENTO else None,
    )


def ultima(empresa=TATICA, *tipos):
    return Execucao.objects.da_empresa(empresa).ultima_bem_sucedida(*tipos)


def test_ultima_bem_sucedida_e_a_mais_recente():
    execucao(minutos_atras=10, ate=date(2026, 11, 17))
    nova = execucao(minutos_atras=5)
    assert ultima() == nova


def test_erro_e_em_andamento_nao_avancam_o_corte():
    """RNF-11: só sucesso conta como ponto de corte."""
    certa = execucao(minutos_atras=10)
    execucao(situacao=Execucao.Situacao.ERRO, minutos_atras=1)
    execucao(situacao=Execucao.Situacao.EM_ANDAMENTO)
    assert ultima() == certa


def test_filtra_por_tipo():
    incremental = execucao(minutos_atras=10)
    reconciliacao = execucao(tipo=Execucao.Tipo.RECONCILIACAO, minutos_atras=1)
    assert ultima(TATICA) == reconciliacao
    assert ultima(TATICA, Execucao.Tipo.INCREMENTAL) == incremental


def test_corte_de_uma_empresa_nao_vale_para_outra():
    execucao(empresa=CLIENTE)
    assert ultima(TATICA) is None
    assert ultima(CLIENTE) is not None


def test_sem_execucao_devolve_none():
    assert ultima() is None
