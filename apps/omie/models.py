from django.db import models

from apps.core.models import ModeloDaEmpresa

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
