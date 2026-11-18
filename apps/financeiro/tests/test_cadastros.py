"""Cadastros e saldos: unicidade sempre composta com a empresa (D-32)."""

from datetime import date

import pytest
from django.db import IntegrityError, transaction

from apps.financeiro.models import Categoria, ClienteFornecedor, ContaCorrente, SaldoConta

pytestmark = pytest.mark.django_db

TATICA, CLIENTE = 1, 2


def duplica(modelo, **chave) -> tuple[bool, bool]:
    """(mesma empresa viola?, outra empresa viola?)"""
    modelo.objects.create(empresa_id=TATICA, **chave)
    resultados = []
    for empresa in (TATICA, CLIENTE):
        try:
            with transaction.atomic():
                modelo.objects.create(empresa_id=empresa, **chave)
            resultados.append(False)
        except IntegrityError:
            resultados.append(True)
    return tuple(resultados)


@pytest.mark.parametrize('modelo, chave', [
    (ContaCorrente, {'codigo': 10}),
    (Categoria, {'codigo': '2.01.03'}),
    (ClienteFornecedor, {'codigo': 77}),
    (SaldoConta, {'codigo_conta_corrente': 10, 'data': date(2026, 11, 18)}),
])
def test_unico_por_empresa(modelo, chave):
    assert duplica(modelo, **chave) == (True, False)


def test_saldo_guarda_um_dia_por_conta():
    for dia in (date(2026, 11, 17), date(2026, 11, 18)):
        SaldoConta.objects.create(empresa_id=TATICA, codigo_conta_corrente=10, data=dia)
    assert SaldoConta.objects.da_empresa(TATICA).count() == 2
    assert SaldoConta.objects.da_empresa(CLIENTE).count() == 0
