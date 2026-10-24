"""Leituras ambientais (BME280): /nodes/{node}/environment e /environment/latest."""
from __future__ import annotations

AUTH = {"Authorization": "Bearer teste-token-123"}


def test_environment_do_no_em_ordem_cronologica(client, dados):
    resposta = client.get("/api/nodes/!5e550001/environment", headers=AUTH)
    assert resposta.status_code == 200
    feats = resposta.json()["features"]
    assert [f["properties"]["temperature_c"] for f in feats] == [26.0, 27.5]
    assert all(f["geometry"] is None for f in feats)
    assert feats[0]["properties"]["telem_time"].endswith("Z")
    # medida ausente (pressão NULL) fica fora das propriedades
    assert "pressure_hpa" not in feats[1]["properties"]
    assert feats[0]["properties"]["pressure_hpa"] == 1008.0


def test_environment_janela_from_to(client, dados):
    # padrão = últimas 24 h: ambas as leituras de S1 estão dentro; janela antiga não tem nenhuma
    todas = client.get("/api/nodes/!5e550001/environment", headers=AUTH).json()["features"]
    assert len(todas) == 2
    antiga = client.get(
        "/api/nodes/!5e550001/environment",
        headers=AUTH,
        params={"from": "2020-01-01T00:00:00Z", "to": "2020-01-02T00:00:00Z"},
    ).json()["features"]
    assert antiga == []
    # limites inclusivos: janela exata da leitura mais nova devolve só ela
    t3 = dados["t3"].isoformat()
    exata = client.get(
        "/api/nodes/!5e550001/environment", headers=AUTH, params={"from": t3, "to": t3}
    ).json()["features"]
    assert len(exata) == 1
    assert exata[0]["properties"]["temperature_c"] == 27.5


def test_environment_limit_devolve_os_mais_novos(client, dados):
    resposta = client.get("/api/nodes/!5e550001/environment", headers=AUTH, params={"limit": 1})
    feats = resposta.json()["features"]
    assert len(feats) == 1
    assert feats[0]["properties"]["temperature_c"] == 27.5


def test_environment_no_desconhecido_colecao_vazia(client, dados):
    resposta = client.get("/api/nodes/!deadbeef/environment", headers=AUTH)
    assert resposta.status_code == 200
    assert resposta.json() == {"type": "FeatureCollection", "features": []}


def test_environment_no_malformado_igual_a_telemetry(client, dados):
    a = client.get("/api/nodes/xyz!/telemetry", headers=AUTH)
    b = client.get("/api/nodes/xyz!/environment", headers=AUTH)
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()


def test_environment_requer_token(client, dados):
    assert client.get("/api/nodes/!5e550001/environment").status_code == 401
    assert client.get("/api/environment/latest").status_code == 401


def test_latest_uma_linha_por_no_sem_posicao(client, dados):
    resposta = client.get("/api/environment/latest", headers=AUTH)
    assert resposta.status_code == 200
    feats = resposta.json()["features"]
    por_no = {f["properties"]["node_num"]: f["properties"] for f in feats}
    # S1: leitura mais recente; S2 (20 dias) fora; S3 presente
    assert set(por_no) == {dados["num_s1"], dados["num_s3"]}
    s1 = por_no[dados["num_s1"]]
    assert s1["temperature_c"] == 27.5
    assert s1["node"] == "!5e550001"
    assert s1["nome"] == "Sensor 1"
    assert s1["age_s"] >= 0
    # o sensor não aparece em /nodes/latest (só lista nós com posição)
    nos = client.get("/api/nodes/latest", headers=AUTH).json()["features"]
    assert "!5e550001" not in {f["properties"].get("node") for f in nos}


def test_latest_relogio_adiantado_age_zero(client, dados):
    feats = client.get("/api/environment/latest", headers=AUTH).json()["features"]
    s3 = next(f["properties"] for f in feats if f["properties"]["node_num"] == dados["num_s3"])
    assert s3["age_s"] == 0
    assert s3["nome"] == "!5e550003"  # sem friendly_name: cai no node_id
