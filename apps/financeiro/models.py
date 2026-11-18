"""
Dados financeiros copiados do Omie (wiki: Banco de Dados; Integração Omie,
"Regras fixadas pelo teste real", D-36 e D-37).

O `ListarMovimentos` do Omie mistura três tipos de linha. Eles vão para
duas tabelas, para cada tela consultar a sua e nunca somar o mesmo
dinheiro duas vezes:

- `Titulo`: a conta a pagar/receber em si, e a previsão de recebimento de
  contrato. Base de contas a pagar/receber e do fluxo PREVISTO.
- `LancamentoConta`: o que passou na conta corrente (baixa de título,
  lançamento direto no extrato, transferência). Base do fluxo REALIZADO.

Um título pago aparece nas duas: como `Titulo` e como `LancamentoConta`
(a baixa). Por isso o realizado nunca soma `Titulo.valor_pago`.

Ids do Omie são guardados sem FK entre as tabelas: a sincronização grava
na ordem em que o Omie devolve, e um lançamento pode chegar antes do
título dele.
"""

from django.db import models
from django.db.models import Exists, OuterRef, Q
from django.db.models.functions import TruncMonth

from apps.core.models import EscopoEmpresaQuerySet, ModeloDaEmpresa

VALOR = {'max_digits': 15, 'decimal_places': 2, 'null': True, 'blank': True}


class Natureza(models.TextChoices):
    PAGAR = 'P', 'a pagar (saída)'
    RECEBER = 'R', 'a receber (entrada)'


class TituloQuerySet(EscopoEmpresaQuerySet):
    def previsoes_validas(self):
        """Previsões de contrato que ainda valem para o fluxo previsto.

        Ao faturar, o Omie troca a previsão pelo título a receber, mas o
        incremental não vê a exclusão: a previsão velha fica no banco até
        a reconciliação diária. Até lá, ela é ignorada se já existir título
        a receber do MESMO contrato, no MESMO mês de vencimento e da MESMA
        empresa (D-36).
        """
        titulo_faturado = (
            Titulo.objects.filter(
                empresa_id=OuterRef('empresa_id'),
                tipo=Titulo.Tipo.RECEBER,
                codigo_contrato=OuterRef('codigo_contrato'),
            )
            .annotate(mes=TruncMonth('data_vencimento'))
            .filter(mes=OuterRef('mes'))
        )
        return (
            self.filter(tipo=Titulo.Tipo.PREVISAO)
            .annotate(mes=TruncMonth('data_vencimento'))
            .exclude(Q(codigo_contrato__isnull=False) & Q(data_vencimento__isnull=False) & Exists(titulo_faturado))
        )


class Titulo(ModeloDaEmpresa):
    """Conta a pagar, conta a receber ou previsão de contrato.

    Chave: empresa + tipo + `codigo_titulo` (`nCodTitulo`), sem colisão no
    teste real (D-36). Valores podem vir nulos do Omie (ex.: previsão sem
    valor) e ficam nulos aqui: nulo é "o Omie não informou", diferente de 0.
    """

    class Tipo(models.TextChoices):
        PAGAR = 'pagar', 'conta a pagar'           # cGrupo CONTA_A_PAGAR
        RECEBER = 'receber', 'conta a receber'     # cGrupo CONTA_A_RECEBER
        PREVISAO = 'previsao', 'previsão de contrato'  # cGrupo PREVISAO_CONTRATO

    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    natureza = models.CharField(max_length=1, choices=Natureza.choices)
    codigo_titulo = models.BigIntegerField(help_text='nCodTitulo do Omie')
    status = models.CharField(max_length=30, blank=True, help_text='cStatus do Omie, como veio (grafia varia)')

    codigo_cliente = models.BigIntegerField(null=True, blank=True, help_text='nCodCliente: fornecedor ou cliente')
    codigo_categoria = models.CharField(max_length=30, blank=True, help_text='cCodCateg')
    codigo_conta_corrente = models.BigIntegerField(null=True, blank=True, help_text='nCodCC')
    codigo_contrato = models.BigIntegerField(null=True, blank=True, help_text='nCodCtr (a receber e previsões)')
    numero_documento = models.CharField(max_length=60, blank=True, help_text='cNumTitulo')
    numero_parcela = models.CharField(max_length=30, blank=True, help_text='cNumParcela')
    tipo_documento = models.CharField(max_length=20, blank=True, help_text='cTipo (BOL, PIX, ...)')
    origem = models.CharField(max_length=10, blank=True, help_text='cOrigem')

    data_emissao = models.DateField(null=True, blank=True)
    data_registro = models.DateField(null=True, blank=True)
    data_vencimento = models.DateField(null=True, blank=True)
    data_previsao = models.DateField(null=True, blank=True)
    data_pagamento = models.DateField(null=True, blank=True)

    valor_titulo = models.DecimalField(**VALOR, help_text='nValorTitulo')
    valor_pago = models.DecimalField(**VALOR, help_text='nValPago — NÃO usar no fluxo realizado')
    valor_aberto = models.DecimalField(**VALOR, help_text='nValAberto')
    valor_liquido = models.DecimalField(**VALOR, help_text='nValLiquido')
    juros = models.DecimalField(**VALOR)
    multa = models.DecimalField(**VALOR)
    desconto = models.DecimalField(**VALOR)
    liquidado = models.BooleanField(default=False, help_text='cLiquidado = S')

    sincronizado_em = models.DateTimeField(auto_now=True)

    objects = TituloQuerySet.as_manager()

    class Meta:
        db_table = 'financeiro_titulos'
        verbose_name = 'título'
        verbose_name_plural = 'títulos'
        constraints = [
            models.UniqueConstraint(
                fields=['empresa_id', 'tipo', 'codigo_titulo'], name='titulo_unico_por_empresa'
            ),
            models.CheckConstraint(condition=Q(codigo_titulo__gt=0), name='titulo_com_codigo'),
        ]
        indexes = [
            models.Index(fields=['empresa_id', 'tipo', 'data_vencimento'], name='titulo_tipo_vencimento'),
            models.Index(fields=['empresa_id', 'codigo_contrato', 'data_vencimento'], name='titulo_contrato_vencimento'),
        ]

    def __str__(self) -> str:
        return f'{self.get_tipo_display()} {self.codigo_titulo} (empresa {self.empresa_id})'


class LancamentoConta(ModeloDaEmpresa):
    """Linha de conta corrente: o que de fato entrou ou saiu do banco.

    Chave: empresa + natureza + `codigo_movimento` (`nCodMovCC`) +
    `codigo_baixa` (`nCodBaixa`), com 0 quando o Omie não manda o campo
    (D-36). Baixa de título tem `codigo_baixa`; lançamento direto e
    transferência só têm `codigo_movimento`.

    **Baixa em lote:** um movimento bancário pode quitar vários títulos. Cada
    baixa vira uma linha com o mesmo `codigo_movimento` e o mesmo
    `valor_movimento`. O fluxo realizado soma `valor` (`nValPago`, o desta
    linha), NUNCA `valor_movimento`, que multiplicaria o pagamento.
    """

    natureza = models.CharField(max_length=1, choices=Natureza.choices)  # cGrupo CONTA_CORRENTE_PAG / _REC
    codigo_movimento = models.BigIntegerField(default=0, help_text='nCodMovCC (0 = não informado)')
    codigo_baixa = models.BigIntegerField(default=0, help_text='nCodBaixa (0 = não é baixa de título)')
    codigo_titulo = models.BigIntegerField(null=True, blank=True, help_text='nCodTitulo do título baixado')
    status = models.CharField(max_length=30, blank=True)

    codigo_conta_corrente = models.BigIntegerField(null=True, blank=True, help_text='nCodCC')
    codigo_cliente = models.BigIntegerField(null=True, blank=True)
    codigo_categoria = models.CharField(max_length=30, blank=True)
    tipo_documento = models.CharField(max_length=20, blank=True)
    origem = models.CharField(
        max_length=10, blank=True, help_text='cOrigem: BAXP/BAXR baixa, EXTP/EXTR extrato, TRAP/TRAR transferência'
    )

    data_pagamento = models.DateField(null=True, blank=True, help_text='dDtPagamento: dia em que o dinheiro moveu')
    data_conciliacao = models.DateField(null=True, blank=True)
    data_credito = models.DateField(null=True, blank=True)

    valor = models.DecimalField(**VALOR, help_text='nValPago: o valor DESTA linha — o que o fluxo realizado soma')
    valor_movimento = models.DecimalField(**VALOR, help_text='nValorMovCC: total do movimento bancário — NUNCA somar')
    juros = models.DecimalField(**VALOR)
    multa = models.DecimalField(**VALOR)
    desconto = models.DecimalField(**VALOR)

    sincronizado_em = models.DateTimeField(auto_now=True)

    objects = EscopoEmpresaQuerySet.as_manager()

    class Meta:
        db_table = 'financeiro_lancamentos_conta'
        verbose_name = 'lançamento de conta corrente'
        verbose_name_plural = 'lançamentos de conta corrente'
        constraints = [
            models.UniqueConstraint(
                fields=['empresa_id', 'natureza', 'codigo_movimento', 'codigo_baixa'],
                name='lancamento_unico_por_empresa',
            ),
            # Sem nenhum dos dois, todas as linhas "sem id" colapsariam numa só.
            models.CheckConstraint(
                condition=Q(codigo_movimento__gt=0) | Q(codigo_baixa__gt=0), name='lancamento_com_codigo'
            ),
        ]
        indexes = [
            models.Index(fields=['empresa_id', 'data_pagamento'], name='lancamento_data_pagamento'),
            models.Index(fields=['empresa_id', 'codigo_titulo'], name='lancamento_titulo'),
        ]

    def __str__(self) -> str:
        return f'lançamento {self.codigo_movimento}/{self.codigo_baixa} (empresa {self.empresa_id})'


# --- cadastros (relidos por inteiro na reconciliação diária, D-37) -----------
#
# Guardam só o necessário para o dashboard mostrar nomes no lugar de
# códigos. Por minimização (LGPD, RNF-04), NÃO guardam CPF/CNPJ, endereço,
# telefone, e-mail, agência ou número de conta (D-38).


class ContaCorrente(ModeloDaEmpresa):
    """Conta corrente/caixa da empresa no Omie (`ListarContasCorrentes`)."""

    codigo = models.BigIntegerField(help_text='nCodCC')
    descricao = models.CharField(max_length=100, blank=True)
    tipo = models.CharField(max_length=30, blank=True, help_text='tipo_conta_corrente (CC, CX, ...)')
    codigo_banco = models.CharField(max_length=10, blank=True)
    ativa = models.BooleanField(default=True, help_text='inativo != S')
    fora_do_fluxo = models.BooleanField(default=False, help_text='nao_fluxo = S: não entra no fluxo de caixa')
    saldo_inicial = models.DecimalField(**VALOR)
    data_saldo_inicial = models.DateField(null=True, blank=True, help_text='saldo_data')
    sincronizado_em = models.DateTimeField(auto_now=True)

    objects = EscopoEmpresaQuerySet.as_manager()

    class Meta:
        db_table = 'financeiro_contas_correntes'
        verbose_name = 'conta corrente'
        verbose_name_plural = 'contas correntes'
        constraints = [
            models.UniqueConstraint(fields=['empresa_id', 'codigo'], name='conta_corrente_unica_por_empresa'),
        ]

    def __str__(self) -> str:
        return f'conta corrente {self.codigo} (empresa {self.empresa_id})'


class Categoria(ModeloDaEmpresa):
    """Categoria financeira (`ListarCategorias`). Código é texto ('2.01.03')."""

    codigo = models.CharField(max_length=30)
    descricao = models.CharField(max_length=120, blank=True)
    codigo_superior = models.CharField(max_length=30, blank=True, help_text='categoria_superior')
    receita = models.BooleanField(default=False, help_text='conta_receita = S')
    despesa = models.BooleanField(default=False, help_text='conta_despesa = S')
    sincronizado_em = models.DateTimeField(auto_now=True)

    objects = EscopoEmpresaQuerySet.as_manager()

    class Meta:
        db_table = 'financeiro_categorias'
        verbose_name = 'categoria'
        verbose_name_plural = 'categorias'
        constraints = [
            models.UniqueConstraint(fields=['empresa_id', 'codigo'], name='categoria_unica_por_empresa'),
        ]

    def __str__(self) -> str:
        return f'categoria {self.codigo} (empresa {self.empresa_id})'


class ClienteFornecedor(ModeloDaEmpresa):
    """Cliente ou fornecedor (`ListarClientes`: o Omie usa um cadastro só)."""

    codigo = models.BigIntegerField(help_text='codigo_cliente_omie')
    razao_social = models.CharField(max_length=150, blank=True)
    nome_fantasia = models.CharField(max_length=150, blank=True)
    ativo = models.BooleanField(default=True, help_text='inativo != S')
    sincronizado_em = models.DateTimeField(auto_now=True)

    objects = EscopoEmpresaQuerySet.as_manager()

    class Meta:
        db_table = 'financeiro_clientes_fornecedores'
        verbose_name = 'cliente ou fornecedor'
        verbose_name_plural = 'clientes e fornecedores'
        constraints = [
            models.UniqueConstraint(fields=['empresa_id', 'codigo'], name='cliente_unico_por_empresa'),
        ]

    def __str__(self) -> str:
        return f'cliente/fornecedor {self.codigo} (empresa {self.empresa_id})'


# --- saldos ------------------------------------------------------------------


class SaldoConta(ModeloDaEmpresa):
    """Saldo de uma conta corrente num dia (`ListarExtrato` com
    `cExibirApenasSaldo`). Uma linha por conta por dia: a sincronização
    regrava o dia de hoje a cada rodada e os dias anteriores ficam como
    histórico."""

    codigo_conta_corrente = models.BigIntegerField(help_text='nCodCC')
    data = models.DateField(help_text='dPeriodoFinal consultado')
    saldo_atual = models.DecimalField(**VALOR, help_text='nSaldoAtual')
    saldo_atual_previsto = models.DecimalField(**VALOR, help_text='nSaldoAtualPrevisto')
    saldo_anterior = models.DecimalField(**VALOR, help_text='nSaldoAnterior')
    saldo_conciliado = models.DecimalField(**VALOR, help_text='nSaldoConciliado')
    saldo_disponivel = models.DecimalField(**VALOR, help_text='nSaldoDisponivel')
    saldo_provisorio = models.DecimalField(**VALOR, help_text='nSaldoProvisorio')
    limite_credito = models.DecimalField(**VALOR, help_text='nLimiteCreditoTotal')
    sincronizado_em = models.DateTimeField(auto_now=True)

    objects = EscopoEmpresaQuerySet.as_manager()

    class Meta:
        db_table = 'financeiro_saldos'
        verbose_name = 'saldo de conta'
        verbose_name_plural = 'saldos de conta'
        constraints = [
            models.UniqueConstraint(
                fields=['empresa_id', 'codigo_conta_corrente', 'data'], name='saldo_unico_por_conta_e_dia'
            ),
        ]

    def __str__(self) -> str:
        return f'saldo da conta {self.codigo_conta_corrente} em {self.data} (empresa {self.empresa_id})'
