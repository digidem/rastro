"""Qualidade do fix na trilha (track_features): campos só quando não nulos. Dados sintéticos."""
from datetime import datetime, timezone

from rastro_api.api import geojson

NO = {"node": "!aaaa0001", "nome": "Monitor A", "from": None, "to": datetime(2026, 1, 2, tzinfo=timezone.utc)}


def _linha(minuto: int, **extra) -> dict:
    base = {
        "pos_time": datetime(2026, 1, 1, 0, minuto, tzinfo=timezone.utc),
        "lat": -5.0,
        "lon": -70.0 + minuto / 1000,
        "time_source": "device",
        "sats_in_view": 7,
        "pdop": None,
        "hdop": None,
        "ground_speed_ms": None,
        "ground_track_deg": None,
    }
    base.update(extra)
    return base


def _pontos(features: list[dict]) -> list[dict]:
    return [f for f in features if f["geometry"]["type"] == "Point"]


def test_pontos_com_qualidade_expõem_campos() -> None:
    rows = [_linha(0, pdop=1.5, hdop=0.9, ground_speed_ms=3.0, ground_track_deg=123.45)]
    props = _pontos(geojson.track_features(rows, NO))[0]["properties"]
    assert props["pdop"] == 1.5
    assert props["hdop"] == 0.9
    assert props["speed_ms"] == 3.0
    assert props["track_deg"] == 123.45


def test_pontos_sem_qualidade_nao_trazem_chaves() -> None:
    rows = [_linha(0), _linha(1, hdop=2.0)]
    pts = _pontos(geojson.track_features(rows, NO))
    assert set(pts[0]["properties"]) == {"pos_time", "time_source", "sats"}
    assert pts[1]["properties"]["hdop"] == 2.0
    assert "pdop" not in pts[1]["properties"]


def test_linhas_nao_mudam_com_qualidade() -> None:
    rows = [_linha(0, hdop=0.9), _linha(1)]
    linhas = [f for f in geojson.track_features(rows, NO) if f["geometry"]["type"] == "LineString"]
    assert len(linhas[0]["geometry"]["coordinates"]) == 2
