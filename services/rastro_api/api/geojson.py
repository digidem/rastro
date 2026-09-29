"""Construtores GeoJSON puros (sem I/O) — datas em ISO 8601 UTC.

Nenhum builder toca banco nem rede; recebem rows (dicts) do psycopg e
devolvem dicts prontos para o FastAPI serializar (Content-Type application/json).
"""
from __future__ import annotations

import datetime as dt
from typing import Any


def iso_utc(value: Any) -> str:
    """Datetime → ISO 8601 UTC com sufixo 'Z'; outros valores viram str."""
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def empty() -> dict:
    """FeatureCollection vazia — resposta para nó desconhecido, nunca 404."""
    return collection([])


def point_feature(lon: Any, lat: Any, props: dict) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
        "properties": props,
    }


def telemetry_feature(props: dict) -> dict:
    """Feature sem geometria (telemetria não tem posição)."""
    return {"type": "Feature", "geometry": None, "properties": props}


def track_features(rows: list[dict], node_meta: dict) -> list[dict]:
    """UMA LineString (ordem cronológica, [lon, lat]) + um Point por fix."""
    coordinates = [[float(r["lon"]), float(r["lat"])] for r in rows]
    line = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "properties": {
            "node": node_meta["node"],
            "nome": node_meta.get("nome"),
            "from": iso_utc(node_meta.get("from", node_meta.get("de"))),
            "de": iso_utc(node_meta.get("from", node_meta.get("de"))),
            "to": iso_utc(node_meta["to"]),
        },
    }
    points = [
        point_feature(r["lon"], r["lat"], {
            "pos_time": iso_utc(r["pos_time"]),
            "time_source": r["time_source"],
            "sats": r["sats_in_view"],
        })
        for r in rows
    ]
    return [line, *points]
