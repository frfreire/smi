"""Camada de persistência (SQLite). Em produção, trocar por PostgreSQL/PostGIS."""
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS usuario (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    senha_hash TEXT NOT NULL,
    papel TEXT NOT NULL DEFAULT 'cidadao'
);
CREATE TABLE IF NOT EXISTS ocorrencia (
    id TEXT PRIMARY KEY,
    tipo TEXT NOT NULL,
    descricao TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    regiao TEXT NOT NULL,
    severidade INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'PENDENTE',
    versao INTEGER NOT NULL DEFAULT 1,
    criada_em TEXT NOT NULL,
    criada_em_cliente TEXT,
    atualizada_em TEXT NOT NULL,
    midia_url TEXT,
    usuario_id INTEGER NOT NULL REFERENCES usuario(id)
);
CREATE INDEX IF NOT EXISTS ix_oc_regiao ON ocorrencia(regiao, criada_em);
CREATE INDEX IF NOT EXISTS ix_oc_geo ON ocorrencia(lat, lon);
CREATE TABLE IF NOT EXISTS voto (
    ocorrencia_id TEXT NOT NULL REFERENCES ocorrencia(id),
    usuario_id INTEGER NOT NULL REFERENCES usuario(id),
    valor INTEGER NOT NULL CHECK (valor IN (1, -1)),
    PRIMARY KEY (ocorrencia_id, usuario_id)
);
CREATE TABLE IF NOT EXISTS registro_cemaden (
    id_externo INTEGER PRIMARY KEY,          -- "ID ocorrência" do CEMADEN
    data TEXT NOT NULL,                      -- AAAA-MM-DD
    uf TEXT NOT NULL,
    municipio TEXT NOT NULL,
    tipologia TEXT NOT NULL,                 -- HIDRO | GEO | NAO_CLASSIFICADO
    magnitude TEXT NOT NULL,                 -- PP | MP | GP
    precisao_local TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    regiao TEXT NOT NULL,
    precisao_horario TEXT,
    horario TEXT,
    importado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cem_uf_data ON registro_cemaden(uf, data);
CREATE INDEX IF NOT EXISTS ix_cem_data ON registro_cemaden(data);
CREATE INDEX IF NOT EXISTS ix_cem_geo ON registro_cemaden(lat, lon);
CREATE TABLE IF NOT EXISTS alerta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL,
    regiao TEXT NOT NULL,
    nivel TEXT NOT NULL,
    mensagem TEXT NOT NULL,
    criado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotencia (
    chave TEXT NOT NULL,
    usuario_id INTEGER NOT NULL,
    hash_payload TEXT NOT NULL,
    ocorrencia_id TEXT NOT NULL,
    PRIMARY KEY (chave, usuario_id)
);
"""


class Database:
    def __init__(self, caminho: str = ":memory:"):
        self.conn = sqlite3.connect(caminho, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def consultar(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def um(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def executar(self, sql: str, params: tuple = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur.lastrowid

    @contextmanager
    def transacao(self):
        with self.lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def saudavel(self) -> bool:
        try:
            self.um("SELECT 1")
            return True
        except sqlite3.Error:
            return False
