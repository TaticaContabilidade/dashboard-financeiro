"""
Cadastra ou troca as credenciais Omie de uma empresa.

    python manage.py configurar_conta_omie --empresa-id 2

`app_key` e `app_secret` são pedidos no terminal, sem eco — nunca por
argumento, para não ficarem no histórico do shell nem na lista de
processos. Por enquanto é a única forma de cadastro (ADM/desenvolvedor);
quem vai cadastrar pela interface ainda está em aberto na wiki.
"""

from getpass import getpass

from django.core.management.base import BaseCommand, CommandError

from apps.omie.models import ContaOmie


class Command(BaseCommand):
    help = 'Cadastra ou troca as credenciais Omie de uma empresa (pedidas sem eco no terminal).'

    def add_arguments(self, parser):
        parser.add_argument('--empresa-id', type=int, required=True, help='id da empresa no forms-meta')
        parser.add_argument('--desativar', action='store_true', help='só desliga a sincronização da empresa')

    def handle(self, *args, empresa_id, desativar, **opcoes):
        if empresa_id <= 0:
            raise CommandError('--empresa-id precisa ser um inteiro positivo.')

        if desativar:
            atualizadas = ContaOmie.objects.da_empresa(empresa_id).update(ativa=False)
            if not atualizadas:
                raise CommandError(f'A empresa {empresa_id} não tem conta Omie.')
            self.stdout.write(self.style.SUCCESS(f'Sincronização desligada para a empresa {empresa_id}.'))
            return

        app_key = getpass('app_key do Omie: ')
        app_secret = getpass('app_secret do Omie: ')

        conta = ContaOmie.objects.da_empresa(empresa_id).first() or ContaOmie(empresa_id=empresa_id)
        try:
            conta.definir_credenciais(app_key, app_secret)
        except ValueError as erro:
            raise CommandError(str(erro)) from erro
        nova = conta.pk is None
        conta.ativa = True
        conta.save()
        acao = 'cadastrada' if nova else 'atualizada'
        self.stdout.write(self.style.SUCCESS(f'Conta Omie da empresa {empresa_id} {acao}.'))
