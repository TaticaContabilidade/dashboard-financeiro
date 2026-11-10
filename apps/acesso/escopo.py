"""
De QUAL empresa são os dados de uma requisição (wiki: Multiempresa, D-32).

Regra:
- DIRETOR, GERENTE e COLABORADOR: sempre a empresa do próprio token
  (`empresa_id`). Pedir outra empresa via `?empresa_id=` é recusado com
  403 — nunca ignorado em silêncio, para um erro de integração no React
  aparecer na hora em vez de mostrar dados de outro lugar.
- ADM (desenvolvedor): pode escolher qualquer empresa com `?empresa_id=`;
  sem o parâmetro, usa a do próprio token.

Use sempre via `EscopoEmpresaMixin` (ou `empresa_da_requisicao`) em toda
view que toca dado financeiro — nunca filtre por um `empresa_id` vindo do
body/query diretamente.
"""

from rest_framework.exceptions import PermissionDenied, ValidationError

from .permissoes import ADM

PARAMETRO = 'empresa_id'


def empresa_da_requisicao(request) -> int:
    usuario = request.user
    propria = usuario.empresa_id
    pedida = request.query_params.get(PARAMETRO)
    if pedida is None or pedida == '':
        return propria

    try:
        pedida = int(pedida)
    except (TypeError, ValueError):
        raise ValidationError({PARAMETRO: 'Informe um número inteiro.'})
    if pedida <= 0:
        raise ValidationError({PARAMETRO: 'Informe um número inteiro positivo.'})

    if usuario.papel == ADM or pedida == propria:
        return pedida
    raise PermissionDenied('Você só pode consultar os dados da sua empresa.')


class EscopoEmpresaMixin:
    """Para views/viewsets do DRF sobre models `ModeloDaEmpresa`.

    - `get_queryset()` devolve só as linhas da empresa da requisição;
    - `perform_create()` grava a empresa da requisição, ignorando qualquer
      `empresa_id` que o cliente tenha mandado no body.

    Precisa vir ANTES da classe base do DRF na herança:
        class ContasPagarViewSet(EscopoEmpresaMixin, viewsets.ReadOnlyModelViewSet): ...
    """

    def empresa_id(self) -> int:
        if not hasattr(self, '_empresa_id'):
            self._empresa_id = empresa_da_requisicao(self.request)
        return self._empresa_id

    def get_queryset(self):
        return super().get_queryset().da_empresa(self.empresa_id())

    def perform_create(self, serializer):
        serializer.save(empresa_id=self.empresa_id())
