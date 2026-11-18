"""O comando de teste nunca pode imprimir valores, nomes ou documentos."""

from io import StringIO
from unittest import mock

import pytest
from django.core.management import CommandError, call_command

from apps.omie import erros
from apps.omie.models import ContaOmie

pytestmark = pytest.mark.django_db

SENTINELAS = [
    'RAZAO-SENTINELA', '12345678000199', '987654.32', 'DESCRICAO-SENTINELA', 'BANCO-SENTINELA',
    'TEXTO-LIVRE-SENTINELA-COMPRIDO-DEMAIS', '424242',
]

MOVIMENTO = {
    'detalhes': {
        'cNatureza': 'P', 'cStatus': 'PAGO', 'dDtEmissao': '01/01/2026', 'dDtPagamento': '05/01/2026',
        'nCodTitulo': 424242, 'cGrupo': 'CONTA_A_PAGAR', 'cOrigem': 'TEXTO-LIVRE-SENTINELA-COMPRIDO-DEMAIS',
        'cTipo': 'BOL',
        'nCodCC': 10, 'nValorTitulo': 987654.32, 'cCPFCNPJCliente': '12345678000199', 'observacao': 'RAZAO-SENTINELA',
    },
    'resumo': {'nValPago': 987654.32, 'cLiquidado': 'S'},
}


class ClienteFalso:
    def listar_contas_correntes(self, *a, **k):
        return iter([{'nCodCC': 10, 'descricao': 'BANCO-SENTINELA', 'inativo': 'N', 'saldo_inicial': 987654.32}])

    def chamar(self, servico, metodo, param):
        if metodo == 'ListarMovimentos':
            return {'movimentos': [MOVIMENTO] * 2, 'nRegistros': 2, 'nTotRegistros': 2, 'nTotPaginas': 1}
        if metodo == 'ListarContasPagar':
            return {'total_de_registros': 1, 'conta_pagar_cadastro': [{'valor_documento': 987654.32}]}
        raise AssertionError(metodo)

    def listar_movimentos(self, *a, **k):
        return iter([MOVIMENTO, MOVIMENTO])

    def saldo_conta_corrente(self, *a):
        return {'nSaldoAtual': 987654.32, 'cDescricao': 'BANCO-SENTINELA'}

    def listar_categorias(self, *a, **k):
        return iter([{'descricao': 'DESCRICAO-SENTINELA', 'conta_receita': 'S'}])


@pytest.fixture
def conta():
    c = ContaOmie(empresa_id=1)
    c.definir_credenciais('k', 's')
    c.save()
    return c


def _rodar(cliente):
    saida = StringIO()
    with mock.patch('apps.omie.management.commands.testar_conta_omie.ClienteOmie.da_conta', return_value=cliente):
        call_command('testar_conta_omie', '--empresa-id', '1', stdout=saida)
    return saida.getvalue()


def test_mostra_contagens_e_estrutura_sem_valores(conta):
    saida = _rodar(ClienteFalso())
    assert "por natureza (P=pagar, R=receber): {'P': 2}" in saida
    assert 'nValorTitulo: float' in saida
    assert "campos de saldo: ['nSaldoAtual']" in saida
    for valor in SENTINELAS:
        assert valor not in saida, valor


def test_credencial_recusada_interrompe(conta):
    cliente = ClienteFalso()
    cliente.listar_contas_correntes = mock.Mock(side_effect=erros.ErroCredencial('recusada', status=500))
    with pytest.raises(CommandError):
        _rodar(cliente)


def test_empresa_sem_conta():
    with pytest.raises(CommandError):
        call_command('testar_conta_omie', '--empresa-id', '99', stdout=StringIO())


def test_etapa_8_conta_repeticoes_sem_mostrar_ids_nem_texto_livre(conta):
    saida = _rodar(ClienteFalso())  # o ClienteFalso devolve o mesmo movimento 2x
    assert 'nCodTitulo que aparecem mais de 1 vez: 1 (maior repetição: 2)' in saida
    assert '  nCodTitulo: 2' in saida  # as 2 linhas colidem nessa chave
    assert "por cGrupo: {'CONTA_A_PAGAR': 2}" in saida
    assert "por cOrigem: {'<texto longo>': 2}" in saida
    assert "('nValorTitulo', 'float'): 2" in saida
    for valor in SENTINELAS:  # nem o id do título nem o texto livre aparecem
        assert valor not in saida, valor


def test_etapa_8_pula_sem_movimentos(conta):
    cliente = ClienteFalso()
    cliente.listar_movimentos = lambda *a, **k: iter([])
    assert 'sem movimentos da etapa 4 — pulado' in _rodar(cliente)
