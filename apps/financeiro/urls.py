from django.urls import path

from .views import (
    ContasAPagarView,
    ContasAReceberView,
    FluxoDeCaixaView,
    UltimaAtualizacaoView,
)
from .views_categorias import CategoriasView, FluxoPorCategoriaView

urlpatterns = [
    path('contas-a-pagar/', ContasAPagarView.as_view(), name='contas-a-pagar'),
    path('contas-a-receber/', ContasAReceberView.as_view(), name='contas-a-receber'),
    path('fluxo-de-caixa/', FluxoDeCaixaView.as_view(), name='fluxo-de-caixa'),
    path('fluxo-de-caixa/por-categoria/', FluxoPorCategoriaView.as_view(), name='fluxo-por-categoria'),
    path('categorias/', CategoriasView.as_view(), name='categorias'),
    path('ultima-atualizacao/', UltimaAtualizacaoView.as_view(), name='ultima-atualizacao'),
]
