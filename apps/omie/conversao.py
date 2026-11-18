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

from apps.financeiro.models import LancamentoConta, Natureza, Titulo

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
