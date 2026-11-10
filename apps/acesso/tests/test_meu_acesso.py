"""Validação do JWT + permissão do Financeiro, ponta a ponta pela API."""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from apps.acesso.tests.perfis import COM_ACESSO_E_ALTERA, COM_ACESSO_SO_LE, PERFIS, SEM_ACESSO

URL = '/api/financeiro/meu-acesso/'


# --- Token ausente ou inválido -> 401 ------------------------------------


def test_sem_token_recebe_401(cliente):
    assert cliente.get(URL).status_code == 401


def test_token_assinado_por_outra_chave_recebe_401(cliente_com_token):
    outra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = outra.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    assert cliente_com_token('adm', chave=pem).get(URL).status_code == 401


def test_token_hs256_antigo_do_forms_meta_recebe_401(cliente_com_token):
    """Tokens emitidos antes da troca para RS256 não podem passar (D-20)."""
    resp = cliente_com_token('adm', chave='segredo-qualquer-do-forms-meta-com-32-bytes-ou-mais', algoritmo='HS256').get(URL)
    assert resp.status_code == 401


def test_token_expirado_recebe_401(cliente_com_token):
    assert cliente_com_token('adm', expira_em_segundos=-60).get(URL).status_code == 401


def test_refresh_token_no_lugar_do_access_recebe_401(cliente_com_token):
    assert cliente_com_token('adm', token_type='refresh').get(URL).status_code == 401


def test_token_sem_user_id_recebe_401(cliente_com_token):
    assert cliente_com_token('adm', remover=('user_id',)).get(URL).status_code == 401


def test_token_com_user_id_nulo_e_bloqueado(cliente_com_token):
    """O simplejwt só checa se a claim existe; `null` é barrado na permissão."""
    assert cliente_com_token('adm', user_id=None).get(URL).status_code == 403


def test_header_mal_formado_recebe_401(cliente):
    cliente.credentials(HTTP_AUTHORIZATION='Token abc')
    assert cliente.get(URL).status_code == 401


# --- Quem tem acesso -> 200 ----------------------------------------------


@pytest.mark.parametrize('perfil', COM_ACESSO_E_ALTERA)
def test_quem_altera_recebe_pode_alterar(cliente_com_token, perfil):
    resp = cliente_com_token(perfil).get(URL)
    assert resp.status_code == 200
    assert resp.json()['pode_ver'] is True
    assert resp.json()['pode_alterar'] is True
    assert resp.json()['empresa_id'] == PERFIS[perfil]['empresa_id']


@pytest.mark.parametrize('perfil', COM_ACESSO_SO_LE)
def test_colaborador_financeiro_so_le(cliente_com_token, perfil):
    resp = cliente_com_token(perfil).get(URL)
    assert resp.status_code == 200
    assert resp.json()['pode_alterar'] is False


# --- Quem não tem acesso -> 403 ------------------------------------------


@pytest.mark.parametrize('perfil', SEM_ACESSO)
def test_fora_do_setor_financeiro_e_bloqueado(cliente_com_token, perfil):
    assert cliente_com_token(perfil).get(URL).status_code == 403


def test_sem_claims_de_papel_e_bloqueado(cliente_com_token):
    """Token válido mas no formato antigo (sem as claims da D-26)."""
    assert cliente_com_token().get(URL).status_code == 403


@pytest.mark.parametrize('valor', ['true', 'True', 1, 'false'])
def test_claim_booleana_nao_aceita_string_nem_numero(cliente_com_token, valor):
    resp = cliente_com_token(papel='GERENTE', setor_financeiro=valor).get(URL)
    assert resp.status_code == 403


@pytest.mark.parametrize('valor', [None, 0, -1, '2', True])
def test_sem_empresa_valida_no_token_e_bloqueado(cliente_com_token, valor):
    """Sem empresa não há como escopar os dados (D-32) — nem para o ADM."""
    assert cliente_com_token('adm', empresa_id=valor).get(URL).status_code == 403


def test_token_sem_claim_de_empresa_e_bloqueado(cliente_com_token):
    assert cliente_com_token('diretor_cliente', remover=('empresa_id',)).get(URL).status_code == 403
