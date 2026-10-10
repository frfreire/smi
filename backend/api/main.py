"""API REST + WebSocket do sistema de monitoramento de alagamentos urbanos."""
import csv
import hashlib
import io
import json
import logging
import math
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, FastAPI, Header, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .alertas import GerenciadorWS, avaliar_cemaden, avaliar_ocorrencia, fmt, regiao_de
from .db import Database
from .schemas import (LoginIn, LoteCemadenIn, Magnitude, OcorrenciaIn, OcorrenciaPatch, RegistroIn,
                      StatusOcorrencia, TipoOcorrencia, Tipologia, VotoIn)
from .security import decodificar_token, emitir_token, hash_senha, verificar_senha

VERSAO = "1.0.0"
log = logging.getLogger("aps")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


class ErroApi(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str,
                 campos: dict | None = None, extra: dict | None = None):
        self.status, self.codigo, self.mensagem = status, codigo, mensagem
        self.campos, self.extra = campos, extra or {}


def semear(db: Database, nome: str, email: str, senha: str, papel: str):
    if not db.um("SELECT 1 FROM usuario WHERE email=?", (email,)):
        db.executar("INSERT INTO usuario(nome,email,senha_hash,papel) VALUES (?,?,?,?)",
                    (nome, email, hash_senha(senha), papel))


def create_app(db_path: str | None = None, segredo: str | None = None,
               validade_token_s: int = 1800) -> FastAPI:
    db = Database(db_path or os.getenv("DB_PATH", ":memory:"))
    segredo = segredo or os.getenv("JWT_SECRET", "dev-secret-trocar-em-producao-32-chars-min")
    app = FastAPI(title="APS - Alagamentos Urbanos", version=VERSAO)
    app.state.db = db
    app.state.agora = lambda: datetime.now(timezone.utc)
    ws_mgr = GerenciadorWS()
    bearer = HTTPBearer(auto_error=False)

    if os.getenv("ADMIN_EMAIL"):
        semear(db, "Moderador", os.environ["ADMIN_EMAIL"], os.environ["ADMIN_SENHA"], "moderador")
    if os.getenv("SERVICO_EMAIL"):
        semear(db, "Ingestao", os.environ["SERVICO_EMAIL"], os.environ["SERVICO_SENHA"], "servico")

    # ---------------------------------------------------------------- erros
    def corpo_erro(request: Request, codigo, mensagem, campos=None, extra=None):
        c = {"erro": codigo, "mensagem": mensagem,
             "request_id": getattr(request.state, "request_id", None)}
        if campos:
            c["campos"] = campos
        c.update(extra or {})
        return c

    @app.exception_handler(ErroApi)
    async def h_erro(request: Request, e: ErroApi):
        headers = {"WWW-Authenticate": "Bearer"} if e.status == 401 else None
        return JSONResponse(corpo_erro(request, e.codigo, e.mensagem, e.campos, e.extra),
                            status_code=e.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def h_validacao(request: Request, e: RequestValidationError):
        campos = {".".join(str(p) for p in err["loc"][1:]) or str(err["loc"][0]): err["msg"]
                  for err in e.errors()}
        return JSONResponse(corpo_erro(request, "validacao", "Dados inválidos.", campos),
                            status_code=422)

    @app.exception_handler(Exception)
    async def h_interno(request: Request, e: Exception):
        log.error(json.dumps({"nivel": "ERROR", "rota": request.url.path, "erro": repr(e),
                              "request_id": getattr(request.state, "request_id", None)}))
        return JSONResponse(corpo_erro(request, "erro_interno", "Erro interno do servidor."),
                            status_code=500)

    @app.middleware("http")
    async def log_requisicoes(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        resp = await call_next(request)
        resp.headers["X-Request-ID"] = request.state.request_id
        log.info(json.dumps({"nivel": "INFO", "metodo": request.method, "rota": request.url.path,
                             "status": resp.status_code, "ms": round((time.perf_counter() - t0) * 1000, 1),
                             "request_id": request.state.request_id}))
        return resp

    # ----------------------------------------------------------- autenticação
    def usuario_atual(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
        if cred is None:
            raise ErroApi(401, "nao_autenticado", "Token de acesso ausente.")
        try:
            p = decodificar_token(segredo, cred.credentials)
        except jwt.ExpiredSignatureError:
            raise ErroApi(401, "token_expirado", "Sessão expirada. Faça login novamente.")
        except jwt.InvalidTokenError:
            raise ErroApi(401, "token_invalido", "Token inválido.")
        return {"id": int(p["sub"]), "papel": p["papel"]}

    def exigir_papel(u: dict, *papeis: str):
        if u["papel"] not in papeis:
            raise ErroApi(403, "sem_permissao", "Você não tem permissão para esta ação.")

    def oc_dict(row) -> dict:
        d = dict(row)
        d.pop("usuario_id", None)
        v = db.um("SELECT COALESCE(SUM(valor=1),0) c, COALESCE(SUM(valor=-1),0) r "
                  "FROM voto WHERE ocorrencia_id=?", (d["id"],))
        d["confirmacoes"], d["refutacoes"] = v["c"], v["r"]
        return d

    def buscar_oc(oid: str):
        row = db.um("SELECT * FROM ocorrencia WHERE id=?", (oid,))
        if not row:
            raise ErroApi(404, "nao_encontrado", "Ocorrência não encontrada.")
        return row

    # ------------------------------------------------------------------ rotas
    @app.get("/health")
    async def health(response: Response):
        ok = db.saudavel()
        if not ok:
            response.status_code = 503
        return {"status": "ok" if ok else "degradado", "db": ok, "versao": VERSAO,
                "hora": fmt(app.state.agora())}

    @app.post("/auth/registro", status_code=201)
    async def registro(corpo: RegistroIn):
        if db.um("SELECT 1 FROM usuario WHERE email=?", (corpo.email,)):
            raise ErroApi(409, "email_em_uso", "E-mail já cadastrado.")
        uid = db.executar("INSERT INTO usuario(nome,email,senha_hash) VALUES (?,?,?)",
                          (corpo.nome, corpo.email, hash_senha(corpo.senha)))
        return {"id": uid, "nome": corpo.nome, "email": corpo.email}

    @app.post("/auth/login")
    async def login(corpo: LoginIn):
        u = db.um("SELECT * FROM usuario WHERE email=?", (corpo.email,))
        if not u or not verificar_senha(corpo.senha, u["senha_hash"]):
            raise ErroApi(401, "credenciais_invalidas", "E-mail ou senha incorretos.")
        return {"access_token": emitir_token(segredo, u["id"], u["papel"], validade_token_s),
                "token_type": "bearer", "expires_in": validade_token_s, "papel": u["papel"]}

    @app.post("/ocorrencias", status_code=201)
    async def criar_ocorrencia(corpo: OcorrenciaIn, response: Response,
                               idempotency_key: str | None = Header(default=None),
                               u: dict = Depends(usuario_atual)):
        h = hashlib.sha256(corpo.model_dump_json().encode()).hexdigest()
        if idempotency_key:
            reg = db.um("SELECT * FROM idempotencia WHERE chave=? AND usuario_id=?",
                        (idempotency_key, u["id"]))
            if reg:
                if reg["hash_payload"] != h:
                    raise ErroApi(409, "idempotency_key_reutilizada",
                                  "A chave de idempotência já foi usada com outro conteúdo.")
                response.status_code = 200
                response.headers["Idempotent-Replay"] = "true"
                return oc_dict(buscar_oc(reg["ocorrencia_id"]))
        agora = app.state.agora()
        oid, regiao = str(uuid.uuid4()), regiao_de(corpo.lat, corpo.lon)
        with db.transacao() as c:
            c.execute("INSERT INTO ocorrencia(id,tipo,descricao,lat,lon,regiao,severidade,criada_em,"
                      "criada_em_cliente,atualizada_em,midia_url,usuario_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (oid, corpo.tipo.value, corpo.descricao, corpo.lat, corpo.lon, regiao,
                       corpo.severidade, fmt(agora),
                       corpo.criada_em_cliente.isoformat() if corpo.criada_em_cliente else None,
                       fmt(agora), corpo.midia_url, u["id"]))
            if idempotency_key:
                c.execute("INSERT INTO idempotencia VALUES (?,?,?,?)",
                          (idempotency_key, u["id"], h, oid))
        out = oc_dict(buscar_oc(oid))
        await ws_mgr.difundir({"evento": "ocorrencia_criada", "id": oid, "tipo": out["tipo"],
                               "lat": out["lat"], "lon": out["lon"]})
        alerta = avaliar_ocorrencia(db, out["tipo"], regiao, agora)
        if alerta:
            await ws_mgr.difundir({"evento": "alerta", **alerta})
        return out

    @app.get("/ocorrencias")
    async def listar_ocorrencias(bbox: str | None = None, tipo: TipoOcorrencia | None = None,
                                 status: StatusOcorrencia | None = None,
                                 page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        where, params = [], []
        if bbox:
            try:
                x0, y0, x1, y1 = (float(p) for p in bbox.split(","))
            except ValueError:
                raise ErroApi(422, "validacao", "Dados inválidos.",
                              {"bbox": "use minLon,minLat,maxLon,maxLat"})
            where.append("lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?")
            params += [min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)]
        if tipo:
            where.append("tipo=?"); params.append(tipo.value)
        if status:
            where.append("status=?"); params.append(status.value)
        cond = ("WHERE " + " AND ".join(where)) if where else ""
        total = db.um(f"SELECT COUNT(*) c FROM ocorrencia {cond}", tuple(params))["c"]
        rows = db.consultar(f"SELECT * FROM ocorrencia {cond} ORDER BY criada_em DESC, id LIMIT ? OFFSET ?",
                            tuple(params) + (page_size, (page - 1) * page_size))
        return {"itens": [oc_dict(r) for r in rows], "pagina": page, "tamanho": page_size, "total": total}

    @app.get("/ocorrencias/{oid}")
    async def obter_ocorrencia(oid: str):
        return oc_dict(buscar_oc(oid))

    @app.patch("/ocorrencias/{oid}")
    async def atualizar_ocorrencia(oid: str, corpo: OcorrenciaPatch, u: dict = Depends(usuario_atual)):
        row = buscar_oc(oid)
        if u["papel"] != "moderador" and row["usuario_id"] != u["id"]:
            raise ErroApi(403, "sem_permissao", "Apenas o autor ou um moderador pode editar.")
        if corpo.status is not None:
            exigir_papel(u, "moderador")
        if corpo.versao != row["versao"]:
            raise ErroApi(409, "conflito_versao", "A ocorrência foi alterada por outra pessoa.",
                          extra={"atual": oc_dict(row)})
        mudancas = {k: v for k, v in
                    {"descricao": corpo.descricao, "severidade": corpo.severidade,
                     "status": corpo.status.value if corpo.status else None}.items() if v is not None}
        if not mudancas:
            raise ErroApi(422, "validacao", "Dados inválidos.", {"corpo": "nenhum campo para alterar"})
        sets = ", ".join(f"{k}=?" for k in mudancas)
        db.executar(f"UPDATE ocorrencia SET {sets}, versao=versao+1, atualizada_em=? WHERE id=?",
                    tuple(mudancas.values()) + (fmt(app.state.agora()), oid))
        return oc_dict(buscar_oc(oid))

    @app.post("/ocorrencias/{oid}/votos")
    async def votar(oid: str, corpo: VotoIn, u: dict = Depends(usuario_atual)):
        row = buscar_oc(oid)
        if corpo.valor not in (1, -1):
            raise ErroApi(422, "validacao", "Dados inválidos.", {"valor": "use 1 ou -1"})
        if row["usuario_id"] == u["id"]:
            raise ErroApi(403, "voto_proprio", "Não é possível votar na própria ocorrência.")
        db.executar("INSERT INTO voto VALUES (?,?,?) ON CONFLICT(ocorrencia_id,usuario_id) "
                    "DO UPDATE SET valor=excluded.valor", (oid, u["id"], corpo.valor))
        d = oc_dict(buscar_oc(oid))
        novo = None
        if d["status"] == "PENDENTE":
            if d["confirmacoes"] >= 3 and d["confirmacoes"] > d["refutacoes"]:
                novo = "CONFIRMADA"
            elif d["refutacoes"] >= 3 and d["refutacoes"] > d["confirmacoes"]:
                novo = "REFUTADA"
        if novo:
            db.executar("UPDATE ocorrencia SET status=?, versao=versao+1, atualizada_em=? WHERE id=?",
                        (novo, fmt(app.state.agora()), oid))
            d = oc_dict(buscar_oc(oid))
        return d

    # ------------------------------------------------- dados oficiais (CEMADEN)
    @app.post("/cemaden/lote")
    async def importar_cemaden(corpo: LoteCemadenIn, u: dict = Depends(usuario_atual)):
        """Carga idempotente: o mesmo id_externo é inserido na primeira vez e atualizado nas seguintes."""
        exigir_papel(u, "servico", "moderador")
        agora = app.state.agora()
        inseridos = atualizados = 0
        novos_alertas = []
        with db.transacao() as c:
            for r in corpo.registros:
                existe = c.execute("SELECT 1 FROM registro_cemaden WHERE id_externo=?",
                                   (r.id_externo,)).fetchone() is not None
                c.execute(
                    "INSERT INTO registro_cemaden(id_externo,data,uf,municipio,tipologia,magnitude,"
                    "precisao_local,lat,lon,regiao,precisao_horario,horario,importado_em) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id_externo) DO UPDATE SET "
                    "data=excluded.data,uf=excluded.uf,municipio=excluded.municipio,"
                    "tipologia=excluded.tipologia,magnitude=excluded.magnitude,"
                    "precisao_local=excluded.precisao_local,lat=excluded.lat,lon=excluded.lon,"
                    "regiao=excluded.regiao,precisao_horario=excluded.precisao_horario,"
                    "horario=excluded.horario,importado_em=excluded.importado_em",
                    (r.id_externo, r.data.isoformat(), r.uf.upper(), r.municipio, r.tipologia.value,
                     r.magnitude.value, r.precisao_local, r.lat, r.lon, regiao_de(r.lat, r.lon),
                     r.precisao_horario, r.horario, fmt(agora)))
                if existe:
                    atualizados += 1
                else:
                    inseridos += 1
                    novos_alertas.append(r)
        gerados = 0
        for r in novos_alertas:
            a = avaliar_cemaden(db, {"tipologia": r.tipologia.value, "magnitude": r.magnitude.value,
                                     "data": r.data.isoformat(), "municipio": r.municipio,
                                     "uf": r.uf.upper()}, agora)
            if a:
                gerados += 1
                await ws_mgr.difundir({"evento": "alerta", **a})
        return {"recebidos": len(corpo.registros), "inseridos": inseridos,
                "atualizados": atualizados, "alertas_gerados": gerados}

    def filtros_cemaden(uf, municipio, tipologia, magnitude, desde, ate, bbox):
        where, params = [], []
        if uf:
            where.append("uf=?"); params.append(uf.upper())
        if municipio:
            where.append("municipio LIKE ?"); params.append(f"%{municipio.upper()}%")
        if tipologia:
            where.append("tipologia=?"); params.append(tipologia.value)
        if magnitude:
            where.append("magnitude=?"); params.append(magnitude.value)
        for nome, val, op in (("desde", desde, ">="), ("ate", ate, "<=")):
            if val:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
                    raise ErroApi(422, "validacao", "Dados inválidos.", {nome: "use AAAA-MM-DD"})
                where.append(f"data{op}?"); params.append(val)
        if bbox:
            try:
                x0, y0, x1, y1 = (float(p) for p in bbox.split(","))
            except ValueError:
                raise ErroApi(422, "validacao", "Dados inválidos.",
                              {"bbox": "use minLon,minLat,maxLon,maxLat"})
            where.append("lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?")
            params += [min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)]
        return ("WHERE " + " AND ".join(where)) if where else "", tuple(params)

    @app.get("/cemaden/ocorrencias")
    async def listar_cemaden(uf: str | None = Query(None, min_length=2, max_length=2),
                             municipio: str | None = None, tipologia: Tipologia | None = None,
                             magnitude: Magnitude | None = None, desde: str | None = None,
                             ate: str | None = None, bbox: str | None = None,
                             page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
        cond, params = filtros_cemaden(uf, municipio, tipologia, magnitude, desde, ate, bbox)
        total = db.um(f"SELECT COUNT(*) c FROM registro_cemaden {cond}", params)["c"]
        rows = db.consultar(f"SELECT * FROM registro_cemaden {cond} ORDER BY data DESC, id_externo DESC "
                            "LIMIT ? OFFSET ?", params + (page_size, (page - 1) * page_size))
        return {"itens": [dict(r) for r in rows], "pagina": page, "tamanho": page_size, "total": total}

    @app.get("/cemaden/resumo")
    async def resumo_cemaden(uf: str | None = Query(None, min_length=2, max_length=2),
                             tipologia: Tipologia | None = None, desde: str | None = None,
                             ate: str | None = None):
        cond, params = filtros_cemaden(uf, None, tipologia, None, desde, ate, None)
        def agrupar(expr, limite=None):
            sql = (f"SELECT {expr} k, COUNT(*) n FROM registro_cemaden {cond} GROUP BY k "
                   "ORDER BY n DESC, k" + (f" LIMIT {limite}" if limite else ""))
            return {r["k"]: r["n"] for r in db.consultar(sql, params)}
        meses = db.consultar(f"SELECT substr(data,1,7) k, COUNT(*) n FROM registro_cemaden {cond} "
                             "GROUP BY k ORDER BY k", params)
        mun = db.consultar(f"SELECT municipio, uf, COUNT(*) total, SUM(magnitude!='PP') acima_pp "
                           f"FROM registro_cemaden {cond} GROUP BY municipio, uf "
                           "ORDER BY total DESC, municipio LIMIT 5", params)
        ext = db.um(f"SELECT COUNT(*) c, MIN(data) a, MAX(data) b FROM registro_cemaden {cond}", params)
        return {"total": ext["c"], "primeira_data": ext["a"], "ultima_data": ext["b"],
                "por_uf": agrupar("uf"), "por_tipologia": agrupar("tipologia"),
                "por_magnitude": agrupar("magnitude"), "por_mes": {r["k"]: r["n"] for r in meses},
                "municipios_criticos": [dict(r) for r in mun]}

    def distancia_km(lat1, lon1, lat2, lon2) -> float:
        p1, p2 = math.radians(lat1), math.radians(lat2)
        h = (math.sin((p2 - p1) / 2) ** 2
             + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
        return 2 * 6371.0088 * math.asin(math.sqrt(h))

    @app.get("/cemaden/proximas")
    async def cemaden_proximas(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
                               raio_km: float = Query(25, gt=0, le=200),
                               dias: int = Query(30, ge=1, le=400),
                               tipologia: Tipologia | None = Tipologia.HIDRO):
        """Contexto oficial para um ponto: registros do CEMADEN num raio, nos últimos N dias."""
        dlat = raio_km / 111.0
        dlon = raio_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
        desde = (app.state.agora() - timedelta(days=dias)).date().isoformat()
        sql = ("SELECT * FROM registro_cemaden WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
               "AND data>=?")
        params = [lat - dlat, lat + dlat, lon - dlon, lon + dlon, desde]
        if tipologia:
            sql += " AND tipologia=?"; params.append(tipologia.value)
        itens = []
        for r in db.consultar(sql, tuple(params)):
            d = distancia_km(lat, lon, r["lat"], r["lon"])
            if d <= raio_km:
                itens.append({**dict(r), "distancia_km": round(d, 2)})
        itens.sort(key=lambda x: x["distancia_km"])
        return {"raio_km": raio_km, "dias": dias, "total": len(itens), "itens": itens[:50]}

    @app.get("/alertas")
    async def listar_alertas(desde: str | None = None):
        rows = db.consultar("SELECT * FROM alerta WHERE criado_em>=? ORDER BY criado_em DESC",
                            (desde or "0000",))
        return {"itens": [dict(r) for r in rows]}

    def desde_periodo(periodo: str) -> str:
        m = re.fullmatch(r"(\d{1,3})([hd])", periodo)
        if not m:
            raise ErroApi(422, "validacao", "Dados inválidos.", {"periodo": "use formato 24h ou 7d"})
        n = int(m.group(1))
        delta = timedelta(hours=n) if m.group(2) == "h" else timedelta(days=n)
        return fmt(app.state.agora() - delta)

    @app.get("/relatorios/resumo")
    async def relatorio_resumo(periodo: str = "7d"):
        desde = desde_periodo(periodo)
        def agrupar(col):
            rs = db.consultar(f"SELECT {col} k, COUNT(*) n FROM ocorrencia WHERE criada_em>=? "
                              f"GROUP BY {col} ORDER BY n DESC", (desde,))
            return {r["k"]: r["n"] for r in rs}
        por_dia = db.consultar("SELECT substr(criada_em,1,10) k, COUNT(*) n FROM ocorrencia "
                               "WHERE criada_em>=? GROUP BY k ORDER BY k", (desde,))
        regs = db.consultar("SELECT regiao, COUNT(*) total, MAX(severidade) severidade_max FROM ocorrencia "
                            "WHERE criada_em>=? GROUP BY regiao ORDER BY total DESC LIMIT 5", (desde,))
        return {"periodo": periodo, "desde": desde,
                "total": db.um("SELECT COUNT(*) c FROM ocorrencia WHERE criada_em>=?", (desde,))["c"],
                "por_tipo": agrupar("tipo"), "por_status": agrupar("status"),
                "por_dia": {r["k"]: r["n"] for r in por_dia},
                "regioes_criticas": [dict(r) for r in regs],
                "alertas": db.um("SELECT COUNT(*) c FROM alerta WHERE criado_em>=?", (desde,))["c"],
                "cemaden_registros": db.um("SELECT COUNT(*) c FROM registro_cemaden WHERE data>=?",
                                           (desde[:10],))["c"]}

    @app.get("/relatorios/ocorrencias.csv")
    async def relatorio_csv(periodo: str = "30d", u: dict = Depends(usuario_atual)):
        exigir_papel(u, "moderador")
        rows = db.consultar("SELECT id,tipo,status,severidade,lat,lon,regiao,criada_em,descricao "
                            "FROM ocorrencia WHERE criada_em>=? ORDER BY criada_em", (desde_periodo(periodo),))
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "tipo", "status", "severidade", "lat", "lon", "regiao", "criada_em", "descricao"])
        for r in rows:
            w.writerow(list(r))
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=ocorrencias.csv"})

    @app.websocket("/ws/alertas")
    async def ws_alertas(ws: WebSocket, token: str = ""):
        try:
            decodificar_token(segredo, token)
        except jwt.InvalidTokenError:
            await ws.close(code=4401)
            return
        await ws_mgr.conectar(ws)
        try:
            while True:
                if await ws.receive_text() == "ping":
                    await ws.send_text("pong")
        except WebSocketDisconnect:
            ws_mgr.desconectar(ws)

    return app


app = create_app() if os.getenv("APS_AUTOSTART", "1") == "1" else None
