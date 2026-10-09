"""Qualidade do fix do firmware (PDOP/HDOP/velocidade/rumo/precisão), migração 03.

Cobre: conversão de unidades com 0 = ausente, ida e volta pelo MQTT e pelo GeoJSON,
ponte JSON (packet_filter) e ingest sem as colunas novas (detecção por conexão).
Dados 100% sintéticos.
"""
from __future__ import annotations

import json

import pytest

from rastro_gateway.bridge.geojson_in import from_geojson_feature
from rastro_gateway.bridge.packet_filter import filter_packet
from rastro_gateway.common.records import (
    PositionRecord,
    parse_mqtt_payload,
    qualidade_do_firmware,
)
from rastro_gateway.ingest import db as ingest_db


# ------------------------------------------------------------ conversão de unidades


def test_qualidade_converte_unidades_do_protobuf() -> None:
    q = qualidade_do_firmware(
        pdop=150, hdop=90, ground_speed=3, ground_track=12_345_000, precision_bits=16
    )
    assert q["pdop"] == pytest.approx(1.5)
    assert q["hdop"] == pytest.approx(0.9)
    assert q["ground_speed_ms"] == pytest.approx(3.0)
    assert q["ground_track_deg"] == pytest.approx(123.45)
    assert q["precision_bits"] == 16


def test_qualidade_zero_e_ausente_vira_none() -> None:
    q = qualidade_do_firmware(pdop=0, hdop=None, ground_speed=0, ground_track=0, precision_bits=0)
    assert q == {
        "pdop": None,
        "hdop": None,
        "ground_speed_ms": None,
        "ground_track_deg": None,
        "precision_bits": None,
    }


# ------------------------------------------------------------ ida e volta


def _registro_com_qualidade() -> PositionRecord:
    return PositionRecord(
        node_num=0x0C0FFEE0,
        node_id="!0c0ffee0",
        time=1_760_000_000,
        time_source="device",
        lat_i=-345678901,
        lon_i=-625725440,
        sats=None,
        **qualidade_do_firmware(150, 90, 3, 12_345_000, 16),
    )


def test_mqtt_round_trip_preserva_qualidade() -> None:
    original = _registro_com_qualidade()
    lido = parse_mqtt_payload(original.to_mqtt_payload())
    assert isinstance(lido, PositionRecord)
    assert lido.pdop == pytest.approx(1.5)
    assert lido.hdop == pytest.approx(0.9)
    assert lido.ground_speed_ms == pytest.approx(3.0)
    assert lido.ground_track_deg == pytest.approx(123.45)
    assert lido.precision_bits == 16


def test_mqtt_payload_antigo_sem_campos_continua_valido() -> None:
    obj = json.loads(_registro_com_qualidade().to_mqtt_payload())
    for chave in ("pdop", "hdop", "ground_speed_ms", "ground_track_deg", "precision_bits"):
        del obj[chave]
    lido = parse_mqtt_payload(json.dumps(obj))
    assert isinstance(lido, PositionRecord)
    assert lido.pdop is None and lido.precision_bits is None


def test_geojson_round_trip_preserva_qualidade() -> None:
    feature = _registro_com_qualidade().to_geojson_feature()
    lido = from_geojson_feature(feature)
    assert isinstance(lido, PositionRecord)
    assert lido.hdop == pytest.approx(0.9)
    assert lido.ground_track_deg == pytest.approx(123.45)


# ------------------------------------------------------------ ponte JSON


def test_packet_filter_converte_campos_json_do_firmware() -> None:
    packet = {
        "from": 0x0C0FFEE0,
        "fromId": "!0c0ffee0",
        "decoded": {
            "portnum": "POSITION_APP",
            "position": {
                "latitudeI": -345678901,
                "longitudeI": -625725440,
                "time": 1_760_000_000,
                "PDOP": 150,
                "HDOP": 90,
                "groundSpeed": 3,
                "groundTrack": 12_345_000,
                "precisionBits": 16,
            }
        },
    }
    rec = filter_packet(packet)
    assert isinstance(rec, PositionRecord)
    assert rec.pdop == pytest.approx(1.5)
    assert rec.hdop == pytest.approx(0.9)
    assert rec.ground_speed_ms == pytest.approx(3.0)
    assert rec.ground_track_deg == pytest.approx(123.45)
    assert rec.precision_bits == 16


def test_packet_filter_sem_campos_de_qualidade() -> None:
    packet = {
        "from": 0x0C0FFEE0,
        "decoded": {
            "portnum": "POSITION_APP",
            "position": {"latitudeI": -345678901, "longitudeI": -625725440},
        },
    }
    rec = filter_packet(packet)
    assert isinstance(rec, PositionRecord)
    assert rec.pdop is None and rec.hdop is None and rec.precision_bits is None


# ------------------------------------------------------------ ingest sem as colunas


class _CursorFalso:
    """Cursor mínimo: registra SQL e devolve as colunas 'presentes' na detecção."""

    def __init__(self, presentes: list[str], log: list) -> None:
        self._presentes = presentes
        self._log = log
        self._ultimo: list = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def execute(self, sql, params=None) -> None:
        self._log.append((sql, params))
        if "information_schema.columns" in sql:
            self._ultimo = [(c,) for c in self._presentes]
        else:
            self._ultimo = []

    rowcount = 0

    def executemany(self, sql, rows) -> None:
        self._log.append((sql, rows))

    def fetchall(self) -> list:
        return self._ultimo


class _ConexaoFalsa:
    def __init__(self, presentes: list[str]) -> None:
        self.log: list = []
        self._presentes = presentes

    def cursor(self) -> _CursorFalso:
        return _CursorFalso(self._presentes, self.log)


def test_colunas_detectadas_uma_vez_por_conexao() -> None:
    conn = _ConexaoFalsa(["pdop", "hdop"])
    assert ingest_db._colunas_qualidade(conn) == ("pdop", "hdop")
    assert ingest_db._colunas_qualidade(conn) == ("pdop", "hdop")
    consultas = [s for s, _ in conn.log if "information_schema.columns" in s]
    assert len(consultas) == 1


def test_insert_sem_colunas_novas_nao_menciona_qualidade() -> None:
    conn = _ConexaoFalsa([])  # banco ainda SEM a migração 03
    ingest_db.Db._insert_positions(conn, [_registro_com_qualidade()])
    sql_insert, linhas = [(s, p) for s, p in conn.log if s.lstrip().startswith("INSERT")][0]
    assert "pdop" not in sql_insert and "precision_bits" not in sql_insert
    assert sql_insert.count("%s") == 13
    assert len(linhas[0]) == 13


def test_insert_com_colunas_novas_grava_qualidade_no_fim() -> None:
    conn = _ConexaoFalsa(list(ingest_db.QUALIDADE_COLUNAS))
    ingest_db.Db._insert_positions(conn, [_registro_com_qualidade()])
    sql_insert, linhas = [(s, p) for s, p in conn.log if s.lstrip().startswith("INSERT")][0]
    assert "pdop, hdop, ground_speed_ms, ground_track_deg, precision_bits" in sql_insert
    assert sql_insert.count("%s") == 13 + 5
    assert linhas[0][13:] == (1.5, 0.9, 3.0, pytest.approx(123.45), 16)
