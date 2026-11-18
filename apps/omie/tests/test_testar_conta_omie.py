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
        if metodo == 'ListarClientes':
            return {'total_de_registros': 1, 'clientes_cadastro': [{
                'codigo_cliente_omie': 424242, 'razao_social': 'RAZAO-SENTINELA', 'nome_fantasia': 'RAZAO-SENTINELA',
                'cnpj_cpf': '12345678000199', 'inativo': 'N',
            }]}
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


def test_etapa_9_campos_e_ids_por_grupo_sem_valores(conta):
    baixa = {
        'detalhes': {'cGrupo': 'CONTA_CORRENTE_PAG', 'nCodTitulo': 424242, 'nCodMovCC': 777001,
                     'observacao': 'RAZAO-SENTINELA'},
        'resumo': {'nValPago': 987654.32},
    }
    cliente = ClienteFalso()
    cliente.listar_movimentos = lambda *a, **k: iter([MOVIMENTO, baixa, {**baixa}])
    saida = _rodar(cliente)
    assert 'CONTA_CORRENTE_PAG: 2 linhas' in saida
    assert 'nCodMovCC: preenchido 2, colide 2' in saida
    assert 'nCodTitulo distintos: 1 | também presentes em CONTA_A_*: 1' in saida
    assert 'observacao:2' in saida  # nome do campo e contagem, nunca o conteúdo
    for valor in SENTINELAS + ['777001']:
        assert valor not in saida, valor


def test_etapa_10_lote_chave_e_previsoes_so_contagens(conta):
    def cc(baixa, titulo, pago):
        return {'detalhes': {'cGrupo': 'CONTA_CORRENTE_PAG', 'nCodMovCC': 777001, 'nCodBaixa': baixa,
                             'nCodTitulo': titulo, 'nValorMovCC': 987654.32},
                'resumo': {'nValPago': pago}}
    lote = [cc(5001, 424242, 900000), cc(5002, 424243, 87654.32)]  # soma = nValorMovCC
    previsao = {'detalhes': {'cGrupo': 'PREVISAO_CONTRATO', 'nCodCtr': 31337, 'nCodTitulo': 9,
                             'cNumParcela': '001/012', 'dDtVenc': '10/01/2026', 'cStatus': 'PREVISAO'}, 'resumo': {}}
    titulo = {'detalhes': {'cGrupo': 'CONTA_A_RECEBER', 'nCodCtr': 31337, 'nCodTitulo': 10,
                           'cNumParcela': '001/012', 'dDtVenc': '15/01/2026'}, 'resumo': {}}
    cliente = ClienteFalso()
    cliente.listar_movimentos = lambda *a, **k: iter([*lote, previsao, titulo])
    saida = _rodar(cliente)
    assert 'colisões em cGrupo+nCodMovCC+nCodBaixa: 0' in saida
    assert 'nCodMovCC com mais de 1 linha (lote?): 1 grupos, 2 linhas' in saida
    assert '  soma dos nValPago == nValorMovCC: 1' in saida
    assert 'previsões com título a receber do mesmo contrato no mesmo mês de vencimento: 1' in saida
    assert 'previsões com título a receber do mesmo contrato e mesma cNumParcela: 1' in saida
    for valor in SENTINELAS + ['777001', '5001', '31337', '001/012']:
        assert valor not in saida, valor


def test_etapa_11_confere_conversao_dos_cadastros_sem_nomes(conta):
    saida = _rodar(ClienteFalso())
    assert 'clientes/fornecedores na conta: 1 (amostra de 1)' in saida
    assert 'cnpj_cpf: str' in saida  # nome do campo aparece; o CNPJ, não
    assert 'clientes/fornecedores (amostra): 1 | convertidos: 1 | ignorados: {}' in saida
    assert "categorias: 1 | convertidos: 0 | ignorados: {'categoria sem codigo': 1}" in saida
    assert 'contas correntes: 1 | convertidos: 1' in saida
    for valor in SENTINELAS:
        assert valor not in saida, valor


def test_etapa_11_mede_lancamentos_em_contas_fora_do_fluxo(conta):
    cliente = ClienteFalso()
    cliente.listar_contas_correntes = lambda *a, **k: iter([
        {'nCodCC': 10, 'descricao': 'BANCO-SENTINELA', 'inativo': 'N', 'nao_fluxo': 'S'},
        {'nCodCC': 11, 'descricao': 'BANCO-SENTINELA', 'inativo': 'N', 'nao_fluxo': 'N'},
    ])
    lancamento = {'detalhes': {'cGrupo': 'CONTA_CORRENTE_PAG', 'nCodMovCC': 1, 'nCodCC': 10}, 'resumo': {}}
    cliente.listar_movimentos = lambda *a, **k: iter([lancamento, {**lancamento}])
    saida = _rodar(cliente)
    assert "contas por marcação: {'fora/ativa': 1, 'no fluxo/ativa': 1}" in saida
    assert "por marcação da conta: {'fora do fluxo, ativa': 2}" in saida
    for valor in SENTINELAS:
        assert valor not in saida, valor
