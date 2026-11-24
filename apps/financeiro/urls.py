from django.urls import path

from .views import (
    ContasAPagarView,
    ContasAReceberView,
    FluxoDeCaixaView,
    UltimaAtualizacaoView,
)

urlpatterns = [
    path('contas-a-pagar/', ContasAPagarView.as_view(), name='contas-a-pagar'),
    path('contas-a-receber/', ContasAReceberView.as_view(), name='contas-a-receber'),
    path('fluxo-de-caixa/', FluxoDeCaixaView.as_view(), name='fluxo-de-caixa'),
    path('ultima-atualizacao/', UltimaAtualizacaoView.as_view(), name='ultima-atualizacao'),
]
