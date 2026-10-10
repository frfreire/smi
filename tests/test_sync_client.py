"""Testes de tolerância a falhas: offline, retry/backoff, circuit breaker, 401, 422."""
import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import OC
from cliente_referencia.sync_client import (CircuitoAberto, ClienteApi, FalhaDeRede, Outbox,
                                            SessaoExpirada, sincronizar)


class Rede:
    """Injeta falhas na rede do TestClient: 'offline' ou N respostas 503 seguidas."""

    def __init__(self, http):
        self.original, self.offline, self.erros_503, self.chamadas = http.request, False, 0, 0
        http.request = self

    def __call__(self, metodo, url, **kw):
        self.chamadas += 1
        if self.offline:
            raise httpx.ConnectError("sem rede")
        if self.erros_503 > 0:
            self.erros_503 -= 1
            return httpx.Response(503, json={"erro": "indisponivel"}, request=httpx.Request(metodo, url))
        return self.original(metodo, url, **kw)


@pytest.fixture()
def cenario(app):
    client = TestClient(app)
    client.post("/auth/registro", json={"nome": "Ana", "email": "ana@x.com", "senha": "senha-forte-1"})
    rede = Rede(client)
    pausas = []
    api = ClienteApi(client, dormir=pausas.append, base_s=0.5, teto_s=8.0)
    api.login("ana@x.com", "senha-forte-1")
    return client, rede, api, pausas, Outbox()


def test_offline_enfileira_e_sincroniza_ao_reconectar(cenario):
    client, rede, api, pausas, fila = cenario
    rede.offline = True
    for i in range(3):
        fila.enfileirar({**OC, "descricao": f"Relato offline {i}"})
    r = sincronizar(fila, api)
    assert r["enviados"] == 0 and r["fila"] == {"pendente": 3}
    assert rede.original("GET", "/ocorrencias").json()["total"] == 0
    rede.offline = False
    api.falhas_seguidas = 0
    api.aberto_ate = 0
    r = sincronizar(fila, api)
    assert r["enviados"] == 3 and r["fila"] == {"sincronizado": 3}
    assert client.get("/ocorrencias").json()["total"] == 3


def test_retry_com_backoff_recupera_de_503(cenario):
    client, rede, api, pausas, fila = cenario
    fila.enfileirar(OC)
    rede.erros_503 = 2
    r = sincronizar(fila, api)
    assert r["enviados"] == 1 and len(pausas) == 2
    assert pausas[0] <= 0.5 and pausas[1] <= 1.0 and all(p <= 8.0 for p in pausas)  # teto do backoff


def test_retry_nao_duplica_quando_resposta_se_perde(cenario):
    """Servidor grava, mas a resposta 'some': o reenvio com a mesma chave não duplica."""
    client, rede, api, pausas, fila = cenario
    chave = fila.enfileirar(OC)
    item = fila.pendentes()[0]
    client.post("/ocorrencias", json=OC, headers={"Authorization": f"Bearer {api.token}",
                                                  "Idempotency-Key": chave})
    r = sincronizar(fila, api)
    assert r["enviados"] == 1 and client.get("/ocorrencias").json()["total"] == 1


def test_circuit_breaker_abre_e_fecha(cenario):
    client, rede, api, pausas, fila = cenario
    t = [0.0]
    api.relogio, api.limiar, api.pausa = (lambda: t[0]), 3, 30.0
    rede.erros_503 = 99
    with pytest.raises(FalhaDeRede):
        api.request("GET", "/health")
    chamadas = rede.chamadas
    with pytest.raises(CircuitoAberto):  # circuito aberto: nem tenta a rede
        api.request("GET", "/health")
    assert rede.chamadas == chamadas
    t[0] = 31.0
    rede.erros_503 = 0
    assert api.request("GET", "/health").status_code == 200


def test_erro_422_marca_item_como_erro_e_segue_a_fila(cenario):
    client, rede, api, pausas, fila = cenario
    fila.enfileirar({**OC, "lat": 500})
    fila.enfileirar(OC)
    r = sincronizar(fila, api)
    assert r["enviados"] == 1 and r["erros"] == 1 and r["fila"] == {"erro": 1, "sincronizado": 1}


def test_sessao_expirada_levanta_excecao(cenario):
    client, rede, api, pausas, fila = cenario
    api.token = "token-invalido"
    fila.enfileirar(OC)
    with pytest.raises(SessaoExpirada):
        sincronizar(fila, api)
    assert fila.contagem() == {"pendente": 1}  # nada é perdido
