"""Testes de integração da API: caminhos 2xx, 4xx e regras de sistemas distribuídos."""
from datetime import timedelta

import pytest

from conftest import OC, auth, novo_usuario, token


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["db"] is True
    assert "X-Request-ID" in r.headers


def test_registro_login_e_duplicidade(client):
    body = {"nome": "Ana", "email": "ana@x.com", "senha": "senha-forte-1"}
    assert client.post("/auth/registro", json=body).status_code == 201
    assert client.post("/auth/registro", json=body).status_code == 409
    assert client.post("/auth/login", json={"email": "ana@x.com", "senha": "errada-123"}).status_code == 401
    assert client.post("/auth/login", json={"email": "ana@x.com", "senha": "senha-forte-1"}).status_code == 200


def test_validacao_422_por_campo(client):
    h = novo_usuario(client, 1)
    r = client.post("/ocorrencias", json={**OC, "lat": 999, "descricao": "x"}, headers=h)
    assert r.status_code == 422
    assert set(r.json()["campos"]) >= {"lat", "descricao"}


def test_sem_token_e_token_invalido_401(client):
    assert client.post("/ocorrencias", json=OC).status_code == 401
    r = client.post("/ocorrencias", json=OC, headers=auth("lixo"))
    assert r.status_code == 401 and r.json()["erro"] == "token_invalido"


def test_token_expirado_401(app):
    from fastapi.testclient import TestClient
    from backend.api.main import create_app
    a = create_app(":memory:", segredo="segredo-de-teste-com-32-caracteres-ok", validade_token_s=-1)
    with TestClient(a) as c:
        c.post("/auth/registro", json={"nome": "Ana", "email": "a@x.com", "senha": "senha-forte-1"})
        tk = token(c, "a@x.com", "senha-forte-1")
        r = c.post("/ocorrencias", json=OC, headers=auth(tk))
        assert r.status_code == 401 and r.json()["erro"] == "token_expirado"


def test_criar_e_listar_com_filtros_e_paginacao(client):
    h = novo_usuario(client, 1)
    for i in range(5):
        client.post("/ocorrencias", json={**OC, "lat": -23.5 - i * 0.1}, headers=h)
    client.post("/ocorrencias", json={**OC, "tipo": "TRANSITO"}, headers=h)
    r = client.get("/ocorrencias", params={"tipo": "ALAGAMENTO", "page_size": 2, "page": 3}).json()
    assert r["total"] == 5 and len(r["itens"]) == 1
    box = client.get("/ocorrencias", params={"bbox": "-47,-23.6,-46,-23.4"}).json()
    assert 1 <= box["total"] <= 6
    assert client.get("/ocorrencias", params={"bbox": "a,b"}).status_code == 422


def test_idempotencia_nao_duplica(client):
    h = novo_usuario(client, 1)
    k = {**h, "Idempotency-Key": "chave-1"}
    r1 = client.post("/ocorrencias", json=OC, headers=k)
    r2 = client.post("/ocorrencias", json=OC, headers=k)
    assert r1.status_code == 201 and r2.status_code == 200
    assert r2.headers["Idempotent-Replay"] == "true" and r1.json()["id"] == r2.json()["id"]
    assert client.get("/ocorrencias").json()["total"] == 1
    r3 = client.post("/ocorrencias", json={**OC, "descricao": "outro conteudo"}, headers=k)
    assert r3.status_code == 409 and r3.json()["erro"] == "idempotency_key_reutilizada"


def test_conflito_de_versao_409(client):
    h = novo_usuario(client, 1)
    oc = client.post("/ocorrencias", json=OC, headers=h).json()
    ok = client.patch(f"/ocorrencias/{oc['id']}", json={"versao": 1, "severidade": 5}, headers=h)
    assert ok.status_code == 200 and ok.json()["versao"] == 2
    velho = client.patch(f"/ocorrencias/{oc['id']}", json={"versao": 1, "severidade": 2}, headers=h)
    assert velho.status_code == 409 and velho.json()["atual"]["severidade"] == 5


def test_permissoes_patch_e_status(client):
    dono, outro = novo_usuario(client, 1), novo_usuario(client, 2)
    oc = client.post("/ocorrencias", json=OC, headers=dono).json()
    assert client.patch(f"/ocorrencias/{oc['id']}", json={"versao": 1, "severidade": 2}, headers=outro).status_code == 403
    assert client.patch(f"/ocorrencias/{oc['id']}", json={"versao": 1, "status": "RESOLVIDA"}, headers=dono).status_code == 403
    mod = auth(token(client, "mod@x.com", "senha-mod-123"))
    r = client.patch(f"/ocorrencias/{oc['id']}", json={"versao": 1, "status": "RESOLVIDA"}, headers=mod)
    assert r.status_code == 200 and r.json()["status"] == "RESOLVIDA"
    assert client.get("/ocorrencias/inexistente").status_code == 404


def test_votacao_confirma_ocorrencia(client):
    dono = novo_usuario(client, 0)
    oc = client.post("/ocorrencias", json=OC, headers=dono).json()
    assert client.post(f"/ocorrencias/{oc['id']}/votos", json={"valor": 1}, headers=dono).status_code == 403
    for i in range(1, 4):
        r = client.post(f"/ocorrencias/{oc['id']}/votos", json={"valor": 1}, headers=novo_usuario(client, i))
    assert r.json()["status"] == "CONFIRMADA" and r.json()["confirmacoes"] == 3
    assert client.post(f"/ocorrencias/{oc['id']}/votos", json={"valor": 7}, headers=dono).status_code in (403, 422)


def test_alerta_colaborativo_e_websocket(client, app):
    h = novo_usuario(client, 1)
    tk = h["Authorization"].split()[1]
    with client.websocket_connect(f"/ws/alertas?token={tk}") as ws:
        for _ in range(3):
            client.post("/ocorrencias", json=OC, headers=h)
        eventos = []
        for _ in range(4):  # 3x ocorrencia_criada + 1 alerta
            eventos.append(ws.receive_json())
    assert [e["evento"] for e in eventos].count("alerta") == 1
    alerta = next(e for e in eventos if e["evento"] == "alerta")
    assert alerta["nivel"] == "ALTO" and alerta["tipo"] == "ALAGAMENTO_COLABORATIVO"
    client.post("/ocorrencias", json=OC, headers=h)  # 4o relato na janela: sem alerta duplicado
    assert len(client.get("/alertas").json()["itens"]) == 1


def test_websocket_rejeita_token_invalido(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/alertas?token=ruim"):
            pass


def reg(i, **kw):
    base = {"id_externo": i, "data": "2026-01-15", "uf": "SP", "municipio": "SAO PAULO",
            "tipologia": "HIDRO", "magnitude": "PP", "lat": -23.55, "lon": -46.63,
            "precisao_local": "MEDIA PRECISAO", "horario": "Tarde - 12:01 as 18:00"}
    return {**base, **kw}


def test_cemaden_lote_exige_papel_e_e_idempotente(client):
    cidadao = novo_usuario(client, 1)
    assert client.post("/cemaden/lote", json={"registros": [reg(1)]}, headers=cidadao).status_code == 403
    svc = auth(token(client, "svc@x.com", "senha-svc-123"))
    r1 = client.post("/cemaden/lote", json={"registros": [reg(1), reg(2, uf="RJ")]}, headers=svc).json()
    assert (r1["inseridos"], r1["atualizados"]) == (2, 0)
    r2 = client.post("/cemaden/lote", json={"registros": [reg(1, magnitude="MP")]}, headers=svc).json()
    assert (r2["inseridos"], r2["atualizados"]) == (0, 1)
    itens = client.get("/cemaden/ocorrencias").json()
    assert itens["total"] == 2
    assert next(i for i in itens["itens"] if i["id_externo"] == 1)["magnitude"] == "MP"


def test_cemaden_validacao_rejeita_lote_invalido(client):
    svc = auth(token(client, "svc@x.com", "senha-svc-123"))
    r = client.post("/cemaden/lote", json={"registros": [reg(1, lat=10.0), reg(2, magnitude="XX")]}, headers=svc)
    assert r.status_code == 422
    assert client.post("/cemaden/lote", json={"registros": []}, headers=svc).status_code == 422
    assert client.get("/cemaden/ocorrencias").json()["total"] == 0  # nada foi gravado


def test_cemaden_filtros_resumo_e_paginacao(client):
    svc = auth(token(client, "svc@x.com", "senha-svc-123"))
    lote = [reg(1), reg(2, magnitude="GP", data="2026-02-10"), reg(3, uf="RJ", municipio="NITEROI", lat=-22.9, lon=-43.1),
            reg(4, tipologia="GEO", data="2026-02-11")]
    client.post("/cemaden/lote", json={"registros": lote}, headers=svc)
    assert client.get("/cemaden/ocorrencias", params={"uf": "rj"}).json()["total"] == 1
    assert client.get("/cemaden/ocorrencias", params={"tipologia": "GEO"}).json()["total"] == 1
    assert client.get("/cemaden/ocorrencias", params={"desde": "2026-02-01"}).json()["total"] == 2
    assert client.get("/cemaden/ocorrencias", params={"municipio": "nite"}).json()["total"] == 1
    assert client.get("/cemaden/ocorrencias", params={"desde": "ontem"}).status_code == 422
    p = client.get("/cemaden/ocorrencias", params={"page_size": 3, "page": 2}).json()
    assert p["total"] == 4 and len(p["itens"]) == 1
    r = client.get("/cemaden/resumo").json()
    assert r["total"] == 4 and r["por_uf"] == {"SP": 3, "RJ": 1}
    assert r["por_tipologia"] == {"HIDRO": 3, "GEO": 1} and r["por_magnitude"]["GP"] == 1
    assert r["por_mes"] == {"2026-01": 2, "2026-02": 2}
    assert r["primeira_data"] == "2026-01-15" and r["ultima_data"] == "2026-02-11"


def test_cemaden_proximas_usa_raio(client):
    svc = auth(token(client, "svc@x.com", "senha-svc-123"))
    client.post("/cemaden/lote", json={"registros": [reg(1), reg(2, lat=-22.9, lon=-43.1)]}, headers=svc)
    r = client.get("/cemaden/proximas", params={"lat": -23.55, "lon": -46.63, "raio_km": 10, "dias": 400}).json()
    assert r["total"] == 1 and r["itens"][0]["id_externo"] == 1 and r["itens"][0]["distancia_km"] < 0.1
    amplo = client.get("/cemaden/proximas", params={"lat": -23.55, "lon": -46.63, "raio_km": 200, "dias": 400}).json()
    assert amplo["total"] == 1  # Niterói fica a ~360 km
    assert client.get("/cemaden/proximas", params={"lat": 99, "lon": 0}).status_code == 422


def test_alerta_oficial_so_para_ocorrencia_recente_e_nao_duplica(client):
    svc = auth(token(client, "svc@x.com", "senha-svc-123"))
    # relógio dos testes: 2026-01-15; histórico antigo não alerta
    r = client.post("/cemaden/lote", json={"registros": [reg(1, magnitude="GP", data="2025-12-01")]}, headers=svc).json()
    assert r["alertas_gerados"] == 0
    r = client.post("/cemaden/lote", json={"registros": [
        reg(2, magnitude="GP", data="2026-01-15"), reg(3, magnitude="GP", data="2026-01-14"),
        reg(4, magnitude="MP", data="2026-01-15", municipio="OSASCO"),
        reg(5, magnitude="PP", data="2026-01-15", municipio="BARUERI"),
        reg(6, tipologia="GEO", magnitude="GP", data="2026-01-15", municipio="COTIA")]}, headers=svc).json()
    assert r["alertas_gerados"] == 2   # GP em SAO PAULO (dedup) + MP em OSASCO
    niveis = sorted(a["nivel"] for a in client.get("/alertas").json()["itens"])
    assert niveis == ["ALTO", "ATENCAO"]
    assert client.post("/cemaden/lote", json={"registros": [reg(2, magnitude="GP", data="2026-01-15")]},
                       headers=svc).json()["alertas_gerados"] == 0   # reenvio não gera alerta


def test_relatorio_resumo_e_csv(client, app):
    h = novo_usuario(client, 1)
    client.post("/ocorrencias", json=OC, headers=h)
    app.state.relogio.t += timedelta(days=2)
    client.post("/ocorrencias", json={**OC, "tipo": "TRANSITO"}, headers=h)
    r = client.get("/relatorios/resumo", params={"periodo": "7d"}).json()
    assert r["total"] == 2 and r["por_tipo"] == {"ALAGAMENTO": 1, "TRANSITO": 1} and len(r["por_dia"]) == 2
    assert client.get("/relatorios/resumo", params={"periodo": "24h"}).json()["total"] == 1
    assert r["cemaden_registros"] == 0
    assert client.get("/relatorios/resumo", params={"periodo": "abc"}).status_code == 422
    assert client.get("/relatorios/ocorrencias.csv", headers=h).status_code == 403
    mod = auth(token(client, "mod@x.com", "senha-mod-123"))
    csv_ = client.get("/relatorios/ocorrencias.csv", headers=mod)
    assert csv_.status_code == 200 and csv_.text.splitlines()[0].startswith("id,tipo") and len(csv_.text.splitlines()) == 3


def test_erro_interno_nao_vaza_detalhes(app):
    from fastapi.testclient import TestClient

    @app.get("/boom")
    async def boom():
        raise RuntimeError("segredo interno")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/boom")
    assert r.status_code == 500 and "segredo" not in r.text and r.json()["erro"] == "erro_interno"
