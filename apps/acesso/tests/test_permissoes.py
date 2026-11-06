"""Regra de escrita da AcessoFinanceiro.

Ainda não existe endpoint de escrita (RF-05 é decidido caso a caso), então
a regra é testada direto na permission class, com requisições de cada
método HTTP.
"""

import pytest
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.models import TokenUser

from apps.acesso.permissoes import AcessoFinanceiro
from apps.acesso.tests.perfis import PERFIS

fabrica = APIRequestFactory()


def _usuario(perfil):
    return TokenUser({'user_id': 1, **PERFIS[perfil]})


def _permite(perfil, metodo):
    request = getattr(fabrica, metodo)('/qualquer/')
    request.user = _usuario(perfil)
    return AcessoFinanceiro().has_permission(request, view=None)


@pytest.mark.parametrize('metodo', ['post', 'put', 'patch', 'delete'])
@pytest.mark.parametrize('perfil', ['adm', 'gerente_financeiro'])
def test_adm_e_gerente_financeiro_podem_alterar(perfil, metodo):
    assert _permite(perfil, metodo) is True


@pytest.mark.parametrize('metodo', ['post', 'put', 'patch', 'delete'])
def test_colaborador_financeiro_nunca_altera(metodo):
    assert _permite('colaborador_financeiro', metodo) is False


@pytest.mark.parametrize('metodo', ['get', 'head', 'options'])
def test_colaborador_financeiro_pode_ler(metodo):
    assert _permite('colaborador_financeiro', metodo) is True


@pytest.mark.parametrize('metodo', ['get', 'post'])
@pytest.mark.parametrize(
    'perfil', ['diretor_tatica', 'gerente_outro_setor_tatica', 'gerente_financeiro_cliente', 'colaborador_cliente']
)
def test_demais_papeis_nem_leem_nem_alteram(perfil, metodo):
    assert _permite(perfil, metodo) is False


def test_usuario_nao_autenticado_e_bloqueado():
    request = fabrica.get('/qualquer/')
    request.user = None
    assert AcessoFinanceiro().has_permission(request, view=None) is False
