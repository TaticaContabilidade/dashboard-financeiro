"""
Recifra as credenciais de todas as contas Omie com a chave mais nova.

Passo 2 da rotação de `OMIE_CREDENCIAIS_CHAVE` (ver `apps/omie/cifra.py`).
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.omie import cifra
from apps.omie.models import ContaOmie


class Command(BaseCommand):
    help = 'Recifra as credenciais Omie com a primeira chave de OMIE_CREDENCIAIS_CHAVE (rotação).'

    @transaction.atomic
    def handle(self, *args, **opcoes):
        total = 0
        # Varre todas as empresas de propósito (comando de manutenção, não
        # uma view): por isso não passa por `.da_empresa()`.
        for conta in ContaOmie.objects.select_for_update().order_by('pk'):
            conta.app_key_cifrada = cifra.recifrar(conta.app_key_cifrada)
            conta.app_secret_cifrado = cifra.recifrar(conta.app_secret_cifrado)
            conta.save(update_fields=['app_key_cifrada', 'app_secret_cifrado', 'atualizado_em'])
            total += 1
        self.stdout.write(self.style.SUCCESS(f'{total} conta(s) Omie recifrada(s).'))
