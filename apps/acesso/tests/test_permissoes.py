"""Regra de escrita da AcessoFinanceiro, por método HTTP.

Ainda não existe endpoint de escrita (RF-05 é decidido caso a caso), então
a regra é testada direto na permission class.
"""

import pytest
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.models import TokenUser

from apps.acesso.permissoes import AcessoFinanceiro
from apps.acesso.tests.perfis import COM_ACESSO_E_ALTERA, COM_ACESSO_SO_LE, PERFIS, SEM_ACESSO

fabrica = APIRequestFactory()
ESCRITA = ['post', 'put', 'patch', 'delete']
LEITURA = ['get', 'head', 'options']


def _permite(perfil, metodo):
    request = getattr(fabrica, metodo)('/qualquer/')
    request.user = TokenUser({'user_id': 1, **PERFIS[perfil]})
    return AcessoFinanceiro().has_permission(request, view=None)


@pytest.mark.parametrize('metodo', LEITURA + ESCRITA)
@pytest.mark.parametrize('perfil', COM_ACESSO_E_ALTERA)
def test_adm_diretores_e_gerentes_financeiros_leem_e_alteram(perfil, metodo):
    assert _permite(perfil, metodo) is True


@pytest.mark.parametrize('perfil', COM_ACESSO_SO_LE)
def test_colaborador_financeiro_le_mas_nunca_altera(perfil):
    assert all(_permite(perfil, m) for m in LEITURA)
    assert not any(_permite(perfil, m) for m in ESCRITA)


@pytest.mark.parametrize('metodo', LEITURA + ESCRITA)
@pytest.mark.parametrize('perfil', SEM_ACESSO)
def test_fora_do_setor_financeiro_nem_le_nem_altera(perfil, metodo):
    assert _permite(perfil, metodo) is False


def test_usuario_nao_autenticado_e_bloqueado():
    request = fabrica.get('/qualquer/')
    request.user = None
    assert AcessoFinanceiro().has_permission(request, view=None) is False
