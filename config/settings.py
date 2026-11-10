"""
Configuração do backend do módulo Financeiro (dashboard-financeiro).

Desenho completo na wiki do repositório (Arquitetura, Validação do JWT,
Banco de Dados). Este serviço não tem login nem tabela de usuários: ele só
valida o JWT RS256 emitido pelo forms-meta (D-20, D-27).
"""

import os
import re
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'dev-secret-troque-isto')
DEBUG = os.environ.get('DJANGO_DEBUG', 'false').lower() == 'true'
ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()
]

INSTALLED_APPS = [
    # Sem django.contrib.admin/auth/sessions de propósito: não existe
    # usuário aqui (identidade vem do JWT do forms-meta). contenttypes e
    # auth ficam só porque o DRF/simplejwt importam os models de
    # permissão — nenhuma tabela de usuário é usada.
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'rest_framework',
    'corsheaders',
    'apps.core',
    'apps.acesso',
    'apps.omie',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'

# Banco: database `dashboard_financeiro` na instância `forms-meta-db` do
# Render, com role próprio (D-11). SSL só quando o host é do Render —
# mesma regra do forms-meta.
DATABASE_URL = os.environ.get(
    'DATABASE_URL', 'postgresql://postgres:postgres@localhost:5436/dashboard_financeiro'
)
DATABASES = {
    'default': dj_database_url.config(
        default=DATABASE_URL,
        conn_max_age=600,
        ssl_require=bool(re.search(r'\.render\.com', DATABASE_URL)),
    )
}

LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


def _ler_chave_publica() -> str:
    """Chave pública RS256 do forms-meta, em PEM.

    Aceita a chave com quebras de linha reais ou com `\\n` literal (útil em
    `.env` de uma linha só). Obrigatória: sem ela nenhum token é validável,
    então o processo falha alto no boot em vez de subir e recusar tudo em
    silêncio — mesma filosofia do `DATABASE_URL` no forms-meta.
    """
    chave = os.environ.get('JWT_PUBLIC_KEY', '').replace('\\n', '\n').strip()
    if not chave:
        raise ImproperlyConfigured(
            'JWT_PUBLIC_KEY não definida — é a chave pública RS256 do forms-meta (ver .env.example).'
        )
    return chave


# DRF + validação do JWT (D-20, D-26). Deny-by-default: toda view exige a
# permissão do financeiro, a menos que declare outra coisa explicitamente
# (só o health check faz isso).
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTStatelessUserAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'apps.acesso.permissoes.AcessoFinanceiro',
    ),
    'UNAUTHENTICATED_USER': None,
}

SIMPLE_JWT = {
    'ALGORITHM': 'RS256',
    # Este serviço nunca emite token: só verifica. A chave privada fica
    # exclusivamente no forms-meta.
    'SIGNING_KEY': None,
    'VERIFYING_KEY': _ler_chave_publica(),
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_CLAIM': 'user_id',
    'TOKEN_TYPE_CLAIM': 'token_type',
}

# Chave(s) Fernet que cifram as credenciais Omie de cada empresa no banco
# (D-32). Várias separadas por vírgula = rotação (ver apps/omie/cifra.py).
# Só é exigida quando uma credencial é gravada/lida.
OMIE_CREDENCIAIS_CHAVE = os.environ.get('OMIE_CREDENCIAIS_CHAVE', '')

# CORS — o React do forms-meta é outra origem.
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get('DJANGO_CORS_ALLOWED_ORIGINS', 'http://localhost:5173').split(',') if o.strip()
]
CORS_ALLOW_CREDENTIALS = False  # JWT vai no header Authorization, não em cookie

# Logs: nunca payloads financeiros nem credenciais (RNF-04). Por enquanto
# só o básico no console, que é o que o Render captura.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': os.environ.get('LOG_LEVEL', 'INFO')},
}
