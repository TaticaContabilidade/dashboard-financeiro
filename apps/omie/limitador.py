"""
Trava de taxa por janela deslizante de 60 s (wiki: Integração Omie).

A documentação do Omie não diz se o "por minuto" é janela fixa ou
deslizante. A deslizante é a hipótese mais restritiva — respeitá-la
também respeita a fixa. Antes de cada requisição, conta as feitas nos
últimos 60 s; se o teto foi atingido, espera a mais antiga sair da janela.

Limites do Omie (artigo de 14/09/2026) e os tetos usados aqui, com folga:
- 960/min por IP (soma de todas as empresas!)       -> LIMITE_IP = 800
- 240/min por IP + app_key + método                  -> LIMITE_POR_METODO = 200

Vale por processo. A sincronização roda num processo só (Cron Job), então
o contador em memória basta; se um dia houver mais de um processo falando
com o Omie ao mesmo tempo (ex.: escrita pelo Web Service), o teto do IP
precisa ser dividido entre eles ou ir para um contador compartilhado.
"""

import threading
import time
from collections import defaultdict, deque

JANELA_SEGUNDOS = 60.0
LIMITE_IP = 800
LIMITE_POR_METODO = 200


class Limitador:
    def __init__(
        self,
        limite_ip: int = LIMITE_IP,
        limite_por_metodo: int = LIMITE_POR_METODO,
        janela: float = JANELA_SEGUNDOS,
        relogio=time.monotonic,
        dormir=time.sleep,
    ):
        self.limite_ip = limite_ip
        self.limite_por_metodo = limite_por_metodo
        self.janela = janela
        self._relogio = relogio
        self._dormir = dormir
        self._ip: deque[float] = deque()
        self._por_metodo: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._trava = threading.Lock()

    def _limpar(self, fila: deque, agora: float) -> None:
        while fila and agora - fila[0] >= self.janela:
            fila.popleft()

    def _espera_necessaria(self, fila: deque, limite: int, agora: float) -> float:
        self._limpar(fila, agora)
        if len(fila) < limite:
            return 0.0
        return self.janela - (agora - fila[0])

    def aguardar_vez(self, app_key: str, metodo: str) -> float:
        """Bloqueia até poder fazer a requisição; devolve quanto esperou."""
        esperou = 0.0
        chave = (app_key, metodo)
        while True:
            with self._trava:
                agora = self._relogio()
                espera = max(
                    self._espera_necessaria(self._ip, self.limite_ip, agora),
                    self._espera_necessaria(self._por_metodo[chave], self.limite_por_metodo, agora),
                )
                if espera <= 0:
                    self._ip.append(agora)
                    self._por_metodo[chave].append(agora)
                    return esperou
            self._dormir(espera)
            esperou += espera


# Um limitador por processo: o teto do IP é global para todas as empresas.
limitador_global = Limitador()
