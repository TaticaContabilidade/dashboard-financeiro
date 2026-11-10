from unittest import mock

import pytest
from django.db.utils import OperationalError

URL = '/api/health/'


@pytest.mark.django_db
def test_health_ok_sem_token(cliente):
    resp = cliente.get(URL)
    assert resp.status_code == 200
    assert resp.json() == {'status': 'ok', 'banco': 'ok'}


@pytest.mark.django_db
def test_health_ignora_token_invalido(cliente):
    """Health check não autentica: um header inválido não pode derrubá-lo."""
    cliente.credentials(HTTP_AUTHORIZATION='Bearer lixo')
    assert cliente.get(URL).status_code == 200


def test_health_503_quando_banco_cai(cliente):
    with mock.patch('apps.core.views.connection.cursor', side_effect=OperationalError('fora do ar')):
        resp = cliente.get(URL)
    assert resp.status_code == 503
    assert resp.json()['status'] == 'erro'
