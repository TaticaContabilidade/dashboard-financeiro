"""
Quem acessa o módulo Financeiro (wiki: Papéis, Multiempresa; D-23, D-30 a D-32).

Ponto ÚNICO de checagem de permissão do serviço — toda regra de acesso
mora aqui, para adaptar num lugar só quando o login novo do forms-meta
mudar (D-27). As informações vêm das claims do JWT (D-26), lidas via
`TokenUser` do simplejwt, sem consulta a banco.

O financeiro é multiempresa (D-32): cada empresa vê só os dados do próprio
Omie. Esta permissão responde "pode usar o módulo?"; DE QUAL empresa são
os dados é decidido em `apps.acesso.escopo`.

| Quem (na própria empresa)                | Lê | Altera |
|------------------------------------------|----|--------|
| ADM (desenvolvedor; qualquer empresa)     | ✔  | ✔      |
| DIRETOR (donos), em qualquer setor        | ✔  | ✔      |
| GERENTE do setor financeiro               | ✔  | ✔      |
| COLABORADOR do setor financeiro           | ✔  | ✘      |
| qualquer outro (outros setores)           | ✘  | ✘      |
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

ADM = 'ADM'
DIRETOR = 'DIRETOR'
GERENTE = 'GERENTE'
COLABORADOR = 'COLABORADOR'


def _claim_verdadeira(usuario, nome: str) -> bool:
    # `is True` de propósito: só o booleano JSON `true` conta. Uma string
    # "false" ou "1" seria truthy num `if` comum e abriria acesso.
    return getattr(usuario, nome, None) is True


def _empresa_valida(usuario) -> bool:
    # Sem empresa no token não há como escopar os dados (D-32). `bool` é
    # subclasse de `int` em Python — `true` não vale como id.
    empresa_id = getattr(usuario, 'empresa_id', None)
    return isinstance(empresa_id, int) and not isinstance(empresa_id, bool) and empresa_id > 0


def pode_ver_financeiro(usuario) -> bool:
    if usuario is None or not getattr(usuario, 'is_authenticated', False):
        return False
    # O simplejwt só exige que a claim `user_id` exista; um `null` passaria.
    if getattr(usuario, 'id', None) is None or not _empresa_valida(usuario):
        return False
    papel = getattr(usuario, 'papel', None)
    if papel in (ADM, DIRETOR):
        return True
    return papel in (GERENTE, COLABORADOR) and _claim_verdadeira(usuario, 'setor_financeiro')


def pode_alterar_financeiro(usuario) -> bool:
    if not pode_ver_financeiro(usuario):
        return False
    return getattr(usuario, 'papel', None) in (ADM, DIRETOR, GERENTE)


class AcessoFinanceiro(BasePermission):
    """Leitura (GET/HEAD/OPTIONS) para quem pode ver; qualquer outro método
    só para quem pode alterar. É a permissão padrão do serviço inteiro
    (settings.REST_FRAMEWORK) — deny-by-default."""

    message = 'Você não tem acesso a esta operação do módulo Financeiro.'

    def has_permission(self, request, view) -> bool:
        if request.method in SAFE_METHODS:
            return pode_ver_financeiro(request.user)
        return pode_alterar_financeiro(request.user)
