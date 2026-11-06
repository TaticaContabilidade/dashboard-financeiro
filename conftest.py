"""
Fixtures compartilhadas.

`gerar_token` imita o access token que o forms-meta vai emitir depois do
login novo (D-26/D-27): RS256, `token_type=access`, `user_id` e as claims
de papel/empresa/setor. Assina com a chave privada gerada em
`config.settings_test` — nunca com uma chave real.
"""

import time
import uuid

import jwt
import pytest
from rest_framework.test import APIClient

from apps.acesso.tests.perfis import PERFIS
from config.settings_test import CHAVE_PRIVADA_TESTE


@pytest.fixture
def gerar_token():
    def _gerar(
        perfil: str | None = None,
        *,
        chave: str = CHAVE_PRIVADA_TESTE,
        algoritmo: str = 'RS256',
        expira_em_segundos: int = 15 * 60,
        token_type: str = 'access',
        remover: tuple[str, ...] = (),
        **claims,
    ) -> str:
        agora = int(time.time())
        payload = {
            'token_type': token_type,
            'exp': agora + expira_em_segundos,
            'iat': agora,
            'jti': uuid.uuid4().hex,
            'user_id': 1,
            'empresa_id': 1,
            'setor_id': 1,
        }
        if perfil is not None:
            payload.update(PERFIS[perfil])
        payload.update(claims)
        for nome in remover:
            payload.pop(nome, None)
        return jwt.encode(payload, chave, algorithm=algoritmo)

    return _gerar


@pytest.fixture
def cliente():
    return APIClient()


@pytest.fixture
def cliente_com_token(cliente, gerar_token):
    def _cliente(perfil: str | None = None, **kwargs) -> APIClient:
        cliente.credentials(HTTP_AUTHORIZATION=f'Bearer {gerar_token(perfil, **kwargs)}')
        return cliente

    return _cliente

