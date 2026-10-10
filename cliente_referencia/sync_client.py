"""Cliente de referência: fila offline + retry com backoff + circuit breaker.

Implementa em Python a mesma lógica do app Flutter (mobile/lib/sync), para que
o comportamento distribuído possa ser testado automaticamente.
"""
import json
import random
import sqlite3
import time
import uuid

import httpx

PENDENTE, SINCRONIZADO, ERRO = "pendente", "sincronizado", "erro"


class SessaoExpirada(Exception):
    pass


class CircuitoAberto(Exception):
    pass


class FalhaDeRede(Exception):
    pass


class Outbox:
    """Fila local persistente de ocorrências ainda não enviadas ao servidor."""

    def __init__(self, caminho: str = ":memory:"):
        self.c = sqlite3.connect(caminho)
        self.c.row_factory = sqlite3.Row
        self.c.execute("""CREATE TABLE IF NOT EXISTS fila (
            id_local INTEGER PRIMARY KEY AUTOINCREMENT, chave TEXT UNIQUE NOT NULL,
            payload TEXT NOT NULL, estado TEXT NOT NULL DEFAULT 'pendente',
            tentativas INTEGER NOT NULL DEFAULT 0, ultimo_erro TEXT, id_remoto TEXT)""")
        self.c.commit()

    def enfileirar(self, payload: dict) -> str:
        chave = str(uuid.uuid4())
        self.c.execute("INSERT INTO fila(chave,payload) VALUES (?,?)", (chave, json.dumps(payload)))
        self.c.commit()
        return chave

    def pendentes(self) -> list[sqlite3.Row]:
        return self.c.execute("SELECT * FROM fila WHERE estado='pendente' ORDER BY id_local").fetchall()

    def marcar(self, chave, estado, erro=None, id_remoto=None):
        self.c.execute("UPDATE fila SET estado=?, ultimo_erro=?, id_remoto=COALESCE(?,id_remoto),"
                       " tentativas=tentativas+1 WHERE chave=?", (estado, erro, id_remoto, chave))
        self.c.commit()

    def contagem(self) -> dict:
        rs = self.c.execute("SELECT estado, COUNT(*) n FROM fila GROUP BY estado").fetchall()
        return {r["estado"]: r["n"] for r in rs}


class ClienteApi:
    def __init__(self, http: httpx.Client, max_tentativas=4, base_s=0.5, teto_s=8.0,
                 timeout_s=8.0, limiar_circuito=5, pausa_circuito_s=30.0,
                 dormir=time.sleep, relogio=time.monotonic):
        self.http, self.max_tentativas = http, max_tentativas
        self.base_s, self.teto_s, self.timeout_s = base_s, teto_s, timeout_s
        self.limiar, self.pausa = limiar_circuito, pausa_circuito_s
        self.dormir, self.relogio = dormir, relogio
        self.token: str | None = None
        self.falhas_seguidas, self.aberto_ate = 0, 0.0
        self.esperas: list[float] = []  # histórico para testes/relatório

    def _espera(self, n: int) -> float:
        """Backoff exponencial com jitter total: U(0, min(teto, base*2^n))."""
        return random.uniform(0, min(self.teto_s, self.base_s * 2 ** n))

    def request(self, metodo: str, caminho: str, **kw) -> httpx.Response:
        if self.relogio() < self.aberto_ate:
            raise CircuitoAberto("Servidor indisponível; nova tentativa em breve.")
        headers = dict(kw.pop("headers", {}))
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        ultimo = None
        for n in range(self.max_tentativas):
            try:
                r = self.http.request(metodo, caminho, headers=headers, timeout=self.timeout_s, **kw)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                ultimo = FalhaDeRede(str(e))
            else:
                if r.status_code == 401:
                    raise SessaoExpirada(r.json().get("erro", "nao_autenticado"))
                if r.status_code < 500 and r.status_code != 429:
                    self.falhas_seguidas = 0
                    return r
                ultimo = FalhaDeRede(f"HTTP {r.status_code}")
            self.falhas_seguidas += 1
            if self.falhas_seguidas >= self.limiar:
                self.aberto_ate = self.relogio() + self.pausa
                break
            if n < self.max_tentativas - 1:
                espera = self._espera(n)
                self.esperas.append(espera)
                self.dormir(espera)
        raise ultimo or FalhaDeRede("falha desconhecida")

    def login(self, email: str, senha: str):
        r = self.request("POST", "/auth/login", json={"email": email, "senha": senha})
        r.raise_for_status()
        self.token = r.json()["access_token"]


def sincronizar(outbox: Outbox, api: ClienteApi) -> dict:
    """Envia a fila em ordem. Para ao primeiro erro de rede (offline); erros 4xx são definitivos."""
    enviados = erros = 0
    for item in outbox.pendentes():
        try:
            r = api.request("POST", "/ocorrencias", json=json.loads(item["payload"]),
                            headers={"Idempotency-Key": item["chave"]})
        except (FalhaDeRede, CircuitoAberto):
            break  # continua pendente; tenta de novo na próxima sincronização
        if r.status_code in (200, 201):
            outbox.marcar(item["chave"], SINCRONIZADO, id_remoto=r.json()["id"])
            enviados += 1
        else:
            outbox.marcar(item["chave"], ERRO, erro=json.dumps(r.json().get("campos") or r.json()))
            erros += 1
    return {"enviados": enviados, "erros": erros, **{"fila": outbox.contagem()}}
