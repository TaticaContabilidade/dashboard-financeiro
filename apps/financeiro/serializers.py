"""
Serializers dos endpoints financeiros (RF-01, RF-02).
"""

from rest_framework import serializers

from apps.financeiro.models import Titulo


class TituloSerializer(serializers.ModelSerializer):
    """Título a pagar ou a receber, com situação derivada e nomes resolvidos.

    Campos ``situacao``, ``nome_cliente_fornecedor`` e ``descricao_categoria``
    vêm de annotations na queryset da view — não existem no model.
    """

    situacao = serializers.CharField(read_only=True)
    nome_cliente_fornecedor = serializers.CharField(
        read_only=True, allow_null=True, default=None,
    )
    descricao_categoria = serializers.CharField(
        read_only=True, allow_null=True, default=None,
    )

    class Meta:
        model = Titulo
        fields = [
            'codigo_titulo',
            'numero_documento',
            'nome_cliente_fornecedor',
            'descricao_categoria',
            'data_emissao',
            'data_vencimento',
            'data_pagamento',
            'valor_titulo',
            'valor_pago',
            'valor_aberto',
            'situacao',
        ]
