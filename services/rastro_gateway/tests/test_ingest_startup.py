"""F3b (ingest): CA vindo de env em base64 + checagem de prontidão do banco.

Unitários puros: sem Postgres, sem broker e sem sono real. Fakes leves no
mesmo estilo de tests/test_ingest.py (e ``__new__`` sem __init__ como em
tests/test_gateway.py quando o construtor abriria recursos reais).
"""
import base64
import contextlib
import logging
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/

import psycopg
import pytest

from rastro_gateway.ingest import __main__ as main_mod
from rastro_gateway.ingest import db as db_mod
from rastro_gateway.ingest import mqtt_in

PEM_FALSO = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"


def _b64(texto: str = PEM_FALSO) -> str:
    return base64.b64encode(texto.encode()).decode()


def _modo(caminho: Path) -> int:
    return stat.S_IMODE(caminho.stat().st_mode)


# --- CA via RASTRO_MQTT_CA_B64 ------------------------------------------------


def test_ca_b64_vira_arquivo_privado_apontado_por_ca_cert(caplog):
    valor = _b64()
    with caplog.at_level(logging.DEBUG):
        cfg = mqtt_in.MqttConfig.from_env({"RASTRO_MQTT_CA_B64": valor})
    arquivo = Path(cfg.ca_cert)
    assert arquivo.is_file()
    assert arquivo.parent.name.startswith("rastro-ca-")
    assert arquivo.parent.parent == Path(tempfile.gettempdir())
    assert _modo(arquivo) == 0o600
    assert _modo(arquivo.parent) == 0o700
    assert arquivo.read_bytes() == PEM_FALSO.encode()
    # NUNCA logar o material: nem o base64, nem o PEM, nem o conteúdo
    assert valor not in caplog.text
    assert "BEGIN CERTIFICATE" not in caplog.text
    mqtt_in._remover_ca_temporaria(str(arquivo.parent), str(arquivo))


def test_ca_b64_segue_o_um_arquivo_por_chamada():
    """Dois from_env = dois diretórios privados (nada de escrever por cima)."""
    a = Path(mqtt_in.MqttConfig.from_env({"RASTRO_MQTT_CA_B64": _b64()}).ca_cert)
    b = Path(mqtt_in.MqttConfig.from_env({"RASTRO_MQTT_CA_B64": _b64()}).ca_cert)
    assert a != b and a.parent != b.parent
    for caminho in (a, b):
        mqtt_in._remover_ca_temporaria(str(caminho.parent), str(caminho))


def test_atexit_apaga_arquivo_e_diretorio():
    arquivo = Path(mqtt_in._ca_de_b64(_b64()))
    direto = arquivo.parent
    mqtt_in._remover_ca_temporaria(str(direto), str(arquivo))
    assert not arquivo.exists()
    assert not direto.exists()
    mqtt_in._remover_ca_temporaria(str(direto), str(arquivo))  # idempotente


def test_atexit_do_processo_limpa_o_diretorio():
    """O registro no atexit é o que impede CA órfã no /tmp do contêiner — o
    caminho só é verificável num processo de verdade, então é assim que se testa."""
    codigo = (
        "import sys; "
        "from rastro_gateway.ingest import mqtt_in; "
        "sys.stdout.write(mqtt_in.MqttConfig.from_env("
        "{'RASTRO_MQTT_CA_B64': sys.argv[1]}).ca_cert)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", codigo, _b64()],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    arquivo = Path(proc.stdout.strip())
    assert arquivo.parent.name.startswith("rastro-ca-")
    assert not arquivo.parent.exists()


def test_ca_cert_e_ca_b64_juntos_e_configuracao_invalida():
    with pytest.raises(ValueError) as exc:
        mqtt_in.MqttConfig.from_env(
            {"RASTRO_MQTT_CA_CERT": "/certs/ca.crt", "RASTRO_MQTT_CA_B64": _b64()}
        )
    assert str(exc.value) == (
        "defina só um: RASTRO_MQTT_CA_CERT ou RASTRO_MQTT_CA_B64"
    )


def test_ca_b64_que_nao_e_pem_de_certificado_da_erro():
    outro = "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"
    with pytest.raises(ValueError) as exc:
        mqtt_in.MqttConfig.from_env({"RASTRO_MQTT_CA_B64": _b64(outro)})
    assert str(exc.value) == "RASTRO_MQTT_CA_B64 não é um PEM de certificado"


def test_ca_b64_invalido_da_erro():
    with pytest.raises(ValueError) as exc:
        mqtt_in.MqttConfig.from_env({"RASTRO_MQTT_CA_B64": "isso não é base64!!"})
    assert "RASTRO_MQTT_CA_B64" in str(exc.value)
    # nada do valor pode aparecer na mensagem (é material de TLS)
    assert "não é base64!!" not in str(exc.value)


def test_ca_vazia_ou_ausente_nao_gera_arquivo():
    assert mqtt_in.MqttConfig.from_env({}).ca_cert is None
    assert (
        mqtt_in.MqttConfig.from_env(
            {"RASTRO_MQTT_CA_CERT": "", "RASTRO_MQTT_CA_B64": ""}
        ).ca_cert
        is None
    )
    cfg = mqtt_in.MqttConfig.from_env({"RASTRO_MQTT_CA_CERT": "/certs/ca.crt"})
    assert cfg.ca_cert == "/certs/ca.crt"


def test_build_client_sempre_tls_mesmo_sem_ca():
    cfg = mqtt_in.MqttConfig.from_env({})
    client = mqtt_in.build_client(cfg)
    assert client._ssl_context is not None  # TLS nunca é opcional
    assert client._ssl_context.check_hostname is True


# --- Db.check_ready ------------------------------------------------------------


class _CursorFalso:
    def __init__(self, row):
        self._row = row
        self.sql = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.sql = sql

    def fetchone(self):
        return self._row


class _PoolFalso:
    def __init__(self, row):
        self.cursor_obj = _CursorFalso(row)

    @contextlib.contextmanager
    def connection(self):
        class _Conn:
            def __init__(inner):
                inner.cursor = lambda: self.cursor_obj
        yield _Conn()


def _db_com_linha(row):
    database = db_mod.Db.__new__(db_mod.Db)  # sem __init__: nada de pool real
    database._pool = _PoolFalso(row)
    return database


def test_check_ready_tudo_ok_nao_levanta():
    row = (True,) * len(db_mod._CHECK_READY_ITENS) + ("{rastro}",)
    database = _db_com_linha(row)
    database.check_ready()
    sql = database._pool.cursor_obj.sql
    # só metadados: nenhum SELECT de linhas das tabelas de histórico
    assert "FROM positions" not in sql and "FROM device_telemetry" not in sql


def test_check_ready_lista_o_que_falta_e_os_schemas():
    itens = db_mod._CHECK_READY_ITENS
    row = [True] * len(itens)
    row[itens.index("falta INSERT em positions")] = False
    database = _db_com_linha(tuple(row) + ("{public}",))
    with pytest.raises(RuntimeError) as exc:
        database.check_ready()
    assert "falta INSERT em positions" in str(exc.value)
    assert "{public}" in str(exc.value)


# --- aguardar_banco (retry no boot) --------------------------------------------


class _DbRoteiro:
    def __init__(self, efeitos):
        self.efeitos = list(efeitos)
        self.chamadas = 0

    def check_ready(self):
        self.chamadas += 1
        efeito = self.efeitos.pop(0) if self.efeitos else None
        if efeito is not None:
            raise efeito


def test_aguardar_banco_tenta_de_novo_com_backoff(monkeypatch):
    sonos = []
    monkeypatch.setattr(main_mod.time, "sleep", sonos.append)
    database = _DbRoteiro([psycopg.OperationalError("x"), psycopg.OperationalError("y"), None])
    assert main_mod.aguardar_banco(database, 120) == main_mod.EXIT_OK
    assert database.chamadas == 3
    assert sonos == [1.0, 2.0]


def test_aguardar_banco_erro_de_schema_sai_2_sem_retry(monkeypatch):
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: pytest.fail("não deveria dormir"))
    database = _DbRoteiro([RuntimeError("banco não está pronto: falta INSERT em positions")])
    assert main_mod.aguardar_banco(database, 120) == main_mod.EXIT_CONFIG
    assert database.chamadas == 1


def test_aguardar_banco_prazo_zero_sai_3(monkeypatch):
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)
    database = _DbRoteiro([psycopg.OperationalError("fora")] * 5)
    assert main_mod.aguardar_banco(database, 0) == main_mod.EXIT_DEPENDENCIA


def test_main_propaga_codigo_do_banco_e_fecha(monkeypatch):
    monkeypatch.setenv("RASTRO_PG_PASSWORD", "x")
    monkeypatch.delenv("RASTRO_MQTT_CA_B64", raising=False)
    fechado = []

    class _DbFalso:
        def __init__(self, cfg):
            pass

        def close(self):
            fechado.append(True)

    monkeypatch.setattr(main_mod.db, "Db", _DbFalso)
    monkeypatch.setattr(main_mod, "aguardar_banco", lambda database, t: main_mod.EXIT_CONFIG)
    assert main_mod.main() == main_mod.EXIT_CONFIG
    assert fechado == [True]


# --- espera pela CA publicada pelo broker (modo TLS automático) ---------------


def test_aguardar_ca_sem_caminho_ou_ja_presente_nao_espera(tmp_path):
    def sleep_proibido(_):
        raise AssertionError("não deveria dormir")

    assert mqtt_in.aguardar_ca(None, sleep=sleep_proibido) is True
    ca = tmp_path / "ca.crt"
    ca.write_text(PEM_FALSO)
    assert mqtt_in.aguardar_ca(str(ca), sleep=sleep_proibido) is True


def test_aguardar_ca_ate_aparecer_loga_uma_vez(tmp_path, caplog):
    ca = tmp_path / "ca.crt"
    relogio = {"t": 0.0}
    dormidas = []

    def sleep(s):
        dormidas.append(s)
        relogio["t"] += s
        if len(dormidas) == 3:
            ca.write_text(PEM_FALSO)

    with caplog.at_level(logging.INFO):
        ok = mqtt_in.aguardar_ca(
            str(ca), {}, sleep=sleep, monotonic=lambda: relogio["t"]
        )
    assert ok is True
    assert dormidas == [5.0, 5.0, 5.0]
    avisos = [r for r in caplog.records if "aguardando o broker publicar a CA" in r.getMessage()]
    assert len(avisos) == 1
    assert str(ca) in avisos[0].getMessage()


def test_aguardar_ca_esgota_o_prazo(tmp_path):
    relogio = {"t": 0.0}

    def sleep(s):
        relogio["t"] += s

    ok = mqtt_in.aguardar_ca(
        str(tmp_path / "nao-existe.crt"),
        {"RASTRO_MQTT_CA_WAIT_SECS": "12"},
        sleep=sleep,
        monotonic=lambda: relogio["t"],
    )
    assert ok is False
    assert relogio["t"] == 15.0  # 3 consultas de 5 s cobrem os 12 s


def test_main_sai_com_config_quando_ca_nunca_aparece(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("RASTRO_MQTT_CA_CERT", str(tmp_path / "ca.crt"))
    monkeypatch.setenv("RASTRO_MQTT_CA_WAIT_SECS", "0")
    monkeypatch.delenv("RASTRO_MQTT_CA_B64", raising=False)
    monkeypatch.setenv("RASTRO_PG_PASSWORD", "x")
    with caplog.at_level(logging.ERROR):
        assert main_mod.main() == main_mod.EXIT_CONFIG
    assert "CA do broker não apareceu" in caplog.text


# --- usuário do Postgres derivado do nome do banco ----------------------------


def test_pg_user_padrao_deriva_do_banco():
    cfg = db_mod.PgConfig.from_env({"RASTRO_PG_PASSWORD": "x", "RASTRO_PG_DB": "mapa"})
    assert cfg.user == "mapa_ingest"
    cfg = db_mod.PgConfig.from_env({"RASTRO_PG_PASSWORD": "x", "RASTRO_PG_USER": ""})
    assert cfg.user == "rastro_ingest"


def test_pg_user_explicito_vence():
    cfg = db_mod.PgConfig.from_env(
        {"RASTRO_PG_PASSWORD": "x", "RASTRO_PG_DB": "mapa", "RASTRO_PG_USER": "outro"}
    )
    assert cfg.user == "outro"
