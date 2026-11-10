from django.conf import settings

from config.settings_test import CHAVE_PRIVADA_TESTE


def test_suite_usa_chave_gerada_no_teste():
    """A suíte nunca pode rodar com a chave pública real de um .env."""
    assert settings.SIMPLE_JWT['ALGORITHM'] == 'RS256'
    assert settings.SIMPLE_JWT['SIGNING_KEY'] is None
    assert settings.SIMPLE_JWT['VERIFYING_KEY'].startswith('-----BEGIN PUBLIC KEY-----')
    assert CHAVE_PRIVADA_TESTE.startswith('-----BEGIN PRIVATE KEY-----')
