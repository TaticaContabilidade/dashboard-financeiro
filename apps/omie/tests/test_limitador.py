from apps.omie.limitador import Limitador


class Relogio:
    def __init__(self):
        self.agora = 0.0
        self.dormidas = []

    def __call__(self):
        return self.agora

    def dormir(self, segundos):
        self.dormidas.append(segundos)
        self.agora += segundos


def _limitador(relogio, **kw):
    return Limitador(relogio=relogio, dormir=relogio.dormir, **kw)


def test_abaixo_do_limite_nao_espera():
    r = Relogio()
    lim = _limitador(r, limite_ip=5, limite_por_metodo=5)
    assert all(lim.aguardar_vez('k', 'M') == 0 for _ in range(5))
    assert r.dormidas == []


def test_janela_deslizante_espera_a_mais_antiga_sair():
    r = Relogio()
    lim = _limitador(r, limite_ip=3, limite_por_metodo=99)
    for _ in range(3):
        lim.aguardar_vez('k', 'M')
        r.agora += 10  # requisições em t=0, 10, 20
    # t=30: a de t=0 só sai da janela em t=60
    assert lim.aguardar_vez('k', 'M') == 30
    assert r.agora == 60


def test_limite_do_ip_soma_todas_as_empresas():
    """O exemplo da conversa: empresas diferentes contam no mesmo teto de IP."""
    r = Relogio()
    lim = _limitador(r, limite_ip=20, limite_por_metodo=99)
    for empresa in range(20):
        lim.aguardar_vez(f'app-key-{empresa}', 'ListarMovimentos')
    assert lim.aguardar_vez('app-key-21', 'ListarMovimentos') == 60


def test_limite_por_metodo_e_separado_por_app_key_e_metodo():
    r = Relogio()
    lim = _limitador(r, limite_ip=99, limite_por_metodo=2)
    lim.aguardar_vez('A', 'M1')
    lim.aguardar_vez('A', 'M1')
    assert lim.aguardar_vez('A', 'M2') == 0  # outro método
    assert lim.aguardar_vez('B', 'M1') == 0  # outra empresa
    assert lim.aguardar_vez('A', 'M1') == 60  # mesmo app_key + método
