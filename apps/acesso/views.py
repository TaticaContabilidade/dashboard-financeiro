from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .permissoes import pode_alterar_financeiro


class MeuAcessoView(APIView):
    """O que o usuário logado pode fazer no Financeiro.

    O React usa para decidir o que mostrar (ex.: esconder botões de
    alteração do colaborador) — só conveniência de interface: cada endpoint
    continua checando a permissão no servidor (RNF-01). Protegido pela
    permissão padrão (AcessoFinanceiro), então quem não tem acesso recebe
    403 aqui mesmo.
    """

    def get(self, request: Request):
        usuario = request.user
        return Response(
            {
                'user_id': usuario.id,
                'papel': usuario.papel,
                'pode_ver': True,
                'pode_alterar': pode_alterar_financeiro(usuario),
            }
        )
