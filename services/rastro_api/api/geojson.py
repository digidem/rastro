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


def _pos_timestamp(val: Any) -> float:
    """Extrai timestamp float de datetime ou ISO string."""
    if isinstance(val, dt.datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=dt.timezone.utc)
        return val.timestamp()
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            parsed = dt.datetime.fromisoformat(val.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.timestamp()
        except Exception:
            return 0.0
    return 0.0


# Qualidade do fix (migração 03): só entra nas propriedades quando não é nula.
_QUALIDADE_PONTO = (
    ("pdop", "pdop"),
    ("hdop", "hdop"),
    ("ground_speed_ms", "speed_ms"),
    ("ground_track_deg", "track_deg"),
)


def _propriedades_ponto(r: dict) -> dict:
    props = {
        "pos_time": iso_utc(r["pos_time"]),
        "time_source": r["time_source"],
        "sats": r["sats_in_view"],
    }
    for coluna, chave in _QUALIDADE_PONTO:
        valor = r.get(coluna)
        if valor is not None:
            props[chave] = float(valor)
    return props


def track_features(
    rows: list[dict],
    node_meta: dict,
    gap_secs: int | float | None = 1800,
    use_multilinestring: bool = False,
) -> list[dict]:
    """Linhas (LineString ou MultiLineString dividida por lacunas) + um Point por fix."""
    if not rows:
        return []

    # Agrupa fixes consecutivos em segmentos quando o intervalo excede gap_secs
    segments: list[list[dict]] = []
    current_segment: list[dict] = []
    last_ts: float | None = None

    for r in rows:
        ts = _pos_timestamp(r.get("pos_time"))
        if last_ts is not None and gap_secs is not None and gap_secs > 0:
            if (ts - last_ts) > gap_secs:
                if current_segment:
                    segments.append(current_segment)
                    current_segment = []
        current_segment.append(r)
        last_ts = ts

    if current_segment:
        segments.append(current_segment)

    common_props = {
        "node": node_meta["node"],
        "nome": node_meta.get("nome"),
        "from": iso_utc(node_meta.get("from", node_meta.get("de"))),
        "de": iso_utc(node_meta.get("from", node_meta.get("de"))),
        "to": iso_utc(node_meta["to"]),
    }

    if use_multilinestring:
        line_features = [
            {
                "type": "Feature",
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [
                        [[float(r["lon"]), float(r["lat"])] for r in seg]
                        for seg in segments
                    ],
                },
                "properties": dict(common_props),
            }
        ]
    else:
        line_features = []
        for idx, seg in enumerate(segments):
            props = dict(common_props)
            if len(segments) > 1:
                props["segment"] = idx
            line_features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[float(r["lon"]), float(r["lat"])] for r in seg],
                },
                "properties": props,
            })

    points = [
        point_feature(r["lon"], r["lat"], _propriedades_ponto(r))
        for r in rows
    ]
    return [*line_features, *points]

