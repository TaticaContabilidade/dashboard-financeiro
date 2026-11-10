import logging

import pytest

from apps.omie import erros
from apps.omie.cliente import ClienteOmie
from apps.omie.limitador import Limitador

APP_KEY, APP_SECRET = 'APPKEY-SENTINELA', 'SECRET-SENTINELA'


class TransporteFalso:
    """Devolve respostas programadas e registra o que foi enviado."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.enviados = []

    def __call__(self, url, corpo, timeout):
        self.enviados.append((url, corpo))
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


def _cliente(transporte, dormidas=None):
    return ClienteOmie(
        APP_KEY, APP_SECRET,
        transporte=transporte,
        limitador=Limitador(limite_ip=10_000, limite_por_metodo=10_000),
        dormir=(dormidas.append if dormidas is not None else lambda s: None),
    )


def test_formato_da_chamada():
    t = TransporteFalso((200, {'ok': 1}))
    assert _cliente(t).chamar('financas/mf', 'ListarMovimentos', {'nPagina': 1}) == {'ok': 1}
    url, corpo = t.enviados[0]
    assert url == 'https://app.omie.com.br/api/v1/financas/mf/'
    assert corpo == {'call': 'ListarMovimentos', 'app_key': APP_KEY, 'app_secret': APP_SECRET, 'param': [{'nPagina': 1}]}


def test_paginacao_percorre_todas_as_paginas():
    t = TransporteFalso(
        (200, {'nTotPaginas': 2, 'movimentos': [{'id': 1}, {'id': 2}]}),
        (200, {'nTotPaginas': 2, 'movimentos': [{'id': 3}]}),
    )
    assert [m['id'] for m in _cliente(t).listar_movimentos({'dDtAltDe': '01/11/2026'}, por_pagina=2)] == [1, 2, 3]
    assert [c['param'][0]['nPagina'] for _, c in t.enviados] == [1, 2]
    assert t.enviados[0][1]['param'][0] == {'dDtAltDe': '01/11/2026', 'nPagina': 1, 'nRegPorPagina': 2}


def test_sem_registros_vira_lista_vazia():
    t = TransporteFalso((500, {'faultstring': 'ERROR: Não existem registros para a página [1]!'}))
    assert list(_cliente(t).listar_contas_pagar()) == []


def test_retenta_falha_passageira_com_espera_crescente():
    dormidas = []
    t = TransporteFalso(erros.ErroTransitorio('rede'), (503, None), (200, {'ok': 1}))
    assert _cliente(t, dormidas).chamar('s', 'M', {}) == {'ok': 1}
    assert dormidas == [2.0, 5.0]


def test_desiste_depois_das_retentativas():
    t = TransporteFalso((503, None), (503, None), (503, None))
    with pytest.raises(erros.ErroTransitorio):
        _cliente(t).chamar('s', 'M', {})
    assert len(t.enviados) == 3


@pytest.mark.parametrize(
    'resposta, classe',
    [
        ((429, {}), erros.ErroLimite),
        ((500, {'faultstring': 'Consumo redundante detectado'}), erros.ErroRedundante),
        ((425, {}), erros.ErroBloqueio),
        ((500, {'faultstring': 'A chave de acesso não é válida'}), erros.ErroCredencial),
        ((500, {'faultstring': 'tag obrigatória'}), erros.ErroPermanente),
    ],
)
def test_nao_retenta_na_hora_o_que_nao_e_falha_de_rede(resposta, classe):
    """Insistir em erro aproxima o bloqueio de 30 min (10 erros seguidos)."""
    t = TransporteFalso(resposta, (200, {}))
    with pytest.raises(classe):
        _cliente(t).chamar('s', 'M', {})
    assert len(t.enviados) == 1


def test_credenciais_nao_aparecem_em_log_nem_repr(caplog):
    caplog.set_level(logging.DEBUG)
    t = TransporteFalso((200, {'movimentos': [{'detalhes': {'cRazao': 'RAZAO-SENTINELA'}}], 'nTotPaginas': 1}))
    cliente = _cliente(t)
    list(cliente.listar_movimentos())
    tudo = caplog.text + repr(cliente)
    for segredo in (APP_KEY, APP_SECRET, 'RAZAO-SENTINELA'):
        assert segredo not in tudo
    assert 'ListarMovimentos' in caplog.text


def test_exige_credenciais():
    with pytest.raises(ValueError):
        ClienteOmie('', 'x')
