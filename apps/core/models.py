"""
Base de todo model de dado financeiro (wiki: Multiempresa, D-32).

Toda tabela com dado de uma empresa herda de `ModeloDaEmpresa` e ganha
`empresa_id`. Não é FK: as empresas moram no banco do forms-meta, e os
dois databases são isolados por role (D-11, RNF-05) — o id vem do JWT.

Leitura sempre por `.da_empresa(id)` (ou pelo `EscopoEmpresaMixin` nas
views), nunca `Model.objects.all()` numa view.
"""

from django.db import models


class EscopoEmpresaQuerySet(models.QuerySet):
    def da_empresa(self, empresa_id: int):
        if empresa_id is None:
            # Falha alto: um `filter(empresa_id=None)` devolveria vazio e
            # esconderia o bug de uma view que esqueceu de resolver a empresa.
            raise ValueError('empresa_id é obrigatório para consultar dados financeiros.')
        return self.filter(empresa_id=empresa_id)


class ModeloDaEmpresa(models.Model):
    empresa_id = models.BigIntegerField(db_index=True, editable=False)

    objects = EscopoEmpresaQuerySet.as_manager()

    class Meta:
        abstract = True
