"""
Sincroniza o Omie das empresas para o nosso banco (wiki: Sincronização).

    python manage.py sincronizar_omie                     # incremental, todas as empresas
    python manage.py sincronizar_omie --reconciliacao     # diária: relê tudo e aplica exclusões
    python manage.py sincronizar_omie --empresa-id 1      # só uma empresa

Roda uma empresa por vez. A falha de uma não interrompe as outras. Empresa
desativada, suspensa (credencial recusada) ou pausada (bloqueio do Omie)
é pulada. A saída tem só contagens, nunca valores (RNF-04).

Código de saída 1 só se houver erro inesperado (bug nosso), para o Cron
Job do Render marcar a execução como falha. Erros do Omie ficam
registrados em `omie_execucoes` e não mudam o código de saída.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.omie.models import ContaOmie, ExecucaoSincronizacao
from apps.omie.sincronizacao import Sincronizador


class Command(BaseCommand):
    help = 'Sincroniza o Omie das empresas (incremental ou reconciliação).'

    def add_arguments(self, parser):
        parser.add_argument('--reconciliacao', action='store_true', help='relê tudo e aplica exclusões (diária)')
        parser.add_argument('--empresa-id', type=int, help='só esta empresa')

    def handle(self, *args, reconciliacao, empresa_id, **opcoes):
        agora = timezone.now()
        contas = ContaOmie.objects.order_by('empresa_id')
        if empresa_id is not None:
            contas = contas.filter(empresa_id=empresa_id)
            if not contas.exists():
                raise CommandError(f'A empresa {empresa_id} não tem conta Omie.')

        bugs = 0
        for conta in contas:
            if not conta.pode_sincronizar(agora):
                self.stdout.write(f'empresa {conta.empresa_id}: pulada ({self._motivo(conta, agora)})')
                continue
            try:
                execucao = Sincronizador(conta).executar(reconciliacao=reconciliacao)
            except Exception as erro:  # noqa: BLE001 — já registrado na execução; segue para a próxima
                bugs += 1
                self.stderr.write(f'empresa {conta.empresa_id}: erro inesperado ({type(erro).__name__})')
                continue
            self.stdout.write(self._resumo(conta.empresa_id, execucao))

        if bugs:
            raise CommandError(f'{bugs} empresa(s) com erro inesperado; ver o log.')

    @staticmethod
    def _motivo(conta: ContaOmie, agora) -> str:
        if not conta.ativa:
            return 'desativada'
        if conta.suspensa_em:
            return 'suspensa: credencial recusada, rode configurar_conta_omie'
        return f'pausada até {timezone.localtime(conta.pausada_ate):%d/%m %H:%M}'

    @staticmethod
    def _resumo(empresa_id: int, execucao: ExecucaoSincronizacao | None) -> str:
        if execucao is None:
            return f'empresa {empresa_id}: pulada (outra rodada em andamento)'
        if execucao.situacao == ExecucaoSincronizacao.Situacao.ERRO:
            return f'empresa {empresa_id}: {execucao.tipo} com ERRO ({execucao.erro_tipo})'
        return f'empresa {empresa_id}: {execucao.tipo} ok {execucao.contagens}'
