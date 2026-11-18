"""
Converte um movimento do `ListarMovimentos` para as tabelas do financeiro
(wiki: Integração Omie, "Regras fixadas pelo teste real", D-36).

    convertido = converter_movimento(movimento)
    convertido.modelo   # Titulo ou LancamentoConta
    convertido.chave    # campos da chave única (sem empresa_id)
    convertido.campos   # o resto, pronto para update_or_create(defaults=...)

Funções puras, sem banco: a sincronização decide como gravar. Linha que não
dá para gravar levanta `LinhaIgnorada` com um motivo curto — o motivo nunca
carrega valor do Omie, para poder ir para o log (RNF-04).
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from apps.financeiro.models import (
    Categoria,
    ClienteFornecedor,
    ContaCorrente,
    LancamentoConta,
    Natureza,
    SaldoConta,
    Titulo,
)

CENTAVO = Decimal('0.01')

TIPO_DO_GRUPO = {
    'CONTA_A_PAGAR': Titulo.Tipo.PAGAR,
    'CONTA_A_RECEBER': Titulo.Tipo.RECEBER,
    'PREVISAO_CONTRATO': Titulo.Tipo.PREVISAO,
}
NATUREZA_DO_GRUPO_CONTA = {
    'CONTA_CORRENTE_PAG': Natureza.PAGAR,
    'CONTA_CORRENTE_REC': Natureza.RECEBER,
}


class LinhaIgnorada(Exception):
    """A linha não pode ser gravada. `motivo` é seguro para log."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


@dataclass(frozen=True)
class Convertido:
    modelo: type
    chave: dict
    campos: dict


def converter_movimento(movimento: dict) -> Convertido:
    detalhes = movimento.get('detalhes') or {}
    resumo = movimento.get('resumo') or {}
    grupo = detalhes.get('cGrupo') or ''
    if grupo in TIPO_DO_GRUPO:
        return _titulo(TIPO_DO_GRUPO[grupo], detalhes, resumo)
    if grupo in NATUREZA_DO_GRUPO_CONTA:
        return _lancamento(NATUREZA_DO_GRUPO_CONTA[grupo], detalhes, resumo)
    # cGrupo é um código curto do Omie (não é dado financeiro), mas corta
    # por garantia antes de ir para o motivo.
    raise LinhaIgnorada(f'cGrupo desconhecido: {str(grupo)[:30]!r}')


def _titulo(tipo: str, d: dict, r: dict) -> Convertido:
    codigo = _inteiro(d.get('nCodTitulo'))
    if not codigo:
        raise LinhaIgnorada(f'{tipo} sem nCodTitulo')
    natureza = Natureza.PAGAR if tipo == Titulo.Tipo.PAGAR else Natureza.RECEBER
    return Convertido(
        modelo=Titulo,
        chave={'tipo': tipo, 'codigo_titulo': codigo},
        campos={
            'natureza': natureza,
            'status': _texto(d.get('cStatus'), 30),
            'codigo_cliente': _inteiro(d.get('nCodCliente')),
            'codigo_categoria': _texto(d.get('cCodCateg'), 30),
            'codigo_conta_corrente': _inteiro(d.get('nCodCC')),
            'codigo_contrato': _inteiro(d.get('nCodCtr')),
            'numero_documento': _texto(d.get('cNumTitulo'), 60),
            'numero_parcela': _texto(d.get('cNumParcela'), 30),
            'tipo_documento': _texto(d.get('cTipo'), 20),
            'origem': _texto(d.get('cOrigem'), 10),
            'data_emissao': _data(d.get('dDtEmissao')),
            'data_registro': _data(d.get('dDtRegistro')),
            'data_vencimento': _data(d.get('dDtVenc')),
            'data_previsao': _data(d.get('dDtPrevisao')),
            'data_pagamento': _data(d.get('dDtPagamento')),
            'valor_titulo': _valor(d.get('nValorTitulo')),
            'valor_pago': _valor(r.get('nValPago')),
            'valor_aberto': _valor(r.get('nValAberto')),
            'valor_liquido': _valor(r.get('nValLiquido')),
            'juros': _valor(_primeiro(r.get('nJuros'), d.get('nJuros'))),
            'multa': _valor(_primeiro(r.get('nMulta'), d.get('nMulta'))),
            'desconto': _valor(_primeiro(r.get('nDesconto'), d.get('nDesconto'))),
            'liquidado': r.get('cLiquidado') == 'S',
        },
    )


def _lancamento(natureza: str, d: dict, r: dict) -> Convertido:
    movimento = _inteiro(d.get('nCodMovCC')) or 0
    baixa = _inteiro(d.get('nCodBaixa')) or 0
    if not movimento and not baixa:
        raise LinhaIgnorada('lançamento de conta corrente sem nCodMovCC nem nCodBaixa')
    return Convertido(
        modelo=LancamentoConta,
        chave={'natureza': natureza, 'codigo_movimento': movimento, 'codigo_baixa': baixa},
        campos={
            'codigo_titulo': _inteiro(d.get('nCodTitulo')),
            'status': _texto(d.get('cStatus'), 30),
            'codigo_conta_corrente': _inteiro(d.get('nCodCC')),
            'codigo_cliente': _inteiro(d.get('nCodCliente')),
            'codigo_categoria': _texto(d.get('cCodCateg'), 30),
            'tipo_documento': _texto(d.get('cTipo'), 20),
            'origem': _texto(d.get('cOrigem'), 10),
            'data_pagamento': _data(d.get('dDtPagamento')),
            'data_conciliacao': _data(d.get('dDtConcilia')),
            'data_credito': _data(d.get('dDtCredito')),
            'valor': _valor(r.get('nValPago')),
            'valor_movimento': _valor(d.get('nValorMovCC')),
            'juros': _valor(d.get('nJuros')),
            'multa': _valor(d.get('nMulta')),
            'desconto': _valor(d.get('nDesconto')),
        },
    )


# --- cadastros e saldos -----------------------------------------------------
# Só os campos que as tabelas guardam: CPF/CNPJ, endereço, contato e dados
# bancários do Omie são descartados aqui (D-38).

def converter_conta_corrente(conta: dict) -> Convertido:
    codigo = _inteiro(conta.get('nCodCC'))
    if not codigo:
        raise LinhaIgnorada('conta corrente sem nCodCC')
    return Convertido(
        modelo=ContaCorrente,
        chave={'codigo': codigo},
        campos={
            'descricao': _texto(conta.get('descricao'), 100),
            'tipo': _texto(_primeiro(conta.get('tipo_conta_corrente'), conta.get('tipo')), 30),
            'codigo_banco': _texto(conta.get('codigo_banco'), 10),
            'ativa': conta.get('inativo') != 'S',
            'fora_do_fluxo': conta.get('nao_fluxo') == 'S',
            'saldo_inicial': _valor(conta.get('saldo_inicial')),
            'data_saldo_inicial': _data(conta.get('saldo_data')),
        },
    )


def converter_categoria(categoria: dict) -> Convertido:
    codigo = _texto(categoria.get('codigo'), 30)
    if not codigo:
        raise LinhaIgnorada('categoria sem codigo')
    return Convertido(
        modelo=Categoria,
        chave={'codigo': codigo},
        campos={
            'descricao': _texto(categoria.get('descricao'), 120),
            'codigo_superior': _texto(categoria.get('categoria_superior'), 30),
            'receita': categoria.get('conta_receita') == 'S',
            'despesa': categoria.get('conta_despesa') == 'S',
        },
    )


def converter_cliente(cliente: dict) -> Convertido:
    codigo = _inteiro(cliente.get('codigo_cliente_omie'))
    if not codigo:
        raise LinhaIgnorada('cliente/fornecedor sem codigo_cliente_omie')
    return Convertido(
        modelo=ClienteFornecedor,
        chave={'codigo': codigo},
        campos={
            'razao_social': _texto(cliente.get('razao_social'), 150),
            'nome_fantasia': _texto(cliente.get('nome_fantasia'), 150),
            'ativo': cliente.get('inativo') != 'S',
        },
    )


def converter_saldo(resposta: dict, codigo_conta: int, dia: date) -> Convertido:
    """Resposta do `ListarExtrato` (só saldos) de UMA conta num dia. A conta
    e o dia vêm de quem pediu, não da resposta: é o que foi consultado."""
    if not codigo_conta:
        raise LinhaIgnorada('saldo sem conta corrente')
    return Convertido(
        modelo=SaldoConta,
        chave={'codigo_conta_corrente': int(codigo_conta), 'data': dia},
        campos={
            'saldo_atual': _valor(resposta.get('nSaldoAtual')),
            'saldo_atual_previsto': _valor(resposta.get('nSaldoAtualPrevisto')),
            'saldo_anterior': _valor(resposta.get('nSaldoAnterior')),
            'saldo_conciliado': _valor(resposta.get('nSaldoConciliado')),
            'saldo_disponivel': _valor(resposta.get('nSaldoDisponivel')),
            'saldo_provisorio': _valor(resposta.get('nSaldoProvisorio')),
            'limite_credito': _valor(resposta.get('nLimiteCreditoTotal')),
        },
    )

# --- conversões de campo -----------------------------------------------------

def _valor(bruto) -> Decimal | None:
    """Número JSON (int ou float) -> Decimal com 2 casas. `Decimal(str(x))`
    evita o erro binário do float (0.1 + 0.2). Nulo continua nulo."""
    if bruto is None or bruto == '' or isinstance(bruto, bool):
        return None
    try:
        return Decimal(str(bruto)).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise LinhaIgnorada('valor em formato inválido') from None


def _data(bruto) -> date | None:
    """'dd/mm/aaaa' -> date. Vazio vira None; formato inválido ignora a linha
    (gravar data errada é pior do que não gravar)."""
    if not bruto:
        return None
    try:
        dia, mes, ano = str(bruto).split('/')
        return date(int(ano), int(mes), int(dia))
    except ValueError:
        raise LinhaIgnorada('data em formato inválido') from None


def _inteiro(bruto) -> int | None:
    if bruto is None or bruto == '' or isinstance(bruto, bool):
        return None
    try:
        return int(bruto)
    except (TypeError, ValueError):
        raise LinhaIgnorada('código em formato inválido') from None


def _primeiro(*valores):
    """Juros/multa/desconto vêm no resumo e às vezes também nos detalhes."""
    return next((v for v in valores if v not in (None, '')), None)


def _texto(bruto, limite: int) -> str:
    return '' if bruto is None else str(bruto).strip()[:limite]
