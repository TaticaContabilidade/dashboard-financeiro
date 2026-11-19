"""Sincronização por empresa (RF-06, RF-07, RNF-09 a RNF-12). Nunca chama o Omie real."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.core.management import CommandError, call_command
from django.db import connections

from apps.financeiro.models import Categoria, ClienteFornecedor, ContaCorrente, LancamentoConta, SaldoConta, Titulo
from apps.omie import erros
from apps.omie.models import ContaOmie
from apps.omie.models import ExecucaoSincronizacao as Execucao
from apps.omie.sincronizacao import NAMESPACE_LOCK, Sincronizador

pytestmark = pytest.mark.django_db

TATICA, CLIENTE = 1, 2
SP = ZoneInfo('America/Sao_Paulo')
DIA1 = datetime(2026, 11, 18, 10, 0, tzinfo=SP)
DIA2 = datetime(2026, 11, 19, 10, 0, tzinfo=SP)


def titulo(codigo, status='A VENCER', grupo='CONTA_A_PAGAR', **extra):
    return {'detalhes': {'cGrupo': grupo, 'nCodTitulo': codigo, 'cStatus': status,
                         'nValorTitulo': 100, **extra}, 'resumo': {}}


def baixa(movimento, baixa_, pago, codigo_titulo=1):
    return {'detalhes': {'cGrupo': 'CONTA_CORRENTE_PAG', 'nCodMovCC': movimento, 'nCodBaixa': baixa_,
                         'nCodTitulo': codigo_titulo, 'nValorMovCC': 300}, 'resumo': {'nValPago': pago}}


class ClienteFalso:
    def __init__(self, movimentos=None, contas=None, categorias=None, clientes=None, saldo=None):
        self.movimentos = movimentos if movimentos is not None else [titulo(1)]
        self.contas = contas if contas is not None else [
            {'nCodCC': 10, 'descricao': 'Conta A', 'inativo': 'N'},
            {'nCodCC': 11, 'descricao': 'Conta B', 'inativo': 'S'},
        ]
        self.categorias = categorias if categorias is not None else [{'codigo': '1.01', 'descricao': 'Receita'}]
        self.clientes = clientes if clientes is not None else [{'codigo_cliente_omie': 77, 'razao_social': 'X'}]
        self.saldo = saldo if saldo is not None else {'nSaldoAtual': 500.25}
        self.filtros_pedidos = []
        self.saldos_pedidos = []
        self.erro_em = None  # nome do método que deve falhar

    def _talvez_falhe(self, metodo):
        if self.erro_em and self.erro_em[0] == metodo:
            raise self.erro_em[1]

    def listar_movimentos(self, filtros, **kw):
        self.filtros_pedidos.append(filtros)
        self._talvez_falhe('movimentos')
        return iter(list(self.movimentos))

    def listar_contas_correntes(self, *a, **kw):
        self._talvez_falhe('contas')
        return iter(self.contas)

    def listar_categorias(self, *a, **kw):
        return iter(self.categorias)

    def listar_clientes(self, *a, **kw):
        return iter(self.clientes)

    def saldo_conta_corrente(self, codigo, de, ate):
        self.saldos_pedidos.append(codigo)
        self._talvez_falhe('saldo')
        return dict(self.saldo)


@pytest.fixture
def conta():
    return _conta(TATICA)


def _conta(empresa):
    c = ContaOmie(empresa_id=empresa)
    c.definir_credenciais('k', 's')
    c.save()
    return c


def rodar(conta_, cliente, agora=DIA1, reconciliacao=False):
    return Sincronizador(conta_, cliente=cliente, agora=agora).executar(reconciliacao=reconciliacao)


# --- primeira execução e incremental -----------------------------------------

def test_primeira_execucao_faz_carga_completa(conta):
    cliente = ClienteFalso(movimentos=[titulo(1), baixa(900, 1, 100), baixa(900, 2, 200)])
    execucao = rodar(conta, cliente)
    assert execucao.tipo == Execucao.Tipo.RECONCILIACAO
    assert execucao.situacao == Execucao.Situacao.SUCESSO
    assert cliente.filtros_pedidos == [{'dDtAltDe': '01/01/2000', 'dDtAltAte': '18/11/2026'}]
    assert Titulo.objects.da_empresa(TATICA).count() == 1
    assert LancamentoConta.objects.da_empresa(TATICA).count() == 2   # baixa em lote: 2 linhas
    assert ContaCorrente.objects.da_empresa(TATICA).count() == 2
    assert Categoria.objects.da_empresa(TATICA).count() == 1
    assert ClienteFornecedor.objects.da_empresa(TATICA).count() == 1
    assert execucao.contagens['titulos'] == {'gravados': 1}
    conta.refresh_from_db()
    assert conta.ultima_sincronizacao_em is not None


def test_saldo_so_das_contas_ativas(conta):
    cliente = ClienteFalso()
    rodar(conta, cliente)
    assert cliente.saldos_pedidos == [10]
    saldo = SaldoConta.objects.da_empresa(TATICA).get()
    assert (saldo.codigo_conta_corrente, saldo.data, saldo.saldo_atual) == (10, date(2026, 11, 18), Decimal('500.25'))


def test_incremental_parte_do_ponto_de_corte_e_atualiza_sem_duplicar(conta):
    rodar(conta, ClienteFalso(movimentos=[titulo(1, 'A VENCER')]), agora=DIA1)
    cliente = ClienteFalso(movimentos=[titulo(1, 'PAGO'), titulo(2)])
    execucao = rodar(conta, cliente, agora=DIA2)
    assert execucao.tipo == Execucao.Tipo.INCREMENTAL
    # relê o dia do corte de propósito (filtro do Omie é por dia)
    assert cliente.filtros_pedidos == [{'dDtAltDe': '18/11/2026', 'dDtAltAte': '19/11/2026'}]
    assert Titulo.objects.da_empresa(TATICA).count() == 2
    assert Titulo.objects.da_empresa(TATICA).get(codigo_titulo=1).status == 'PAGO'
    # incremental não relê cadastros
    assert execucao.contagens.get('contas_correntes') is None


def test_chave_repetida_no_mesmo_lote_nao_quebra(conta):
    execucao = rodar(conta, ClienteFalso(movimentos=[titulo(1, 'A VENCER'), titulo(1, 'PAGO')]))
    assert execucao.situacao == Execucao.Situacao.SUCESSO
    assert Titulo.objects.da_empresa(TATICA).get().status == 'PAGO'


def test_linha_ignorada_conta_o_motivo_e_nao_impede_as_outras(conta):
    execucao = rodar(conta, ClienteFalso(movimentos=[titulo(1), {'detalhes': {'cGrupo': 'NOVO'}}]))
    assert execucao.situacao == Execucao.Situacao.SUCESSO
    assert execucao.contagens['ignorados'] == {"cGrupo desconhecido: 'NOVO'": 1}
    assert Titulo.objects.da_empresa(TATICA).count() == 1


# --- erros -------------------------------------------------------------------

def test_erro_passageiro_nao_avanca_o_corte_nem_suspende(conta):
    rodar(conta, ClienteFalso(), agora=DIA1)
    cliente = ClienteFalso()
    cliente.erro_em = ('movimentos', erros.ErroTransitorio('Falha de rede ao chamar o Omie: Timeout.'))
    falhou = rodar(conta, cliente, agora=DIA2)
    assert falhou.situacao == Execucao.Situacao.ERRO
    assert falhou.erro_tipo == 'ErroTransitorio'
    conta.refresh_from_db()
    assert conta.suspensa_em is None and conta.pausada_ate is None
    # a próxima incremental volta a pedir desde o corte antigo (RNF-11)
    proxima = ClienteFalso()
    rodar(conta, proxima, agora=DIA2)
    assert proxima.filtros_pedidos[0]['dDtAltDe'] == '18/11/2026'


def test_credencial_recusada_suspende_a_empresa(conta):
    cliente = ClienteFalso()
    cliente.erro_em = ('contas', erros.ErroCredencial('Credencial do Omie recusada.', status=403,
                                                     faultstring='A chave de acesso não está preenchida'))
    execucao = rodar(conta, cliente)
    assert execucao.erro_tipo == 'ErroCredencial'
    assert 'status=403' in execucao.erro_detalhe
    conta.refresh_from_db()
    assert conta.suspensa_em is not None
    assert not conta.pode_sincronizar(DIA2)
    conta.definir_credenciais('nova', 'nova')  # configurar_conta_omie limpa a suspensão
    assert conta.suspensa_em is None and conta.pode_sincronizar(DIA2)


@pytest.mark.parametrize('erro', [
    erros.ErroBloqueio('Omie bloqueou (HTTP 425).', status=425),
    erros.ErroPermanente('Omie recusou a requisição (HTTP 500).', status=500, faultstring='Tag inválida'),
])
def test_bloqueio_ou_erro_permanente_pausa_30_min(conta, erro):
    cliente = ClienteFalso()
    cliente.erro_em = ('contas', erro)
    execucao = rodar(conta, cliente)
    conta.refresh_from_db()
    assert conta.pausada_ate == execucao.finalizada_em + timedelta(minutes=30)
    assert not conta.pode_sincronizar(execucao.finalizada_em + timedelta(minutes=29))
    assert conta.pode_sincronizar(execucao.finalizada_em + timedelta(minutes=31))


def test_bug_nosso_e_registrado_sem_detalhe_e_sobe(conta):
    cliente = ClienteFalso()
    cliente.erro_em = ('contas', KeyError('987654,32 SENTINELA'))
    with pytest.raises(KeyError):
        rodar(conta, cliente)
    execucao = Execucao.objects.da_empresa(TATICA).get()
    assert execucao.situacao == Execucao.Situacao.ERRO
    assert 'SENTINELA' not in execucao.erro_detalhe


# --- reconciliação e exclusões -----------------------------------------------

def test_reconciliacao_apaga_o_que_sumiu_do_omie(conta):
    rodar(conta, ClienteFalso(movimentos=[titulo(1), titulo(2, grupo='PREVISAO_CONTRATO')]), agora=DIA1)
    # a previsão 2 foi faturada: sumiu do Omie
    execucao = rodar(conta, ClienteFalso(movimentos=[titulo(1)]), agora=DIA2, reconciliacao=True)
    assert set(Titulo.objects.da_empresa(TATICA).values_list('codigo_titulo', flat=True)) == {1}
    assert execucao.contagens['excluidos'] == {'titulos': 1}


def test_incremental_nunca_apaga(conta):
    rodar(conta, ClienteFalso(movimentos=[titulo(1), titulo(2)]), agora=DIA1)
    rodar(conta, ClienteFalso(movimentos=[titulo(1)]), agora=DIA2)
    assert Titulo.objects.da_empresa(TATICA).count() == 2


def test_reconciliacao_nao_apaga_em_massa(conta):
    """Leitura que volta quase vazia é suspeita: não apaga, registra."""
    rodar(conta, ClienteFalso(movimentos=[titulo(i) for i in range(1, 101)]), agora=DIA1)
    execucao = rodar(conta, ClienteFalso(movimentos=[titulo(1)]), agora=DIA2, reconciliacao=True)
    assert Titulo.objects.da_empresa(TATICA).count() == 100
    assert execucao.contagens['exclusoes_bloqueadas'] == {'titulos': 99}


def test_reconciliacao_com_erro_nao_apaga_nada(conta):
    rodar(conta, ClienteFalso(movimentos=[titulo(1), titulo(2)]), agora=DIA1)
    cliente = ClienteFalso(movimentos=[titulo(1)])
    cliente.erro_em = ('saldo', erros.ErroTransitorio('Omie respondeu HTTP 502.'))
    rodar(conta, cliente, agora=DIA2, reconciliacao=True)
    assert Titulo.objects.da_empresa(TATICA).count() == 2


# --- multiempresa ------------------------------------------------------------

def test_cada_empresa_grava_so_o_seu_e_a_reconciliacao_nao_apaga_a_outra(conta):
    outra = _conta(CLIENTE)
    rodar(outra, ClienteFalso(movimentos=[titulo(1), titulo(5)]), agora=DIA1)
    rodar(conta, ClienteFalso(movimentos=[titulo(1)]), agora=DIA1)
    rodar(conta, ClienteFalso(movimentos=[titulo(1)]), agora=DIA2, reconciliacao=True)
    assert set(Titulo.objects.da_empresa(CLIENTE).values_list('codigo_titulo', flat=True)) == {1, 5}
    assert Titulo.objects.da_empresa(TATICA).count() == 1
    assert Execucao.objects.da_empresa(CLIENTE).count() == 1


def test_ponto_de_corte_e_por_empresa(conta):
    outra = _conta(CLIENTE)
    rodar(outra, ClienteFalso(), agora=DIA1)
    execucao = rodar(conta, ClienteFalso(), agora=DIA2)
    assert execucao.tipo == Execucao.Tipo.RECONCILIACAO  # a Tática ainda não tinha corte


# --- lock --------------------------------------------------------------------

def test_empresa_em_andamento_em_outra_sessao_e_pulada(conta):
    outra_sessao = connections['default'].copy()
    chave = (NAMESPACE_LOCK << 32) | TATICA
    try:
        with outra_sessao.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_lock(%s)', [chave])
        assert rodar(conta, ClienteFalso()) is None
        assert Execucao.objects.da_empresa(TATICA).count() == 0
        with outra_sessao.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_unlock(%s)', [chave])
        assert rodar(conta, ClienteFalso()) is not None
    finally:
        outra_sessao.close()


# --- comando -----------------------------------------------------------------

def _comando(*args, cliente=None):
    saida = StringIO()
    with mock.patch('apps.omie.sincronizacao.ClienteOmie.da_conta', return_value=cliente or ClienteFalso()):
        call_command('sincronizar_omie', *args, stdout=saida, stderr=StringIO())
    return saida.getvalue()


def test_comando_pula_desativada_suspensa_e_pausada():
    ativa = _conta(1)
    ContaOmie.objects.create(empresa_id=2, app_key_cifrada='x', app_secret_cifrado='x', ativa=False)
    _conta(3)
    ContaOmie.objects.filter(empresa_id=3).update(suspensa_em=DIA1)
    _conta(4)
    ContaOmie.objects.filter(empresa_id=4).update(pausada_ate=DIA2 + timedelta(days=365))
    saida = _comando()
    assert 'empresa 1: reconciliacao ok' in saida
    assert 'empresa 2: pulada (desativada)' in saida
    assert 'empresa 3: pulada (suspensa' in saida
    assert 'empresa 4: pulada (pausada' in saida
    assert Execucao.objects.count() == 1 and Execucao.objects.get().empresa_id == ativa.empresa_id


def test_comando_saida_sem_valores():
    _conta(1)
    cliente = ClienteFalso(
        movimentos=[titulo(424242, nValorTitulo=987654.32)],
        clientes=[{'codigo_cliente_omie': 77, 'razao_social': 'RAZAO-SENTINELA', 'cnpj_cpf': '12345678000199'}],
    )
    saida = _comando(cliente=cliente)
    for valor in ('424242', '987654', 'RAZAO-SENTINELA', '12345678000199'):
        assert valor not in saida, valor


def test_comando_falha_com_bug_mas_segue_para_as_outras():
    _conta(1)
    _conta(2)
    quebrado = ClienteFalso()
    quebrado.erro_em = ('contas', RuntimeError('bug'))
    with mock.patch('apps.omie.sincronizacao.ClienteOmie.da_conta', side_effect=[quebrado, ClienteFalso()]):
        with pytest.raises(CommandError, match='1 empresa'):
            call_command('sincronizar_omie', stdout=StringIO(), stderr=StringIO())
    assert Execucao.objects.da_empresa(2).get().situacao == Execucao.Situacao.SUCESSO


def test_comando_empresa_sem_conta():
    with pytest.raises(CommandError):
        call_command('sincronizar_omie', '--empresa-id', '99', stdout=StringIO())
