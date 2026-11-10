import pytest

from apps.omie import erros
from apps.omie.erros import classificar


def test_sucesso():
    assert classificar(200, {'movimentos': []}) is None


@pytest.mark.parametrize(
    'status, corpo, classe',
    [
        (500, {'faultstring': 'ERROR: Não existem registros para a página [2]!', 'faultcode': 'SOAP-ENV:Client-5113'}, erros.SemRegistros),
        (425, {}, erros.ErroBloqueio),
        (429, {}, erros.ErroLimite),
        (500, {'faultstring': 'Too many requests'}, erros.ErroLimite),
        (500, {'faultstring': 'Consumo redundante detectado'}, erros.ErroRedundante),
        (500, {'faultstring': 'ERROR: A chave de acesso não está preenchida ou não é válida.'}, erros.ErroCredencial),
        (403, {}, erros.ErroCredencial),
        (502, None, erros.ErroTransitorio),
        (500, {'faultstring': 'PROTO_BYEBYE'}, erros.ErroPermanente),
        (500, {'faultstring': 'O preenchimento da tag [nCodCC] é obrigatório'}, erros.ErroPermanente),
        (400, {'faultstring': 'qualquer coisa nova'}, erros.ErroPermanente),
    ],
)
def test_classificacao(status, corpo, classe):
    assert type(classificar(status, corpo)) is classe


def test_resposta_real_de_credencial_invalida():
    """Capturada do Omie real em 2026-11-10 com uma credencial inventada."""
    corpo = {'faultstring': 'A chave de acesso não está preenchida ou não é válida.', 'faultcode': 'SOAP-ENV:Server'}
    assert type(classificar(403, corpo)) is erros.ErroCredencial


def test_fault_com_http_200_nao_e_sucesso():
    assert isinstance(classificar(200, {'faultstring': 'Too many requests'}), erros.ErroLimite)


def test_transitorios_e_permanentes():
    assert erros.ErroLimite('x').transitorio and erros.ErroRedundante('x').transitorio
    assert not erros.ErroCredencial('x').transitorio and not erros.ErroBloqueio('x').transitorio
