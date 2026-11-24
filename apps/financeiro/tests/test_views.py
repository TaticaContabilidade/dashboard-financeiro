"""
Endpoints da API do dashboard financeiro (RF-01 a RF-04, D-32, D-40).

As respostas são lidas como JSON de verdade (`resp.json()`), não por
`resp.data`: é no JSON que um Decimal viraria float.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.acesso.tests.perfis import CLIENTE, SEM_ACESSO, TATICA
from apps.financeiro.models import (
    Categoria,
    ClienteFornecedor,
    ContaCorrente,
    LancamentoConta,
    Natureza,
    SaldoConta,
    Titulo,
)
from apps.omie.models import ExecucaoSincronizacao

pytestmark = pytest.mark.django_db

PAGAR = '/api/financeiro/contas-a-pagar/'
RECEBER = '/api/financeiro/contas-a-receber/'
FLUXO = '/api/financeiro/fluxo-de-caixa/'
ATUALIZACAO = '/api/financeiro/ultima-atualizacao/'
SALDOS = '/api/financeiro/saldos/'
TODOS = [PAGAR, RECEBER, FLUXO, SALDOS, ATUALIZACAO]

NOVEMBRO = '?data_de=2026-11-01&data_ate=2026-11-30'


def criar_titulo(empresa_id=TATICA, tipo=Titulo.Tipo.PAGAR, codigo=1, valor='100.00',
                 vencimento=None, liquidado=False, aberto=None, **kwargs):
    natureza = Natureza.PAGAR if tipo == Titulo.Tipo.PAGAR else Natureza.RECEBER
    if aberto is None and not liquidado and tipo != Titulo.Tipo.PREVISAO:
        aberto = valor
    return Titulo.objects.create(
        empresa_id=empresa_id,
        tipo=tipo,
        natureza=natureza,
        codigo_titulo=codigo,
        valor_titulo=Decimal(valor),
        valor_aberto=Decimal(aberto) if aberto is not None else None,
        data_vencimento=vencimento or timezone.localdate() + timedelta(days=10),
        liquidado=liquidado,
        **kwargs,
    )


def criar_lancamento(empresa_id=TATICA, natureza=Natureza.PAGAR, movimento=1, baixa=1,
                     valor='100.00', data_pagamento=None, **kwargs):
    return LancamentoConta.objects.create(
        empresa_id=empresa_id,
        natureza=natureza,
        codigo_movimento=movimento,
        codigo_baixa=baixa,
        valor=Decimal(valor),
        data_pagamento=data_pagamento or date(2026, 11, 20),
        **kwargs,
    )


def criar_execucao(empresa_id=TATICA, situacao=ExecucaoSincronizacao.Situacao.SUCESSO, finalizada_em=None):
    return ExecucaoSincronizacao.objects.create(
        empresa_id=empresa_id,
        tipo=ExecucaoSincronizacao.Tipo.INCREMENTAL,
        situacao=situacao,
        finalizada_em=finalizada_em or timezone.now(),
    )


# --- acesso e isolamento entre empresas (D-32) ----------------------------------


@pytest.mark.parametrize('url', TODOS)
@pytest.mark.parametrize('perfil', SEM_ACESSO)
def test_quem_nao_e_do_financeiro_recebe_403(cliente_com_token, url, perfil):
    assert cliente_com_token(perfil).get(url).status_code == 403


@pytest.mark.parametrize('url', TODOS)
def test_sem_token_recebe_401(cliente, url):
    assert cliente.get(url).status_code == 401


@pytest.mark.parametrize('url', TODOS)
def test_pedir_outra_empresa_recebe_403(cliente_com_token, url):
    resp = cliente_com_token('gerente_financeiro_cliente').get(f'{url}?empresa_id={TATICA}')
    assert resp.status_code == 403


def test_contas_a_pagar_nao_vaza_entre_empresas(cliente_com_token):
    criar_titulo(empresa_id=TATICA, codigo=1, valor='500.00')
    criar_titulo(empresa_id=CLIENTE, codigo=2, valor='300.00')

    dados = cliente_com_token('colaborador_financeiro_cliente').get(PAGAR).json()

    assert [t['codigo_titulo'] for t in dados['results']] == [2]
    assert dados['resumo']['a_vencer'] == {'quantidade': 1, 'total': '300.00'}


def test_contas_a_receber_nao_vaza_entre_empresas(cliente_com_token):
    criar_titulo(empresa_id=TATICA, tipo=Titulo.Tipo.RECEBER, codigo=1, valor='500.00')
    criar_titulo(empresa_id=CLIENTE, tipo=Titulo.Tipo.RECEBER, codigo=2, valor='300.00')

    dados = cliente_com_token('colaborador_financeiro_cliente').get(RECEBER).json()

    assert [t['codigo_titulo'] for t in dados['results']] == [2]
    assert dados['resumo']['a_vencer']['total'] == '300.00'


def test_nome_do_fornecedor_vem_so_da_propria_empresa(cliente_com_token):
    """Mesmo código de fornecedor nas duas empresas: o nome tem que ser o da
    empresa do título, nunca o da outra."""
    ClienteFornecedor.objects.create(empresa_id=TATICA, codigo=10, razao_social='Da Tática')
    ClienteFornecedor.objects.create(empresa_id=CLIENTE, codigo=10, razao_social='Do Cliente')
    criar_titulo(empresa_id=CLIENTE, codigo=1, codigo_cliente=10)

    dados = cliente_com_token('colaborador_financeiro_cliente').get(PAGAR).json()

    assert dados['results'][0]['nome_cliente_fornecedor'] == 'Do Cliente'


def test_fluxo_de_caixa_nao_vaza_entre_empresas(cliente_com_token):
    criar_lancamento(empresa_id=TATICA, valor='1000.00', data_pagamento=date(2026, 11, 5))
    criar_lancamento(empresa_id=CLIENTE, valor='50.00', data_pagamento=date(2026, 11, 5))
    criar_titulo(empresa_id=TATICA, codigo=1, valor='999.00')

    dados = cliente_com_token('colaborador_financeiro_cliente').get(FLUXO + NOVEMBRO).json()

    assert dados['totais']['realizado']['saidas'] == '50.00'
    assert dados['totais']['previsto']['saidas'] == '0.00'


def test_ultima_atualizacao_nao_vaza_entre_empresas(cliente_com_token):
    criar_execucao(empresa_id=TATICA)

    dados = cliente_com_token('colaborador_financeiro_cliente').get(ATUALIZACAO).json()

    assert dados == {'ultima_sincronizacao': None, 'atualizado_ha': None}


def test_adm_escolhe_a_empresa(cliente_com_token):
    criar_titulo(empresa_id=CLIENTE, codigo=7)

    dados = cliente_com_token('adm').get(f'{PAGAR}?empresa_id={CLIENTE}').json()

    assert [t['codigo_titulo'] for t in dados['results']] == [7]


# --- contas a pagar / a receber (RF-01, RF-02) ----------------------------------


def test_contas_a_pagar_lista_situacoes_e_resumo(cliente_com_token):
    hoje = timezone.localdate()
    ClienteFornecedor.objects.create(empresa_id=TATICA, codigo=10, razao_social='Fornecedor Alpha')
    Categoria.objects.create(empresa_id=TATICA, codigo='1.01', descricao='Despesas Operacionais')
    criar_titulo(codigo=1, valor='150.00', vencimento=hoje + timedelta(days=5),
                 codigo_cliente=10, codigo_categoria='1.01')
    criar_titulo(codigo=2, valor='200.00', vencimento=hoje - timedelta(days=5))
    criar_titulo(codigo=3, valor='300.00', vencimento=hoje - timedelta(days=2),
                 liquidado=True, valor_pago=Decimal('310.00'))

    dados = cliente_com_token('gerente_financeiro').get(PAGAR).json()

    assert dados['count'] == 3
    por_codigo = {t['codigo_titulo']: t for t in dados['results']}
    assert por_codigo[1]['nome_cliente_fornecedor'] == 'Fornecedor Alpha'
    assert por_codigo[1]['descricao_categoria'] == 'Despesas Operacionais'
    assert [por_codigo[c]['situacao'] for c in (1, 2, 3)] == ['a_vencer', 'vencido', 'pago']
    assert por_codigo[1]['valor_titulo'] == '150.00'
    assert dados['resumo'] == {
        'a_vencer': {'quantidade': 1, 'total': '150.00'},
        'vencido': {'quantidade': 1, 'total': '200.00'},
        'pago': {'quantidade': 1, 'total': '310.00'},  # o que foi pago (com juros), não o valor do título
    }


def test_resumo_soma_o_que_falta_pagar_na_baixa_parcial(cliente_com_token):
    """Título de 1.000 com 400 já pagos: em aberto são 600, não 1.000."""
    criar_titulo(codigo=1, valor='1000.00', aberto='600.00', valor_pago=Decimal('400.00'),
                 vencimento=timezone.localdate() - timedelta(days=1), status='PAGTO PARCIAL')

    resumo = cliente_com_token('gerente_financeiro').get(PAGAR).json()['resumo']

    assert resumo['vencido'] == {'quantidade': 1, 'total': '600.00'}


def test_cancelado_nao_aparece_nem_entra_no_resumo(cliente_com_token):
    """O Omie manda o cancelado com cLiquidado = S: sem o filtro, ele
    apareceria como pago."""
    criar_titulo(codigo=1, valor='100.00')
    criar_titulo(codigo=2, valor='500.00', liquidado=True, status='CANCELADO')

    dados = cliente_com_token('gerente_financeiro').get(PAGAR).json()

    assert [t['codigo_titulo'] for t in dados['results']] == [1]
    assert dados['resumo']['pago'] == {'quantidade': 0, 'total': '0.00'}


def test_resumo_soma_todos_os_filtrados_nao_so_a_pagina(cliente_com_token):
    for codigo in range(1, 26):
        criar_titulo(codigo=codigo, valor='10.00')

    dados = cliente_com_token('gerente_financeiro').get(f'{PAGAR}?page=2').json()

    assert len(dados['results']) == 5
    assert dados['resumo']['a_vencer'] == {'quantidade': 25, 'total': '250.00'}


def test_contas_a_pagar_filtros(cliente_com_token):
    hoje = timezone.localdate()
    criar_titulo(codigo=1, vencimento=hoje - timedelta(days=10))
    criar_titulo(codigo=2, vencimento=hoje + timedelta(days=5))
    criar_titulo(codigo=3, vencimento=hoje + timedelta(days=20), liquidado=True)
    cliente = cliente_com_token('gerente_financeiro')

    vencidos = cliente.get(f'{PAGAR}?situacao=vencido').json()
    assert [t['codigo_titulo'] for t in vencidos['results']] == [1]

    periodo = cliente.get(f'{PAGAR}?data_de={hoje}&data_ate={hoje + timedelta(days=10)}').json()
    assert [t['codigo_titulo'] for t in periodo['results']] == [2]


def test_contas_a_receber_so_traz_titulos_a_receber(cliente_com_token):
    criar_titulo(tipo=Titulo.Tipo.PAGAR, codigo=1)
    criar_titulo(tipo=Titulo.Tipo.RECEBER, codigo=2)
    criar_titulo(tipo=Titulo.Tipo.PREVISAO, codigo=3)

    dados = cliente_com_token('gerente_financeiro').get(RECEBER).json()

    assert [t['codigo_titulo'] for t in dados['results']] == [2]


@pytest.mark.parametrize('consulta, campo', [
    ('data_de=abc', 'data_de'),
    ('data_ate=20/11/2026', 'data_ate'),
    ('data_de=2026-11-30&data_ate=2026-11-01', 'data_ate'),
    ('situacao=atrasado', 'situacao'),
])
def test_contas_parametro_invalido_e_400(cliente_com_token, consulta, campo):
    resp = cliente_com_token('gerente_financeiro').get(f'{PAGAR}?{consulta}')

    assert resp.status_code == 400
    assert campo in resp.json()


# --- fluxo de caixa (RF-03) ------------------------------------------------------


def test_fluxo_realizado_e_previsto(cliente_com_token):
    criar_lancamento(natureza=Natureza.RECEBER, movimento=1, valor='1000.00', data_pagamento=date(2026, 11, 5))
    criar_lancamento(natureza=Natureza.PAGAR, movimento=2, valor='400.00', data_pagamento=date(2026, 11, 10),
                     valor_movimento=Decimal('9999.00'))  # total do lote: nunca somado
    criar_titulo(tipo=Titulo.Tipo.RECEBER, codigo=10, valor='2000.00', vencimento=date(2026, 11, 15))
    criar_titulo(tipo=Titulo.Tipo.PAGAR, codigo=11, valor='700.00', vencimento=date(2026, 11, 20))
    criar_titulo(tipo=Titulo.Tipo.RECEBER, codigo=12, valor='999.00', vencimento=date(2026, 11, 25), liquidado=True)
    criar_titulo(tipo=Titulo.Tipo.PREVISAO, codigo=13, valor='300.00', vencimento=date(2026, 11, 28))

    dados = cliente_com_token('gerente_financeiro').get(FLUXO + NOVEMBRO + '&granularidade=mes').json()

    assert dados['totais'] == {
        'realizado': {'entradas': '1000.00', 'saidas': '400.00', 'saldo': '600.00'},
        'previsto': {'entradas': '2300.00', 'saidas': '700.00', 'saldo': '1600.00'},
    }
    assert [(p['inicio'], p['fim']) for p in dados['periodos']] == [('2026-11-01', '2026-11-30')]


def test_fluxo_baixa_parcial_nao_conta_duas_vezes(cliente_com_token):
    """Título de 1.000 com baixa de 400 no mês: 400 realizado + 600 previsto."""
    criar_titulo(codigo=1, valor='1000.00', aberto='600.00', valor_pago=Decimal('400.00'),
                 vencimento=date(2026, 11, 20), status='PAGTO PARCIAL')
    criar_lancamento(codigo_titulo=1, valor='400.00', data_pagamento=date(2026, 11, 10))

    totais = cliente_com_token('gerente_financeiro').get(FLUXO + NOVEMBRO).json()['totais']

    assert totais['realizado']['saidas'] == '400.00'
    assert totais['previsto']['saidas'] == '600.00'


def test_fluxo_ignora_cancelados(cliente_com_token):
    criar_titulo(codigo=1, valor='500.00', status='CANCELADO')

    totais = cliente_com_token('gerente_financeiro').get(FLUXO + NOVEMBRO).json()['totais']

    assert totais['previsto']['saidas'] == '0.00'


def test_fluxo_por_semana(cliente_com_token):
    criar_lancamento(movimento=1, valor='10.00', data_pagamento=date(2026, 11, 2))   # segunda
    criar_lancamento(movimento=2, valor='20.00', data_pagamento=date(2026, 11, 8))   # domingo, mesma semana
    criar_lancamento(movimento=3, valor='30.00', data_pagamento=date(2026, 11, 9))   # semana seguinte

    periodos = cliente_com_token('gerente_financeiro').get(
        FLUXO + NOVEMBRO + '&granularidade=semana').json()['periodos']

    assert [(p['inicio'], p['fim'], p['realizado']['saidas']) for p in periodos] == [
        ('2026-11-02', '2026-11-08', '30.00'),
        ('2026-11-09', '2026-11-15', '30.00'),
    ]


@pytest.mark.parametrize('consulta, campo', [
    ('granularidade=ano', 'granularidade'),
    ('data_de=ontem', 'data_de'),
    ('data_de=2026-12-01&data_ate=2026-11-01', 'data_ate'),
])
def test_fluxo_parametro_invalido_e_400(cliente_com_token, consulta, campo):
    resp = cliente_com_token('gerente_financeiro').get(f'{FLUXO}?{consulta}')

    assert resp.status_code == 400
    assert campo in resp.json()


# --- última atualização (RF-04) --------------------------------------------------


def test_ultima_atualizacao_usa_a_ultima_execucao_com_sucesso(cliente_com_token):
    agora = timezone.now()
    criar_execucao(finalizada_em=agora - timedelta(minutes=15))
    criar_execucao(situacao=ExecucaoSincronizacao.Situacao.ERRO, finalizada_em=agora)

    dados = cliente_com_token('gerente_financeiro').get(ATUALIZACAO).json()

    assert dados['atualizado_ha'] == 'há 15 min'
    assert dados['ultima_sincronizacao'] is not None


def test_ultima_atualizacao_sem_execucoes(cliente_com_token):
    dados = cliente_com_token('gerente_financeiro').get(ATUALIZACAO).json()

    assert dados == {'ultima_sincronizacao': None, 'atualizado_ha': None}


# --- saldo por conta corrente (RF-03) -------------------------------------------


def criar_conta(empresa_id=TATICA, codigo=1, descricao='Banco A', ativa=True, fora_do_fluxo=False):
    return ContaCorrente.objects.create(
        empresa_id=empresa_id, codigo=codigo, descricao=descricao, tipo='CC',
        ativa=ativa, fora_do_fluxo=fora_do_fluxo,
    )


def criar_saldo(empresa_id=TATICA, conta=1, data=date(2026, 11, 24), atual='100.00', **kwargs):
    return SaldoConta.objects.create(
        empresa_id=empresa_id, codigo_conta_corrente=conta, data=data,
        saldo_atual=Decimal(atual) if atual is not None else None, **kwargs,
    )


def test_saldos_por_conta_e_totais(cliente_com_token):
    criar_conta(codigo=1, descricao='Banco A')
    criar_conta(codigo=2, descricao='Caixa', fora_do_fluxo=True)
    criar_saldo(conta=1, atual='1500.50', saldo_disponivel=Decimal('1400.00'))
    criar_saldo(conta=2, atual='200.00')

    dados = cliente_com_token('gerente_financeiro').get(f'{SALDOS}?data=2026-11-24').json()

    assert dados['data'] == '2026-11-24'
    assert [(c['codigo'], c['saldo_atual'], c['fora_do_fluxo']) for c in dados['contas']] == [
        (1, '1500.50', False),
        (2, '200.00', True),
    ]
    assert dados['contas'][0]['saldo_disponivel'] == '1400.00'
    assert dados['contas'][0]['limite_credito'] is None  # não informado pelo Omie: nulo, não zero
    assert dados['totais'] == {'saldo_atual': '1700.50', 'saldo_atual_no_fluxo': '1500.50'}


def test_saldos_usa_o_ultimo_saldo_ate_a_data(cliente_com_token):
    criar_conta(codigo=1)
    criar_saldo(conta=1, data=date(2026, 11, 20), atual='10.00')
    criar_saldo(conta=1, data=date(2026, 11, 22), atual='20.00')
    criar_saldo(conta=1, data=date(2026, 11, 24), atual='30.00')
    cliente = cliente_com_token('gerente_financeiro')

    conta = cliente.get(f'{SALDOS}?data=2026-11-23').json()['contas'][0]
    assert (conta['data_saldo'], conta['saldo_atual']) == ('2026-11-22', '20.00')

    conta = cliente.get(f'{SALDOS}?data=2026-11-24').json()['contas'][0]
    assert (conta['data_saldo'], conta['saldo_atual']) == ('2026-11-24', '30.00')


def test_saldos_conta_ativa_sem_saldo_aparece_vazia_e_inativa_sem_saldo_nao(cliente_com_token):
    criar_conta(codigo=1, descricao='Ativa sem saldo')
    criar_conta(codigo=2, descricao='Inativa sem saldo', ativa=False)
    criar_conta(codigo=3, descricao='Inativa com saldo antigo', ativa=False)
    criar_saldo(conta=3, data=date(2026, 10, 1), atual='5.00')

    contas = cliente_com_token('gerente_financeiro').get(f'{SALDOS}?data=2026-11-24').json()['contas']

    assert [(c['codigo'], c['data_saldo'], c['saldo_atual']) for c in contas] == [
        (1, None, None),
        (3, '2026-10-01', '5.00'),  # inativas vão para o fim
    ]


def test_saldos_nao_vaza_entre_empresas(cliente_com_token):
    criar_conta(empresa_id=TATICA, codigo=1, descricao='Da Tática')
    criar_saldo(empresa_id=TATICA, conta=1, atual='9999.00')
    criar_conta(empresa_id=TATICA, codigo=2, descricao='Outra da Tática')  # ativa, sem saldo
    criar_conta(empresa_id=CLIENTE, codigo=1, descricao='Do cliente')
    criar_saldo(empresa_id=CLIENTE, conta=1, atual='50.00')

    dados = cliente_com_token('colaborador_financeiro_cliente').get(f'{SALDOS}?data=2026-11-24').json()

    assert [(c['descricao'], c['saldo_atual']) for c in dados['contas']] == [('Do cliente', '50.00')]
    assert dados['totais']['saldo_atual'] == '50.00'


def test_saldos_data_invalida_e_400(cliente_com_token):
    resp = cliente_com_token('gerente_financeiro').get(f'{SALDOS}?data=24/11/2026')

    assert resp.status_code == 400
    assert 'data' in resp.json()
