"""De qual empresa são os dados (apps.acesso.escopo, D-32)."""

import pytest
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.models import TokenUser

from apps.acesso.escopo import empresa_da_requisicao
from apps.acesso.tests.perfis import CLIENTE, PERFIS, TATICA

fabrica = APIRequestFactory()


def _req(perfil, **query):
    request = Request(fabrica.get('/qualquer/', query))
    request.user = TokenUser({'user_id': 1, **PERFIS[perfil]})
    return request


@pytest.mark.parametrize(
    'perfil, esperada',
    [('diretor_tatica', TATICA), ('diretor_cliente', CLIENTE), ('colaborador_financeiro_cliente', CLIENTE)],
)
def test_sem_parametro_usa_a_empresa_do_token(perfil, esperada):
    assert empresa_da_requisicao(_req(perfil)) == esperada


def test_pedir_a_propria_empresa_e_aceito():
    assert empresa_da_requisicao(_req('diretor_cliente', empresa_id=CLIENTE)) == CLIENTE


@pytest.mark.parametrize('perfil', ['diretor_cliente', 'gerente_financeiro_cliente', 'diretor_tatica'])
def test_nao_adm_pedindo_outra_empresa_recebe_403(perfil):
    outra = TATICA if PERFIS[perfil]['empresa_id'] == CLIENTE else CLIENTE
    with pytest.raises(PermissionDenied):
        empresa_da_requisicao(_req(perfil, empresa_id=outra))


def test_adm_escolhe_qualquer_empresa():
    assert empresa_da_requisicao(_req('adm', empresa_id=CLIENTE)) == CLIENTE
    assert empresa_da_requisicao(_req('adm', empresa_id=999)) == 999


def test_adm_sem_parametro_usa_a_propria():
    assert empresa_da_requisicao(_req('adm')) == TATICA


@pytest.mark.parametrize('valor', ['abc', '1.5', '0', '-3'])
def test_parametro_invalido_recebe_400(valor):
    with pytest.raises(ValidationError):
        empresa_da_requisicao(_req('adm', empresa_id=valor))


# --- EscopoEmpresaMixin numa view de verdade -----------------------------
# View só de teste sobre ContaOmie (o único ModeloDaEmpresa por enquanto),
# expondo apenas empresa_id — imita como as views de contas a pagar/receber
# vão usar o mixin.

from rest_framework import generics, serializers  # noqa: E402
from rest_framework.test import force_authenticate  # noqa: E402

from apps.acesso.escopo import EscopoEmpresaMixin  # noqa: E402
from apps.omie.models import ContaOmie  # noqa: E402


class _ContaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContaOmie
        fields = ('empresa_id', 'ativa')


class _ContasView(EscopoEmpresaMixin, generics.ListAPIView):
    queryset = ContaOmie.objects.all()
    serializer_class = _ContaSerializer


@pytest.fixture
def contas(db):
    for empresa_id in (TATICA, CLIENTE):
        conta = ContaOmie(empresa_id=empresa_id)
        conta.definir_credenciais('k', 's')
        conta.save()


def _listar(perfil, **query):
    request = fabrica.get('/contas/', query)
    force_authenticate(request, user=TokenUser({'user_id': 1, **PERFIS[perfil]}))
    return _ContasView.as_view()(request)


def test_mixin_mostra_so_a_empresa_do_usuario(contas):
    resp = _listar('diretor_cliente')
    assert resp.status_code == 200
    assert [c['empresa_id'] for c in resp.data] == [CLIENTE]


def test_mixin_recusa_outra_empresa_para_nao_adm(contas):
    assert _listar('diretor_cliente', empresa_id=TATICA).status_code == 403


def test_mixin_adm_escolhe_a_empresa(contas):
    assert [c['empresa_id'] for c in _listar('adm', empresa_id=CLIENTE).data] == [CLIENTE]


def test_mixin_sem_permissao_continua_403(contas):
    assert _listar('gerente_outro_setor_tatica').status_code == 403
