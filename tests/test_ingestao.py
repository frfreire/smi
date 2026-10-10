from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import token
from backend.ingestao.ingestor import carregar, enviar_lote, ler_csv, normalizar_linha

CSV_REAL = Path(__file__).resolve().parents[1] / "dados" / "cemaden" / "reindesc_tabela_ocorrencias.csv"
CAB = "Data;UF;Município;Tipologia;Magnitude;Precisão;Latitude;Longitude;Precisão do horário;Horário da ocorrência;ID ocorrência\n"


def linha(**kw):
    base = {"Data": "2026-07-11", "UF": "SC", "Município": "LAGES", "Tipologia": "Hidro", "Magnitude": "PP",
            "Precisão": "BAIXA PRECISÃO", "Latitude": "-27.8", "Longitude": "-50.3",
            "Precisão do horário": "", "Horário da ocorrência": "", "ID ocorrência": "1"}
    return {**base, **kw}


def test_normaliza_linha_valida_e_vazios_viram_none():
    r = normalizar_linha(linha(Tipologia="Não classificado"))
    assert r["tipologia"] == "NAO_CLASSIFICADO" and r["horario"] is None and r["lat"] == -27.8


@pytest.mark.parametrize("campo,valor,motivo", [
    ("Latitude", "0.0", "coordenada_nula"), ("Latitude", "abc", "campo_numerico_invalido"),
    ("Longitude", "10", "fora_do_brasil"), ("Tipologia", "Outra", "tipologia_desconhecida"),
    ("Magnitude", "XX", "magnitude_desconhecida"), ("UF", "S", "campo_obrigatorio_ausente")])
def test_linhas_invalidas_sao_rejeitadas(campo, valor, motivo):
    kw = {campo: valor}
    if campo == "Latitude" and valor == "0.0":
        kw["Longitude"] = "0.0"
    with pytest.raises(ValueError, match=motivo):
        normalizar_linha(linha(**kw))


def test_ler_csv_descarta_invalidos_e_duplicados(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("﻿" + CAB
                 + "2026-07-11;SC;LAGES;Hidro;PP;BAIXA PRECISÃO;-27.8;-50.3;;;1\n"
                 + "2026-07-11;SC;LAGES;Hidro;PP;BAIXA PRECISÃO;-27.8;-50.3;;;1\n"
                 + "2026-07-11;SC;LAGES;Hidro;PP;;0.0;0.0;;;2\n", encoding="utf-8")
    validos, descartes = ler_csv(f)
    assert len(validos) == 1 and descartes == {"id_duplicado": 1, "coordenada_nula": 1}


def test_arquivo_real_do_cemaden_carrega_na_api(app):
    validos, descartes = ler_csv(CSV_REAL)
    assert len(validos) + sum(descartes.values()) == 1992 + 1  # 1.993 linhas de dados
    assert descartes.keys() <= {"coordenada_nula"}
    with TestClient(app) as c:
        tk = token(c, "svc@x.com", "senha-svc-123")
        r = carregar(c, tk, validos)
        assert r["inseridos"] == len(validos) and r["atualizados"] == 0
        assert c.get("/cemaden/resumo").json()["total"] == len(validos)
        r2 = carregar(c, tk, validos)                    # recarga idempotente
        assert r2["inseridos"] == 0 and r2["atualizados"] == len(validos)
        assert c.get("/cemaden/resumo").json()["total"] == len(validos)


def test_retry_em_5xx_e_falha_de_rede_depois_sucesso():
    chamadas = []

    def handler(req):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise httpx.ConnectTimeout("fora")
        if len(chamadas) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"recebidos": 1, "inseridos": 1, "atualizados": 0, "alertas_gerados": 0})

    http = httpx.Client(base_url="http://x", transport=httpx.MockTransport(handler))
    esperas = []
    out = enviar_lote(http, [{"id_externo": 1}], "t", dormir=esperas.append)
    assert out["inseridos"] == 1 and len(chamadas) == 3 and len(esperas) == 2


def test_4xx_nao_e_repetido_e_esgotar_tentativas_levanta():
    http4 = httpx.Client(base_url="http://x", transport=httpx.MockTransport(lambda r: httpx.Response(422)))
    with pytest.raises(RuntimeError):
        enviar_lote(http4, [{}], "t", dormir=lambda s: None)
    http5 = httpx.Client(base_url="http://x", transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(ConnectionError):
        enviar_lote(http5, [{}], "t", tentativas=3, dormir=lambda s: None)
