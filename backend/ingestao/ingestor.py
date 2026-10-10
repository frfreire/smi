"""Serviço de ingestão: carrega a tabela de ocorrências do CEMADEN na API.

Fonte: CSV exportado do portal do CEMADEN (separador ';', UTF-8 com BOM). Cada linha é
validada e normalizada; linhas inválidas são descartadas e contadas (não interrompem a
carga). O envio é feito em lotes idempotentes (mesmo "ID ocorrência" => atualiza, não duplica),
com nova tentativa e recuo exponencial em falhas de rede ou 5xx.
"""
import csv
import os
import random
import time
import unicodedata
from collections import Counter
from pathlib import Path

import httpx

TAMANHO_LOTE = 200
TIPOLOGIAS = {"HIDRO": "HIDRO", "GEO": "GEO", "NAO CLASSIFICADO": "NAO_CLASSIFICADO"}
MAGNITUDES = {"PP", "MP", "GP"}


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def _texto(v) -> str | None:
    v = (v or "").strip()
    return v or None


def normalizar_linha(linha: dict) -> dict:
    """Converte uma linha do CSV no contrato da API. Levanta ValueError se for inválida."""
    try:
        lat, lon = float(linha["Latitude"]), float(linha["Longitude"])
        id_externo = int(linha["ID ocorrência"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("campo_numerico_invalido")
    if lat == 0.0 and lon == 0.0:
        raise ValueError("coordenada_nula")          # 0,0 é marcador de "sem localização"
    if not (-35 <= lat <= 6 and -75 <= lon <= -28):
        raise ValueError("fora_do_brasil")
    tipologia = TIPOLOGIAS.get(_sem_acento((linha.get("Tipologia") or "")).strip().upper())
    if tipologia is None:
        raise ValueError("tipologia_desconhecida")
    magnitude = (linha.get("Magnitude") or "").strip().upper()
    if magnitude not in MAGNITUDES:
        raise ValueError("magnitude_desconhecida")
    uf = (linha.get("UF") or "").strip().upper()
    municipio = (linha.get("Município") or "").strip()
    data = (linha.get("Data") or "").strip()
    if len(uf) != 2 or not municipio or len(data) != 10:
        raise ValueError("campo_obrigatorio_ausente")
    return {"id_externo": id_externo, "data": data, "uf": uf, "municipio": municipio,
            "tipologia": tipologia, "magnitude": magnitude, "lat": lat, "lon": lon,
            "precisao_local": _texto(linha.get("Precisão")),
            "precisao_horario": _texto(linha.get("Precisão do horário")),
            "horario": _texto(linha.get("Horário da ocorrência"))}


def ler_csv(caminho) -> tuple[list[dict], Counter]:
    """Lê o CSV; devolve (registros válidos, contagem de descartes por motivo)."""
    validos, descartes, vistos = [], Counter(), set()
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        for linha in csv.DictReader(f, delimiter=";"):
            try:
                r = normalizar_linha(linha)
            except ValueError as e:
                descartes[str(e)] += 1
                continue
            if r["id_externo"] in vistos:
                descartes["id_duplicado"] += 1
                continue
            vistos.add(r["id_externo"])
            validos.append(r)
    return validos, descartes


def enviar_lote(http, lote: list[dict], token: str, tentativas: int = 4, base_s: float = 0.5,
                dormir=time.sleep) -> dict:
    """Envia um lote com retry (rede/5xx) e backoff exponencial com jitter. 4xx não é repetido."""
    for n in range(tentativas):
        try:
            r = http.post("/cemaden/lote", json={"registros": lote},
                          headers={"Authorization": f"Bearer {token}"})
            if r.status_code < 500:
                if r.status_code != 200:
                    raise RuntimeError(f"lote rejeitado: HTTP {r.status_code} {r.text[:200]}")
                return r.json()
        except httpx.TransportError:
            pass
        if n < tentativas - 1:
            dormir(random.uniform(0, min(8.0, base_s * 2 ** n)))
    raise ConnectionError("API indisponível após várias tentativas")


def carregar(http, token: str, registros: list[dict], tam_lote: int = TAMANHO_LOTE,
             dormir=time.sleep) -> dict:
    total = Counter()
    for i in range(0, len(registros), tam_lote):
        total.update(enviar_lote(http, registros[i:i + tam_lote], token, dormir=dormir))
    return dict(total)


def main():  # pragma: no cover
    api = os.getenv("API_URL", "http://api:8000")
    caminho = Path(os.getenv("CSV_PATH", "dados/cemaden/reindesc_tabela_ocorrencias.csv"))
    intervalo = int(os.getenv("INTERVALO_S", "600"))
    visto = None
    while True:
        try:
            mtime = caminho.stat().st_mtime
            if mtime != visto:                      # recarrega só quando o arquivo muda
                registros, descartes = ler_csv(caminho)
                print(f"lidos={len(registros)} descartados={dict(descartes)}", flush=True)
                with httpx.Client(base_url=api, timeout=8.0) as h:
                    tk = h.post("/auth/login", json={"email": os.environ["SERVICO_EMAIL"],
                                                     "senha": os.environ["SERVICO_SENHA"]}).json()["access_token"]
                    print("carga:", carregar(h, tk, registros), flush=True)
                visto = mtime
        except Exception as e:
            print("ciclo falhou:", repr(e), flush=True)
        time.sleep(intervalo)


if __name__ == "__main__":
    main()
