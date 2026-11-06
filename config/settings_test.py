"""
Settings da suíte de testes.

Gera um par de chaves RSA novo a cada execução e usa a pública como
JWT_PUBLIC_KEY — os testes assinam seus próprios tokens com a privada
(`CHAVE_PRIVADA_TESTE`), imitando o que o forms-meta vai emitir (D-27).
Nenhuma chave de teste fica versionada.

Precisa rodar antes de `config.settings` porque o simplejwt monta o
verificador de token uma única vez, na importação.
"""

import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)

CHAVE_PRIVADA_TESTE = _chave.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()

os.environ['JWT_PUBLIC_KEY'] = (
    _chave.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode()
)

from .settings import *  # noqa: E402,F401,F403
