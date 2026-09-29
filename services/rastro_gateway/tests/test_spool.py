"""Spool durável: watermark contígua, rotação, poda e replay."""
import json

import pytest

from rastro_gateway.bridge.spool import Spool
from rastro_gateway.common.records import PositionRecord, TelemetryRecord

FIRST = "spool-000001.geojsonl"


def pos(node=1, **over):
    base = dict(node_num=node, node_id=f"!{node:08x}", time=None, time_source="gateway",
                lat_i=-345678901, lon_i=-62572544)
    base.update(over)
    return PositionRecord(**base)


def telem(node=2, **over):
    base = dict(node_num=node, node_id=f"!{node:08x}", time=1727184000,
                time_source="device", battery_level=87.0, voltage=4.15,
                channel_util=12.5, air_util_tx=0.23, uptime_s=3600)
    base.update(over)
    return TelemetryRecord(**base)


def file_names(tmp_path):
    return sorted(p.name for p in tmp_path.glob("spool-*.geojsonl"))


def file_size(tmp_path, name):
    return (tmp_path / name).stat().st_size


# --- watermark --------------------------------------------------------------

def test_append_e_watermark_avanca(tmp_path):
    spool = Spool(tmp_path, rotate_bytes=10**6, max_bytes=10**7)
    assert spool.position() == (FIRST, 0)  # começo: nada acked

    s1 = spool.append(pos())
    assert spool.position() == (FIRST, 0)  # append sozinho NÃO move a watermark

    spool.register(s1)
    assert spool.position() == (FIRST, 0)  # registrado, mas sem PUBACK ainda

    spool.advance(s1)
    assert spool.position() == (FIRST, s1[2])  # fim exato da linha ackada


def test_advance_fora_de_ordem_espera_contigua(tmp_path):
    spool = Spool(tmp_path, rotate_bytes=10**6, max_bytes=10**7)
    s1 = spool.append(pos())
    s2 = spool.append(pos(node=2))
    s3 = spool.append(pos(node=3))

    for span in (s1, s2, s3):
        spool.register(span)
    spool.advance(s3)
    spool.advance(s2)
    assert spool.position() == (FIRST, 0)  # buraco em s1: watermark parada

    spool.advance(s1)  # fecha o buraco → pula direto pro mínimo contíguo
    assert spool.position() == (FIRST, s3[2])


# --- rotação ----------------------------------------------------------------

def test_rotacao(tmp_path):
    spool = Spool(tmp_path, rotate_bytes=120, max_bytes=10**7)  # linha ≈ 300 B > 120
    spool.append(pos())
    assert file_names(tmp_path) == [FIRST]

    spool.append(pos(node=2))  # estoura rotate_bytes → abre o próximo
    assert file_names(tmp_path) == [FIRST, "spool-000002.geojsonl"]
    # um append por arquivo: cada um tem exatamente 1 linha
    for name in file_names(tmp_path):
        assert (tmp_path / name).read_bytes().count(b"\n") == 1


# --- poda -------------------------------------------------------------------

def _spool_de_4(tmp_path):
    """4 appends com rotate pequeno → 4 arquivos de 1 linha; devolve os spans."""
    spool = Spool(tmp_path, rotate_bytes=120, max_bytes=10**7)
    return spool, [spool.append(pos(node=n)) for n in range(1, 5)]


def test_prune_so_acked(tmp_path):
    spool, spans = _spool_de_4(tmp_path)
    for span in spans[:3]:  # watermark avança até o FIM do 3º arquivo
        spool.register(span)
        spool.advance(span)
    assert spool.position() == ("spool-000003.geojsonl", spans[2][2])

    total = sum(file_size(tmp_path, n) for n in file_names(tmp_path))
    primeiro = file_size(tmp_path, FIRST)
    # teto que só cabe sem o 1º arquivo (e ainda sobra folga p/ não tocar o 2º)
    spool = Spool(tmp_path, rotate_bytes=120, max_bytes=total - primeiro)
    spool.prune()

    assert file_names(tmp_path) == ["spool-000002.geojsonl", "spool-000003.geojsonl",
                                    "spool-000004.geojsonl"]
    assert not (tmp_path / FIRST).exists()
    assert spool.position() == ("spool-000003.geojsonl", spans[2][2])  # intocado


def test_prune_overflow_sem_ack(tmp_path):
    spool, _spans = _spool_de_4(tmp_path)
    assert spool.position() == (FIRST, 0)  # nada acked

    total = sum(file_size(tmp_path, n) for n in file_names(tmp_path))
    primeiro = file_size(tmp_path, FIRST)
    spool = Spool(tmp_path, rotate_bytes=120, max_bytes=total - primeiro)
    spool.prune()  # outage: descarta o mais antigo MESMO sem ack

    assert file_names(tmp_path) == ["spool-000002.geojsonl", "spool-000003.geojsonl",
                                    "spool-000004.geojsonl"]
    # watermark apontava pro arquivo podado → recomeça do mais antigo, offset 0
    assert spool.position() == ("spool-000002.geojsonl", 0)


# --- recuperação da watermark ------------------------------------------------

@pytest.mark.parametrize("corrompe", ["offset_além_do_eof", "json_quebrado", "arquivo_ausente"])
def test_watermark_corrompida_recomeca_atras(tmp_path, corrompe):
    spool = Spool(tmp_path, rotate_bytes=10**6, max_bytes=10**7)
    s1 = spool.append(pos())
    s2 = spool.append(pos(node=2))
    spool.register(s1)
    spool.register(s2)
    spool.advance(s1)  # watermark legítima no meio do 1º arquivo
    assert spool.position() == (FIRST, s1[2])

    wm = tmp_path / "watermark.json"
    if corrompe == "offset_além_do_eof":
        wm.write_text(json.dumps({"file": FIRST, "offset": 999999}))
    elif corrompe == "json_quebrado":
        wm.write_text("{não sou json")
    else:
        wm.unlink()

    novo = Spool(tmp_path, rotate_bytes=10**6, max_bytes=10**7)
    assert novo.position() == (FIRST, 0)  # direção segura: PARA TRÁS


# --- replay ------------------------------------------------------------------

def test_iter_from_linha_incompleta_para(tmp_path):
    spool = Spool(tmp_path, rotate_bytes=10**6, max_bytes=10**7)
    span = spool.append(pos())
    linha_completa = file_size(tmp_path, FIRST)

    # linha PARCIAL sem \n no fim (append ao vivo em curso / corrupção)
    with open(tmp_path / FIRST, "ab") as f:
        f.write(b'{"type":"Fea')

    itens = list(spool.iter_from(FIRST, 0))
    # só a linha completa sai; a parcial NÃO gera span
    assert [s for s, _ in itens] == [(FIRST, 0, linha_completa)]
    record = itens[0][1]
    assert record == pos()  # dataclass eq: roundtrip exato
    assert span[2] == linha_completa


# --- roundtrip GeoJSON -------------------------------------------------------

def test_roundtrip_geojson(tmp_path):
    p_orig = pos(node=1, time=1727184000, time_source="device", altitude_m=95,
                 sats=6, hop_limit=3, snr=-12.5, rssi=-81, friendly_name="Lancha Alfa")
    t_orig = telem(node=2, time=None, time_source="gateway", friendly_name="Base Rio")

    from rastro_gateway.bridge.geojson_in import from_geojson_feature

    for orig in (p_orig, t_orig):
        rec = from_geojson_feature(orig.to_geojson_feature())
        assert rec == orig
        assert rec.to_mqtt_payload() == orig.to_mqtt_payload()
