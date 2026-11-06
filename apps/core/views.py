import logging

from django.db import connection
from django.db.utils import DatabaseError
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class HealthView(APIView):
    """Processo de pé + banco respondendo (RNF-12). Única rota pública do
    serviço — usada pelo health check do Render. Não expõe nenhum dado."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request: Request):
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
        except DatabaseError:
            logger.exception('Health check: banco indisponível')
            return Response({'status': 'erro', 'banco': 'indisponível'}, status=503)
        return Response({'status': 'ok', 'banco': 'ok'})
