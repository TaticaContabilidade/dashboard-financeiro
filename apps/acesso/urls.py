from django.urls import path

from .views import MeuAcessoView

urlpatterns = [
    path('meu-acesso/', MeuAcessoView.as_view(), name='meu-acesso'),
]
