"""
Erros do cliente do Omie, classificados pelo que a sincronização deve fazer
(wiki: Integração Omie, "Limites de consumo"; Sincronização).

| Classe              | Exemplo                                  | O que fazer                                        |
|---------------------|------------------------------------------|----------------------------------------------------|
| ErroTransitorio     | rede, timeout, HTTP 5xx sem fault        | tentar de novo mais tarde (o cliente já re-tenta)  |
| ErroLimite          | "Too many requests" / HTTP 429           | esperar a janela de 60 s                           |
| ErroRedundante      | mesma consulta repetida em < 60 s        | esperar 60 s; não é falha da empresa               |
| ErroBloqueio        | HTTP 425 — bloqueio de 30 min            | não tocar na empresa por 30 min                    |
| ErroCredencial      | app_key/app_secret inválidos             | suspender a empresa até a credencial ser corrigida |
| ErroPermanente      | parâmetro inválido, fault desconhecido   | não re-tentar igual; registrar para análise        |

Confirmado contra o Omie real (2026-11-10): credencial inválida volta
HTTP 403 com faultstring "A chave de acesso não está preenchida ou não é
válida." (faultcode "SOAP-ENV:Server"). Os demais padrões de texto ainda
não foram vistos em resposta real — o `testar_conta_omie` ajuda a
confirmar. Fault não reconhecido cai em `ErroPermanente` (o lado
seguro: não insistir, porque 10 erros seguidos bloqueiam por 30 min).
"""


class ErroOmie(Exception):
    """Base. `transitorio` diz se vale tentar de novo mais tarde."""

    transitorio = False

    def __init__(self, mensagem: str, *, status: int | None = None, faultcode: str = '', faultstring: str = ''):
        super().__init__(mensagem)
        self.status = status
        self.faultcode = faultcode
        self.faultstring = faultstring


class ErroTransitorio(ErroOmie):
    transitorio = True


class ErroLimite(ErroTransitorio):
    pass


class ErroRedundante(ErroTransitorio):
    pass


class ErroBloqueio(ErroOmie):
    pass


class ErroCredencial(ErroOmie):
    pass


class ErroPermanente(ErroOmie):
    pass


class SemRegistros(Exception):
    """Não é erro: o Omie responde com fault quando a página/consulta não
    tem registros. O cliente converte isso em lista vazia."""


def _contem(texto: str, *trechos: str) -> bool:
    texto = texto.lower()
    return any(t in texto for t in trechos)


def classificar(status: int, corpo: dict | None) -> Exception | None:
    """Recebe a resposta HTTP já decodificada; devolve a exceção a lançar
    (ou `None` se a resposta é um sucesso)."""
    corpo = corpo if isinstance(corpo, dict) else {}
    faultstring = str(corpo.get('faultstring') or '')
    faultcode = str(corpo.get('faultcode') or '')
    dados = {'status': status, 'faultcode': faultcode, 'faultstring': faultstring}

    if status == 200 and not faultstring:
        return None

    if _contem(faultstring, 'não existem registros', 'nao existem registros'):
        return SemRegistros()
    if status == 425:
        return ErroBloqueio('Omie bloqueou as requisições desta empresa (HTTP 425, ~30 min).', **dados)
    if status == 429 or _contem(faultstring, 'too many requests', 'limite de requisi'):
        return ErroLimite('Limite de requisições do Omie atingido.', **dados)
    if _contem(faultstring, 'redundante', 'já foi realizada', 'ja foi realizada', 'redundant'):
        return ErroRedundante('Consulta repetida em menos de 60 s.', **dados)
    if status in (401, 403) or _contem(
        faultstring, 'app_key', 'app_secret', 'chave de acesso', 'application key', 'não autorizad', 'nao autorizad'
    ):
        return ErroCredencial('Credencial do Omie recusada.', **dados)
    if _contem(faultstring, 'proto_byebye'):
        # A documentação pede para NÃO re-tentar às cegas (pode deixar
        # registro parcial) — relevante em escrita; tratado como permanente.
        return ErroPermanente('Instabilidade do Omie com risco de registro parcial (PROTO_BYEBYE).', **dados)
    if status >= 500 and not faultstring:
        return ErroTransitorio(f'Omie respondeu HTTP {status}.', **dados)
    return ErroPermanente(f'Omie recusou a requisição (HTTP {status}).', **dados)
