"""
Criptografia das credenciais do Omie guardadas no banco (D-32, RNF-03).

Fernet (AES-128-CBC + HMAC-SHA256, da biblioteca `cryptography`). A chave
vem da variável `OMIE_CREDENCIAIS_CHAVE` — nunca do banco, para um dump do
banco sozinho não expor credenciais.

Rotação: a variável aceita várias chaves separadas por vírgula. A PRIMEIRA
cifra os valores novos; todas decifram. Para trocar a chave:
  1. coloque a nova na frente: `NOVA,ANTIGA`;
  2. rode `python manage.py recifrar_contas_omie`;
  3. remova a antiga.

A chave só é exigida quando alguém cifra/decifra — o serviço sobe sem ela
enquanto nenhuma empresa tiver conta Omie cadastrada.
"""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class CredencialIlegivel(Exception):
    """O valor no banco não abre com nenhuma das chaves configuradas."""


def _fernet() -> MultiFernet:
    bruto = getattr(settings, 'OMIE_CREDENCIAIS_CHAVE', '') or ''
    chaves = [c.strip() for c in bruto.split(',') if c.strip()]
    if not chaves:
        raise ImproperlyConfigured(
            'OMIE_CREDENCIAIS_CHAVE não definida — necessária para guardar/ler credenciais do Omie '
            '(ver .env.example).'
        )
    try:
        return MultiFernet([Fernet(c) for c in chaves])
    except ValueError as erro:
        raise ImproperlyConfigured('OMIE_CREDENCIAIS_CHAVE inválida (esperado chave Fernet).') from erro


def cifrar(valor: str) -> str:
    return _fernet().encrypt(valor.encode()).decode()


def decifrar(cifrado: str) -> str:
    try:
        return _fernet().decrypt(cifrado.encode()).decode()
    except InvalidToken as erro:
        raise CredencialIlegivel('Credencial do Omie não pôde ser decifrada com as chaves atuais.') from erro


def recifrar(cifrado: str) -> str:
    """Decifra com qualquer chave e cifra de novo com a primeira (rotação)."""
    try:
        return _fernet().rotate(cifrado.encode()).decode()
    except InvalidToken as erro:
        raise CredencialIlegivel('Credencial do Omie não pôde ser decifrada com as chaves atuais.') from erro
