from django.db import models

from apps.core.models import EscopoEmpresaQuerySet, ModeloDaEmpresa

from . import cifra


class ContaOmie(ModeloDaEmpresa):
    """Conta Omie de uma empresa (D-32): 1 por empresa.

    `app_key`/`app_secret` ficam cifrados (ver `cifra.py`). Leia pelos
    métodos `credenciais()`; grave por `definir_credenciais()`. Os campos
    cifrados nunca vão para serializer, log ou `repr`.

    `ultima_sincronizacao_em` fica aqui por enquanto como marcador simples;
    o controle completo de execuções (ponto de corte por entidade, erros)
    vem com a entrega de sincronização.
    """

    empresa_id = models.BigIntegerField(unique=True, editable=False)
    app_key_cifrada = models.TextField(editable=False)
    app_secret_cifrado = models.TextField(editable=False)
    ativa = models.BooleanField(
        default=True,
        help_text='Desligada = a sincronização ignora esta empresa (sem apagar as credenciais).',
    )
    ultima_sincronizacao_em = models.DateTimeField(null=True, blank=True, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'omie_contas'
        verbose_name = 'conta Omie'
        verbose_name_plural = 'contas Omie'

    def __str__(self) -> str:
        return f'Conta Omie da empresa {self.empresa_id}'

    def __repr__(self) -> str:
        return f'<ContaOmie empresa_id={self.empresa_id} ativa={self.ativa}>'

    def definir_credenciais(self, app_key: str, app_secret: str) -> None:
        app_key, app_secret = (app_key or '').strip(), (app_secret or '').strip()
        if not app_key or not app_secret:
            raise ValueError('app_key e app_secret são obrigatórios.')
        self.app_key_cifrada = cifra.cifrar(app_key)
        self.app_secret_cifrado = cifra.cifrar(app_secret)

    def credenciais(self) -> tuple[str, str]:
        """(app_key, app_secret) em texto puro — só para montar a chamada ao Omie."""
        return cifra.decifrar(self.app_key_cifrada), cifra.decifrar(self.app_secret_cifrado)


class ExecucaoQuerySet(EscopoEmpresaQuerySet):
    def bem_sucedidas(self):
        return self.filter(situacao=ExecucaoSincronizacao.Situacao.SUCESSO)

    def ultima_bem_sucedida(self, *tipos: str):
        """A mais recente que terminou com sucesso (de qualquer tipo, ou só
        dos `tipos` pedidos). Base do ponto de corte e do "atualizado há X
        min" (RF-04). Use sempre depois de `.da_empresa()`."""
        consulta = self.bem_sucedidas()
        if tipos:
            consulta = consulta.filter(tipo__in=tipos)
        return consulta.order_by('-finalizada_em', '-id').first()


class ExecucaoSincronizacao(ModeloDaEmpresa):
    """Uma rodada da sincronização de UMA empresa (wiki: Sincronização;
    RNF-11, RNF-12).

    **Ponto de corte:** `alterados_ate` da última execução bem-sucedida. A
    próxima incremental pede "alterados de `alterados_ate` até hoje" (o
    filtro do Omie é por dia, então o dia do corte é relido de propósito).
    Execução com erro não conta: o corte só avança com sucesso (RNF-11).

    `contagens` guarda só números (gravados por tabela, ignorados por
    motivo); `erro_detalhe` só a mensagem nossa e o fault do Omie, nunca
    payload (RNF-04).
    """

    class Tipo(models.TextChoices):
        INCREMENTAL = 'incremental', 'incremental (~5 min)'
        RECONCILIACAO = 'reconciliacao', 'reconciliação diária'

    class Situacao(models.TextChoices):
        EM_ANDAMENTO = 'em_andamento', 'em andamento'
        SUCESSO = 'sucesso', 'sucesso'
        ERRO = 'erro', 'erro'

    tipo = models.CharField(max_length=15, choices=Tipo.choices)
    situacao = models.CharField(max_length=15, choices=Situacao.choices, default=Situacao.EM_ANDAMENTO)
    iniciada_em = models.DateTimeField(auto_now_add=True)
    finalizada_em = models.DateTimeField(null=True, blank=True)
    alterados_desde = models.DateField(null=True, blank=True, help_text='dDtAltDe pedido ao Omie')
    alterados_ate = models.DateField(null=True, blank=True, help_text='dDtAltAte pedido ao Omie (ponto de corte)')
    contagens = models.JSONField(default=dict, blank=True)
    erro_tipo = models.CharField(max_length=40, blank=True, help_text='classe do erro (ErroCredencial, ...)')
    erro_detalhe = models.TextField(blank=True)

    objects = ExecucaoQuerySet.as_manager()

    class Meta:
        db_table = 'omie_execucoes'
        verbose_name = 'execução da sincronização'
        verbose_name_plural = 'execuções da sincronização'
        indexes = [
            models.Index(fields=['empresa_id', 'situacao', 'finalizada_em'], name='execucao_empresa_situacao'),
        ]

    def __str__(self) -> str:
        return f'{self.get_tipo_display()} da empresa {self.empresa_id}: {self.get_situacao_display()}'
