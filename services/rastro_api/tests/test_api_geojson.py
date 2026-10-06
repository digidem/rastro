"""GeoJSON: última posição, trilha (hex/num/janela), telemetria e casos de borda."""
from datetime import timedelta

AUTH = {"Authorization": "Bearer teste-token-123"}


def _degrees(i: int) -> float:
    """Inteiro ×1e7 → graus (mesma conta das colunas geradas lat/lon)."""
    return i / 10_000_000


def test_latest_duas_features(client, dados):
    resposta = client.get("/api/nodes/latest", headers=AUTH)
    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("application/json")
    corpo = resposta.json()
    assert corpo["type"] == "FeatureCollection"
    feats = {f["properties"]["node_id"]: f for f in corpo["features"]}
    assert set(feats) == {"!aaaa0001", "!bbbb0002"}
    a = feats["!aaaa0001"]
    props = a["properties"]
    assert props["nome"] == "Monitor A"
    assert a["geometry"]["type"] == "Point"
    lon, lat = a["geometry"]["coordinates"]
    # latest = fix MAIS RECENTE do nó A (t3), não o primeiro
    assert abs(lon - _degrees(-700_200_000)) < 1e-7
    assert abs(lat - _degrees(-65_200_000)) < 1e-7
    assert props["sats"] == 6
    assert props["pos_time"].endswith("Z")
    assert props["time_source"] == "device"
    assert props["battery"] == 87.5
    for chave in ("node_num", "altitude_m", "sats", "received_at"):
        assert chave in props


def test_track_hex(client, dados, monkeypatch):
    monkeypatch.setenv("RASTRO_TRACK_GAP_SECS", "0")  # sem segmentar por lacuna (fixture tem 1h entre fixes)
    resposta = client.get("/api/nodes/!aaaa0001/track", headers=AUTH)
    assert resposta.status_code == 200
    feats = resposta.json()["features"]
    lines = [f for f in feats if f["geometry"]["type"] == "LineString"]
    points = [f for f in feats if f["geometry"]["type"] == "Point"]
    assert len(lines) == 1
    assert len(points) == 3
    coords = lines[0]["geometry"]["coordinates"]
    expected = [
        [_degrees(-700_000_000), _degrees(-65_000_000)],
        [_degrees(-700_100_000), _degrees(-65_100_000)],
        [_degrees(-700_200_000), _degrees(-65_200_000)],
    ]
    assert len(coords) == 3
    for (lon, lat), (elon, elat) in zip(coords, expected):
        assert abs(lon - elon) < 1e-7
        assert abs(lat - elat) < 1e-7
    props = lines[0]["properties"]
    assert props["node"] == "!aaaa0001"
    assert props["nome"] == "Monitor A"
    assert {"de", "to"} <= set(props)
    times = [p["properties"]["pos_time"] for p in points]
    assert times == sorted(times)  # ordem cronológica
    assert [p["properties"]["sats"] for p in points] == [7, 8, 6]


def test_track_node_num_mesmo_resultado(client, dados, monkeypatch):
    monkeypatch.setenv("RASTRO_TRACK_GAP_SECS", "0")
    hex_resp = client.get("/api/nodes/!aaaa0001/track", headers=AUTH)
    num_resp = client.get(f"/api/nodes/{int('aaaa0001', 16)}/track", headers=AUTH)
    assert num_resp.status_code == 200
    feats_num = num_resp.json()["features"]
    feats_hex = hex_resp.json()["features"]
    # mesma trilha — de/to vêm de now() e variam entre requests, então
    # comparam-se geometrias, fixes e identidade do nó
    assert [f["geometry"] for f in feats_num] == [f["geometry"] for f in feats_hex]
    assert [f["properties"]["pos_time"] for f in feats_num[1:]] == [
        f["properties"]["pos_time"] for f in feats_hex[1:]
    ]
    assert feats_num[0]["properties"]["node"] == "!aaaa0001"
    assert feats_hex[0]["properties"]["node"] == "!aaaa0001"


def test_track_janela_restritiva_um_fix(client, dados):
    t2 = dados["t2"]
    params = {
        "from": (t2 - timedelta(seconds=60)).isoformat(),
        "to": (t2 + timedelta(seconds=60)).isoformat(),
    }
    resposta = client.get("/api/nodes/!aaaa0001/track", headers=AUTH, params=params)
    assert resposta.status_code == 200
    feats = resposta.json()["features"]
    points = [f for f in feats if f["geometry"]["type"] == "Point"]
    assert len(points) == 1
    assert len(feats[0]["geometry"]["coordinates"]) == 1


def test_track_node_desconhecido_colecao_vazia(client, dados):
    resposta = client.get("/api/nodes/!deadbeef/track", headers=AUTH)
    assert resposta.status_code == 200
    assert resposta.json() == {"type": "FeatureCollection", "features": []}


def test_telemetry_um_feature_com_battery(client, dados):
    resposta = client.get("/api/nodes/!aaaa0001/telemetry", headers=AUTH)
    assert resposta.status_code == 200
    feats = resposta.json()["features"]
    assert len(feats) == 1
    assert feats[0]["geometry"] is None
    props = feats[0]["properties"]
    assert props["battery_level"] == 87.5
    assert props["voltage"] == 4.1 or abs(props["voltage"] - 4.1) < 1e-5  # REAL (float4)
    for chave in ("channel_util", "air_util_tx", "uptime_s", "telem_time"):
        assert chave in props
    assert props["telem_time"].endswith("Z")


def test_track_limit_cap_nao_explode(client, dados):
    resposta = client.get(
        "/api/nodes/!aaaa0001/track", headers=AUTH, params={"limit": 9999}
    )
    assert resposta.status_code == 200
    points = [f for f in resposta.json()["features"] if f["geometry"]["type"] == "Point"]
    assert len(points) == 3  # cap 2000 aplicado sem erro; só existem 3 fixes
