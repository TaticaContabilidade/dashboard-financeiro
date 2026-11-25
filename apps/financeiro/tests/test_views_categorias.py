"""
Categorias, fluxo por categoria e filtro `?categoria=` (D-41, D-42).
Respostas lidas como JSON de verdade.
"""

from datetime import date
from decimal import Decimal

import pytest

from apps.acesso.tests.perfis import CLIENTE, SEM_ACESSO, TATICA
from apps.financeiro.models import Categoria, LancamentoConta, Natureza, Titulo

pytestmark = pytest.mark.django_db

POR_CATEGORIA = '/api/financeiro/fluxo-de-caixa/por-categoria/'
CATEGORIAS = '/api/financeiro/categorias/'
NOVEMBRO = 'data_de=2026-11-01&data_ate=2026-11-30'


def categoria(codigo, descricao, empresa_id=TATICA):
    superior = codigo.rsplit('.', 1)[0]
    return Categoria.objects.create(
        empresa_id=empresa_id, codigo=codigo, descricao=descricao, codigo_superior=superior,
        receita=codigo.startswith('1.'), despesa=codigo.startswith('2.'),
    )


def lancamento(categoria, valor, natureza=Natureza.PAGAR, empresa_id=TATICA, dia=date(2026, 11, 10), movimento=None,
               **kwargs):
    lancamento.contador += 1
    return LancamentoConta.objects.create(
        empresa_id=empresa_id, natureza=natureza, codigo_movimento=movimento or lancamento.contador,
        codigo_baixa=0, codigo_categoria=categoria, valor=Decimal(valor), data_pagamento=dia, **kwargs,
    )


lancamento.contador = 0


def titulo(categoria, valor, tipo=Titulo.Tipo.PAGAR, aberto=None, codigo=None, empresa_id=TATICA, **kwargs):
    titulo.contador += 1
    natureza = Natureza.PAGAR if tipo == Titulo.Tipo.PAGAR else Natureza.RECEBER
    return Titulo.objects.create(
        empresa_id=empresa_id, tipo=tipo, natureza=natureza, codigo_titulo=codigo or titulo.contador,
        codigo_categoria=categoria, valor_titulo=Decimal(valor),
        valor_aberto=Decimal(aberto if aberto is not None else valor) if tipo != Titulo.Tipo.PREVISAO else None,
        data_vencimento=date(2026, 11, 20), **kwargs,
    )


titulo.contador = 0


@pytest.fixture
def plano():
    categoria('1.01', '1.1 - Serviços Prestados ')
    categoria('1.01.01', 'Honorários')
    categoria('2.03', '3.1 - Despesas com Pessoal')
    categoria('2.03.01', 'Salários')
    categoria('2.03.02', 'Encargos')
    categoria('2.04', '3.2 - Despesas Administrativas')
    categoria('2.04.01', 'Aluguel')
    categoria('0.01', 'Transferência')
    categoria('0.01.01', 'Transferência entre contas')


# --- acesso e isolamento ---------------------------------------------------------


@pytest.mark.parametrize('url', [POR_CATEGORIA, CATEGORIAS])
@pytest.mark.parametrize('perfil', SEM_ACESSO)
def test_quem_nao_e_do_financeiro_recebe_403(cliente_com_token, url, perfil):
    assert cliente_com_token(perfil).get(url).status_code == 403


@pytest.mark.parametrize('url', [POR_CATEGORIA, CATEGORIAS])
def test_pedir_outra_empresa_recebe_403(cliente_com_token, url):
    assert cliente_com_token('gerente_financeiro_cliente').get(f'{url}?empresa_id={TATICA}').status_code == 403


def test_por_categoria_nao_vaza_entre_empresas(cliente_com_token):
    categoria('2.03', 'Pessoal da Tática')
    categoria('2.03.01', 'Salários da Tática')
    categoria('2.03', 'Pessoal do cliente', empresa_id=CLIENTE)
    categoria('2.03.01', 'Salários do cliente', empresa_id=CLIENTE)
    categoria('2.04', 'Administrativas da Tática')
    categoria('2.04.01', 'Aluguel da Tática')
    lancamento('2.03.01', '9999.00')
    lancamento('2.03.01', '50.00', empresa_id=CLIENTE)
    lancamento('2.04.01', '10.00', empresa_id=CLIENTE)  # o cliente não tem 2.04 cadastrada

    dados = cliente_com_token('colaborador_financeiro_cliente').get(f'{POR_CATEGORIA}?{NOVEMBRO}').json()

    assert dados['total'] == '60.00'
    assert [(c['descricao'], c['subcategorias'][0]['descricao']) for c in dados['categorias']] == [
        ('Pessoal do cliente', 'Salários do cliente'),
        ('Categoria não cadastrada', 'Categoria não cadastrada'),  # nunca o nome da Tática
    ]


def test_categorias_nao_vaza_entre_empresas(cliente_com_token):
    categoria('2.03', 'Da Tática')
    categoria('2.04', 'Do cliente', empresa_id=CLIENTE)

    dados = cliente_com_token('colaborador_financeiro_cliente').get(CATEGORIAS).json()

    assert [c['descricao'] for c in dados['categorias']] == ['Do cliente']


# --- fluxo por categoria ---------------------------------------------------------


def test_despesas_realizadas_em_dois_niveis_do_maior_para_o_menor(cliente_com_token, plano):
    lancamento('2.03.01', '600.00')
    lancamento('2.03.02', '150.00')
    lancamento('2.04.01', '250.00')
    lancamento('1.01.01', '5000.00', natureza=Natureza.RECEBER)  # receita: fora das despesas

    dados = cliente_com_token('gerente_financeiro').get(f'{POR_CATEGORIA}?{NOVEMBRO}').json()

    assert (dados['tipo'], dados['base'], dados['total']) == ('despesas', 'realizado', '1000.00')
    assert dados['categorias'] == [
        {
            'codigo': '2.03', 'descricao': '3.1 - Despesas com Pessoal', 'total': '750.00', 'percentual': '75.00',
            'subcategorias': [
                {'codigo': '2.03.01', 'descricao': 'Salários', 'total': '600.00', 'percentual': '60.00'},
                {'codigo': '2.03.02', 'descricao': 'Encargos', 'total': '150.00', 'percentual': '15.00'},
            ],
        },
        {
            'codigo': '2.04', 'descricao': '3.2 - Despesas Administrativas', 'total': '250.00', 'percentual': '25.00',
            'subcategorias': [
                {'codigo': '2.04.01', 'descricao': 'Aluguel', 'total': '250.00', 'percentual': '25.00'},
            ],
        },
    ]


def test_estorno_e_descontado_e_transferencia_fica_fora(cliente_com_token, plano):
    lancamento('2.03.01', '600.00')
    lancamento('2.03.01', '100.00', natureza=Natureza.RECEBER)   # estorno de salário
    lancamento('0.01.01', '7777.00')                             # transferência saindo (sem a volta no período)

    dados = cliente_com_token('gerente_financeiro').get(f'{POR_CATEGORIA}?{NOVEMBRO}').json()

    assert dados['total'] == '500.00'
    assert [c['codigo'] for c in dados['categorias']] == ['2.03']


def test_receitas(cliente_com_token, plano):
    lancamento('1.01.01', '3000.00', natureza=Natureza.RECEBER)
    lancamento('2.03.01', '600.00')

    dados = cliente_com_token('gerente_financeiro').get(f'{POR_CATEGORIA}?{NOVEMBRO}&tipo=receitas').json()

    assert dados['total'] == '3000.00'
    assert dados['categorias'][0]['descricao'] == '1.1 - Serviços Prestados'  # espaço sobrando do Omie, removido


def test_previsto_usa_o_valor_em_aberto_e_as_previsoes(cliente_com_token, plano):
    titulo('1.01.01', '1000.00', tipo=Titulo.Tipo.RECEBER, aberto='600.00')
    titulo('1.01.01', '400.00', tipo=Titulo.Tipo.PREVISAO)
    titulo('1.01.01', '999.00', tipo=Titulo.Tipo.RECEBER, liquidado=True, aberto='0')
    titulo('1.01.01', '888.00', tipo=Titulo.Tipo.RECEBER, status='CANCELADO')
    lancamento('1.01.01', '5000.00', natureza=Natureza.RECEBER)  # realizado: fora do previsto

    dados = cliente_com_token('gerente_financeiro').get(
        f'{POR_CATEGORIA}?{NOVEMBRO}&tipo=receitas&base=previsto').json()

    assert dados['total'] == '1000.00'


def test_categoria_apagada_do_omie_aparece_como_nao_cadastrada(cliente_com_token, plano):
    lancamento('2.09.01', '100.00')

    categorias = cliente_com_token('gerente_financeiro').get(f'{POR_CATEGORIA}?{NOVEMBRO}').json()['categorias']

    assert categorias[0]['descricao'] == 'Categoria não cadastrada'
    assert categorias[0]['subcategorias'][0]['descricao'] == 'Categoria não cadastrada'


def test_periodo_sem_lancamentos(cliente_com_token, plano):
    dados = cliente_com_token('gerente_financeiro').get(f'{POR_CATEGORIA}?{NOVEMBRO}').json()

    assert (dados['total'], dados['categorias']) == ('0.00', [])


@pytest.mark.parametrize('consulta, campo', [
    ('tipo=transferencias', 'tipo'),
    ('base=futuro', 'base'),
    ('data_de=ontem', 'data_de'),
])
def test_por_categoria_parametro_invalido_e_400(cliente_com_token, consulta, campo):
    resp = cliente_com_token('gerente_financeiro').get(f'{POR_CATEGORIA}?{consulta}')

    assert resp.status_code == 400
    assert campo in resp.json()


# --- árvore de categorias --------------------------------------------------------


def test_arvore_de_categorias(cliente_com_token, plano):
    dados = cliente_com_token('gerente_financeiro').get(CATEGORIAS).json()

    assert [(c['codigo'], c['tipo'], [s['codigo'] for s in c['subcategorias']]) for c in dados['categorias']] == [
        ('0.01', 'transferencia', ['0.01.01']),
        ('1.01', 'receita', ['1.01.01']),
        ('2.03', 'despesa', ['2.03.01', '2.03.02']),
        ('2.04', 'despesa', ['2.04.01']),
    ]


# --- filtro ?categoria= ---------------------------------------------------------


def test_filtro_de_categoria_nas_contas_a_pagar(cliente_com_token, plano):
    titulo('2.03.01', '100.00', codigo=1)
    titulo('2.03.02', '100.00', codigo=2)
    titulo('2.04.01', '100.00', codigo=3)
    titulo('2.030.01', '100.00', codigo=4)  # parecido com 2.03, mas não é do grupo
    cliente = cliente_com_token('gerente_financeiro')

    def codigos(filtro):
        return [t['codigo_titulo'] for t in cliente.get(f'/api/financeiro/contas-a-pagar/?categoria={filtro}').json()['results']]

    assert codigos('2.03') == [1, 2]            # o grupo traz as subcategorias
    assert codigos('2.03.02') == [2]
    assert codigos('2.03.02,2.04') == [2, 3]


def test_filtro_de_categoria_no_fluxo(cliente_com_token, plano):
    lancamento('2.03.01', '600.00')
    lancamento('2.04.01', '250.00')
    titulo('2.04.01', '70.00')

    totais = cliente_com_token('gerente_financeiro').get(
        f'/api/financeiro/fluxo-de-caixa/?{NOVEMBRO}&categoria=2.04').json()['totais']

    assert totais['realizado']['saidas'] == '250.00'
    assert totais['previsto']['saidas'] == '70.00'


@pytest.mark.parametrize('valor', ['abc', '2.03;DROP', '2..03', ','])
def test_filtro_de_categoria_invalido_e_400(cliente_com_token, valor):
    resp = cliente_com_token('gerente_financeiro').get(f'/api/financeiro/contas-a-pagar/?categoria={valor}')

    assert resp.status_code == 400
    assert 'categoria' in resp.json()


# --- contas fora do fluxo (D-43) ------------------------------------------------


@pytest.fixture
def contas():
    from apps.financeiro.models import ContaCorrente
    ContaCorrente.objects.create(empresa_id=TATICA, codigo=1, descricao='Principal', fora_do_fluxo=False)
    ContaCorrente.objects.create(empresa_id=TATICA, codigo=2, descricao='Aplicação', fora_do_fluxo=True)
    # A mesma conta 1 é "fora do fluxo" na outra empresa: não pode afetar a Tática.
    ContaCorrente.objects.create(empresa_id=CLIENTE, codigo=1, descricao='Do cliente', fora_do_fluxo=True)


FLUXO = '/api/financeiro/fluxo-de-caixa/'


def test_fluxo_deixa_contas_fora_do_fluxo_de_fora_por_padrao(cliente_com_token, plano, contas):
    lancamento('2.03.01', '100.00', codigo_conta_corrente=1)
    lancamento('2.03.01', '900.00', codigo_conta_corrente=2)       # conta fora do fluxo
    lancamento('2.03.01', '5.00')                                  # sem conta: continua
    titulo('2.03.01', '40.00', codigo_conta_corrente=1)
    titulo('2.03.01', '400.00', codigo_conta_corrente=2)           # previsto também (D-43)
    titulo('2.03.01', '4.00')                                      # sem conta definida: continua
    cliente = cliente_com_token('gerente_financeiro')

    padrao = cliente.get(f'{FLUXO}?{NOVEMBRO}').json()
    assert padrao['incluir_fora_do_fluxo'] is False
    assert padrao['totais']['realizado']['saidas'] == '105.00'
    assert padrao['totais']['previsto']['saidas'] == '44.00'

    tudo = cliente.get(f'{FLUXO}?{NOVEMBRO}&incluir_fora_do_fluxo=true').json()
    assert tudo['incluir_fora_do_fluxo'] is True
    assert tudo['totais']['realizado']['saidas'] == '1005.00'
    assert tudo['totais']['previsto']['saidas'] == '444.00'


def test_previsao_em_conta_fora_do_fluxo_fica_de_fora(cliente_com_token, plano, contas):
    titulo('1.01.01', '300.00', tipo=Titulo.Tipo.PREVISAO, codigo_conta_corrente=2)
    titulo('1.01.01', '30.00', tipo=Titulo.Tipo.PREVISAO, codigo_conta_corrente=1)

    totais = cliente_com_token('gerente_financeiro').get(f'{FLUXO}?{NOVEMBRO}').json()['totais']

    assert totais['previsto']['entradas'] == '30.00'


def test_por_categoria_segue_a_mesma_regra(cliente_com_token, plano, contas):
    lancamento('2.03.01', '100.00', codigo_conta_corrente=1)
    lancamento('2.04.01', '900.00', codigo_conta_corrente=2)
    cliente = cliente_com_token('gerente_financeiro')

    padrao = cliente.get(f'{POR_CATEGORIA}?{NOVEMBRO}').json()
    assert (padrao['total'], [c['codigo'] for c in padrao['categorias']]) == ('100.00', ['2.03'])

    tudo = cliente.get(f'{POR_CATEGORIA}?{NOVEMBRO}&incluir_fora_do_fluxo=true').json()
    assert tudo['total'] == '1000.00'


@pytest.mark.parametrize('url', [FLUXO, POR_CATEGORIA])
@pytest.mark.parametrize('valor', ['sim', '1', 'True'])
def test_incluir_fora_do_fluxo_invalido_e_400(cliente_com_token, url, valor):
    resp = cliente_com_token('gerente_financeiro').get(f'{url}?incluir_fora_do_fluxo={valor}')

    assert resp.status_code == 400
    assert 'incluir_fora_do_fluxo' in resp.json()
