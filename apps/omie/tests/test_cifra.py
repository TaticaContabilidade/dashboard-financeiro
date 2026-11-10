import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

from apps.omie import cifra


def test_ida_e_volta():
    cifrado = cifra.cifrar('minha-app-key')
    assert cifrado != 'minha-app-key'
    assert 'minha-app-key' not in cifrado
    assert cifra.decifrar(cifrado) == 'minha-app-key'


def test_mesmo_valor_gera_cifrados_diferentes():
    assert cifra.cifrar('x') != cifra.cifrar('x')


def test_sem_chave_falha_alto(settings):
    settings.OMIE_CREDENCIAIS_CHAVE = ''
    with pytest.raises(ImproperlyConfigured):
        cifra.cifrar('x')


def test_chave_invalida_falha_alto(settings):
    settings.OMIE_CREDENCIAIS_CHAVE = 'nao-e-uma-chave-fernet'
    with pytest.raises(ImproperlyConfigured):
        cifra.cifrar('x')


def test_valor_de_outra_chave_e_ilegivel(settings):
    cifrado = cifra.cifrar('segredo')
    settings.OMIE_CREDENCIAIS_CHAVE = Fernet.generate_key().decode()
    with pytest.raises(cifra.CredencialIlegivel):
        cifra.decifrar(cifrado)


def test_rotacao_de_chave(settings):
    antiga = settings.OMIE_CREDENCIAIS_CHAVE
    cifrado_antigo = cifra.cifrar('segredo')
    nova = Fernet.generate_key().decode()

    settings.OMIE_CREDENCIAIS_CHAVE = f'{nova},{antiga}'
    assert cifra.decifrar(cifrado_antigo) == 'segredo'  # antiga ainda abre
    recifrado = cifra.recifrar(cifrado_antigo)

    settings.OMIE_CREDENCIAIS_CHAVE = nova  # antiga removida
    assert cifra.decifrar(recifrado) == 'segredo'
    with pytest.raises(cifra.CredencialIlegivel):
        cifra.decifrar(cifrado_antigo)
