from io import StringIO
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection

from apps.omie.models import ContaOmie

pytestmark = pytest.mark.django_db


def _conta(empresa_id=2, app_key='chave-123', app_secret='segredo-456'):
    conta = ContaOmie(empresa_id=empresa_id)
    conta.definir_credenciais(app_key, app_secret)
    conta.save()
    return conta


def test_credenciais_ficam_cifradas_no_banco():
    conta = _conta()
    with connection.cursor() as cursor:
        cursor.execute('SELECT app_key_cifrada, app_secret_cifrado FROM omie_contas WHERE id = %s', [conta.pk])
        app_key_bruta, app_secret_bruto = cursor.fetchone()
    assert 'chave-123' not in app_key_bruta
    assert 'segredo-456' not in app_secret_bruto
    assert ContaOmie.objects.get(pk=conta.pk).credenciais() == ('chave-123', 'segredo-456')


def test_repr_e_str_nao_vazam_credenciais():
    conta = _conta()
    for texto in (repr(conta), str(conta)):
        assert 'chave-123' not in texto and 'segredo-456' not in texto


def test_uma_conta_por_empresa():
    _conta(empresa_id=2)
    with pytest.raises(IntegrityError):
        _conta(empresa_id=2)


def test_credenciais_vazias_sao_recusadas():
    with pytest.raises(ValueError):
        ContaOmie(empresa_id=2).definir_credenciais('  ', 'x')


def test_da_empresa_isola_as_contas():
    _conta(empresa_id=1)
    _conta(empresa_id=2)
    assert list(ContaOmie.objects.da_empresa(2).values_list('empresa_id', flat=True)) == [2]


def test_da_empresa_sem_id_falha_alto():
    with pytest.raises(ValueError):
        ContaOmie.objects.da_empresa(None)


# --- comandos ------------------------------------------------------------


def _configurar(empresa_id, app_key, app_secret, *extra):
    with mock.patch(
        'apps.omie.management.commands.configurar_conta_omie.getpass', side_effect=[app_key, app_secret]
    ):
        saida = StringIO()
        call_command('configurar_conta_omie', '--empresa-id', str(empresa_id), *extra, stdout=saida)
        return saida.getvalue()


def test_comando_cadastra_e_depois_atualiza():
    assert 'cadastrada' in _configurar(3, 'k1', 's1')
    assert 'atualizada' in _configurar(3, 'k2', 's2')
    assert ContaOmie.objects.get(empresa_id=3).credenciais() == ('k2', 's2')


def test_comando_nao_imprime_credenciais():
    saida = _configurar(3, 'chave-secreta', 'segredo-secreto')
    assert 'chave-secreta' not in saida and 'segredo-secreto' not in saida


def test_comando_recusa_credencial_vazia():
    with pytest.raises(CommandError):
        _configurar(3, '', 's')


def test_comando_desativa_sem_apagar():
    _conta(empresa_id=4)
    call_command('configurar_conta_omie', '--empresa-id', '4', '--desativar', stdout=StringIO())
    conta = ContaOmie.objects.get(empresa_id=4)
    assert conta.ativa is False
    assert conta.credenciais() == ('chave-123', 'segredo-456')


def test_recifrar_mantem_as_credenciais(settings):
    from cryptography.fernet import Fernet

    _conta(empresa_id=5)
    settings.OMIE_CREDENCIAIS_CHAVE = f'{Fernet.generate_key().decode()},{settings.OMIE_CREDENCIAIS_CHAVE}'
    call_command('recifrar_contas_omie', stdout=StringIO())
    settings.OMIE_CREDENCIAIS_CHAVE = settings.OMIE_CREDENCIAIS_CHAVE.split(',')[0]
    assert ContaOmie.objects.get(empresa_id=5).credenciais() == ('chave-123', 'segredo-456')
