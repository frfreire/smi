import os
import sys
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

os.environ["APS_AUTOSTART"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backend.api.main import create_app, semear  # noqa: E402


class Relogio:
    def __init__(self):
        self.t = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t


@pytest.fixture()
def app():
    a = create_app(":memory:", segredo="segredo-de-teste-com-32-caracteres-ok")
    a.state.relogio = Relogio()
    a.state.agora = a.state.relogio
    semear(a.state.db, "Mod", "mod@x.com", "senha-mod-123", "moderador")
    semear(a.state.db, "Svc", "svc@x.com", "senha-svc-123", "servico")
    return a


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


def token(client, email, senha):
    r = client.post("/auth/login", json={"email": email, "senha": senha})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth(tk):
    return {"Authorization": f"Bearer {tk}"}


def novo_usuario(client, n):
    email = f"u{n}@x.com"
    r = client.post("/auth/registro", json={"nome": f"Usuario {n}", "email": email, "senha": "senha-forte-1"})
    assert r.status_code == 201, r.text
    return auth(token(client, email, "senha-forte-1"))


OC = {"tipo": "ALAGAMENTO", "descricao": "Rua alagada na altura do 100",
      "lat": -23.5505, "lon": -46.6333, "severidade": 3}
