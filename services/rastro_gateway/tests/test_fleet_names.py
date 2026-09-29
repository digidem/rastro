"""Leitor do fleet.json (shape real: devices[] → identity{node_num, long/short_name})."""
import json

from rastro_gateway.common import fleet_names

FLEET = {
    "schema_version": 1,
    "devices": [
        {
            "id": "heltec-v4-e5d0",
            "identity": {
                "node_num": 202374880,
                "node_num_hex": "0x0c0ffee0",
                "user_id": "!0c0ffee0",
                "long_name": "Meshtastic e5d0",
                "short_name": "e5d0",
            },
        },
    ],
}


def _write_fleet(tmp_path, data):
    path = tmp_path / "fleet.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_load_known_node(tmp_path):
    path = _write_fleet(tmp_path, FLEET)
    names = fleet_names.load_fleet_names(path)
    assert names == {202374880: ("Meshtastic e5d0", "e5d0")}
    assert fleet_names.friendly(202374880, "!0c0ffee0", names) == "Meshtastic e5d0"


def test_load_fleet_ids(tmp_path):
    path = _write_fleet(tmp_path, FLEET)
    assert fleet_names.load_fleet_ids(path) == {202374880: "heltec-v4-e5d0"}


def test_friendly_unknown_uses_node_id_then_hex():
    names = {}  # fallback puro — o default leria o fleet.json REAL do repo
    assert fleet_names.friendly(1, "!0c0ffee0", names) == "!0c0ffee0"
    assert fleet_names.friendly(0xDEADBEEF, None, names) == "!deadbeef"


def test_friendly_without_long_name_falls_back(tmp_path):
    data = {"devices": [{"id": "x", "identity": {"node_num": 5, "long_name": "", "short_name": "x5"}}]}
    names = fleet_names.load_fleet_names(_write_fleet(tmp_path, data))
    assert fleet_names.friendly(5, "!00000005", names) == "!00000005"


def test_malformed_fleet_returns_empty(tmp_path):
    path = tmp_path / "fleet.json"
    path.write_text("{quebrado", encoding="utf-8")
    assert fleet_names.load_fleet_names(str(path)) == {}
    assert fleet_names.load_fleet_ids(str(path)) == {}


def test_missing_path_returns_empty(tmp_path):
    missing = str(tmp_path / "nenhum.json")
    assert fleet_names.load_fleet_names(missing) == {}
    assert fleet_names.load_fleet_ids(missing) == {}


def test_wrong_shape_returns_empty(tmp_path):
    path = _write_fleet(tmp_path, {"nodes": {}})  # shape antigo do brief, não o real
    assert fleet_names.load_fleet_names(path) == {}


def test_env_var_path(monkeypatch, tmp_path):
    path = _write_fleet(tmp_path, FLEET)
    monkeypatch.setenv("RASTRO_FLEET_JSON", path)
    assert fleet_names.load_fleet_names() == {202374880: ("Meshtastic e5d0", "e5d0")}


# --- fortalecimento R1b-9: ramos do _load_fleet --------------------------------


def test_load_fleet_ignora_bool_e_aceita_node_num_digitos(tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from rastro_gateway.common import fleet_names

    f = tmp_path / "fleet.json"
    num = 0x0badf00a
    f.write_text(
        '{"devices": ['
        '{"identity": {"node_num": true, "node_id": "!0badf00a"}, "name": "bool-ignorado"},'
        f'{{"identity": {{"node_num": "{num}", "node_id": "!0badf00a"}}, "name": "string-digitos"}}]}}',
        encoding="utf-8",
    )
    names = fleet_names._load_fleet(f)
    assert num in names          # string de dígitos virou int
    assert 1 not in names        # bool não entrou (bool é int — ramo explícito)
