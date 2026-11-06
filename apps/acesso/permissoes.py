"""
Quem acessa o módulo Financeiro (wiki: Papéis, D-18, D-23, D-30, RF-08).

Ponto ÚNICO de checagem de permissão do serviço — toda regra de acesso
mora aqui, para adaptar num lugar só quando o login novo do forms-meta
mudar (D-27). As informações vêm das claims do JWT (D-26), lidas via
`TokenUser` do simplejwt, sem consulta a banco.

| Quem                                                  | Lê | Altera |
|-------------------------------------------------------|----|--------|
| ADM                                                   | ✔  | ✔      |
| DIRETOR da empresa interna (donos da Tática)           | ✔  | ✔      |
| GERENTE do setor financeiro da empresa interna         | ✔  | ✔      |
| COLABORADOR do setor financeiro da empresa interna     | ✔  | ✘      |
| qualquer outro (outros setores, empresas clientes)     | ✘  | ✘      |

O Diretor de empresa cliente fica de fora: os dados aqui são da conta Omie
da Tática (D-10), e o dono de um cliente não pode ver as finanças da
Tática.
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


def _e_equipe_financeira(usuario) -> bool:
    return _claim_verdadeira(usuario, 'empresa_interna') and _claim_verdadeira(usuario, 'setor_financeiro')


def pode_ver_financeiro(usuario) -> bool:
    if usuario is None or not getattr(usuario, 'is_authenticated', False):
        return False
    # O simplejwt só exige que a claim `user_id` exista; um `null` passaria.
    if getattr(usuario, 'id', None) is None:
        return False
    papel = getattr(usuario, 'papel', None)
    if papel == ADM:
        return True
    if papel == DIRETOR:
        # Dono da Tática vê a empresa inteira, qualquer que seja o setor dele.
        return _claim_verdadeira(usuario, 'empresa_interna')
    return papel in (GERENTE, COLABORADOR) and _e_equipe_financeira(usuario)


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
