"""Regras de alerta e difusão em tempo real (WebSocket)."""
from datetime import datetime, timedelta, timezone

from fastapi import WebSocket

JANELA_MIN = 60
MIN_RELATOS_ALAGAMENTO = 3
JANELA_CEMADEN_DIAS = 2     # só ocorrências recentes geram alerta (a carga histórica não)


def regiao_de(lat: float, lon: float) -> str:
    """Célula de grade de ~1 km (0,01 grau) usada como 'região' de alerta."""
    return f"{round(lat, 2):.2f},{round(lon, 2):.2f}"


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GerenciadorWS:
    def __init__(self):
        self.conexoes: set[WebSocket] = set()

    async def conectar(self, ws: WebSocket):
        await ws.accept()
        self.conexoes.add(ws)

    def desconectar(self, ws: WebSocket):
        self.conexoes.discard(ws)

    async def difundir(self, mensagem: dict):
        for ws in list(self.conexoes):
            try:
                await ws.send_json(mensagem)
            except Exception:  # cliente caiu: remove sem derrubar os demais
                self.desconectar(ws)


def _ja_existe(db, tipo: str, regiao: str, desde: str) -> bool:
    return db.um(
        "SELECT 1 FROM alerta WHERE tipo=? AND regiao=? AND criado_em>=?",
        (tipo, regiao, desde),
    ) is not None


def _criar(db, tipo, regiao, nivel, mensagem, agora: datetime) -> dict:
    criado = fmt(agora)
    aid = db.executar(
        "INSERT INTO alerta(tipo,regiao,nivel,mensagem,criado_em) VALUES (?,?,?,?,?)",
        (tipo, regiao, nivel, mensagem, criado),
    )
    return {"id": aid, "tipo": tipo, "regiao": regiao, "nivel": nivel,
            "mensagem": mensagem, "criado_em": criado}


def avaliar_ocorrencia(db, tipo: str, regiao: str, agora: datetime) -> dict | None:
    """Alerta quando há vários relatos de alagamento não refutados na mesma célula."""
    if tipo != "ALAGAMENTO":
        return None
    desde = fmt(agora - timedelta(minutes=JANELA_MIN))
    n = db.um(
        "SELECT COUNT(*) c FROM ocorrencia WHERE tipo='ALAGAMENTO' AND regiao=? "
        "AND status!='REFUTADA' AND criada_em>=?", (regiao, desde))["c"]
    if n >= MIN_RELATOS_ALAGAMENTO and not _ja_existe(db, "ALAGAMENTO_COLABORATIVO", regiao, desde):
        return _criar(db, "ALAGAMENTO_COLABORATIVO", regiao, "ALTO",
                      f"{n} relatos de alagamento na região {regiao} na última hora.", agora)
    return None


def avaliar_cemaden(db, reg: dict, agora: datetime) -> dict | None:
    """Alerta oficial para ocorrência hidrológica recente do CEMADEN.

    Magnitude GP gera nível ALTO; MP, ATENCAO; PP não gera alerta. Um alerta por
    município e nível dentro da janela (deduplicação).
    """
    if reg["tipologia"] != "HIDRO" or reg["magnitude"] == "PP":
        return None
    limite = (agora - timedelta(days=JANELA_CEMADEN_DIAS)).date().isoformat()
    if str(reg["data"]) < limite:
        return None
    nivel = "ALTO" if reg["magnitude"] == "GP" else "ATENCAO"
    regiao = f'{reg["municipio"]}/{reg["uf"]}'
    desde = fmt(agora - timedelta(days=JANELA_CEMADEN_DIAS))
    tipo = f"CEMADEN_{nivel}"
    if _ja_existe(db, tipo, regiao, desde):
        return None
    return _criar(db, tipo, regiao, nivel,
                  f"CEMADEN registrou ocorrência hidrológica (magnitude {reg['magnitude']}) "
                  f"em {regiao} em {reg['data']}.", agora)
