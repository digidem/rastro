"""Conexão do Postgres publicada pelo app -setup (conn.env): sem banco, testes puros."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/

from rastro_api.api import main as api  # noqa: E402


@pytest.fixture
def limpo(monkeypatch):
    for nome in ("RASTRO_PG_HOST", "RASTRO_PG_PORT", "RASTRO_PG_SSLMODE",
                 "RASTRO_PG_CONN_FILE", "RASTRO_PG_CONN_WAIT_SECS"):
        monkeypatch.delenv(nome, raising=False)
    return monkeypatch


def _arquivo(tmp_path, texto):
    arq = tmp_path / "conn.env"
    arq.write_text(texto)
    return str(arq)


def test_le_host_porta_sslmode_e_ignora_o_resto(limpo, tmp_path):
    arq = _arquivo(tmp_path, "# x\nRASTRO_PG_HOST=pg.exemplo.com\nRASTRO_PG_PORT=6543\n"
                             "RASTRO_PG_SSLMODE=require\nRASTRO_PG_PASSWORD=segredo\nfoo\n")
    assert api._ler_conn_file(arq) == {
        "RASTRO_PG_HOST": "pg.exemplo.com", "RASTRO_PG_PORT": "6543", "RASTRO_PG_SSLMODE": "require",
    }
    limpo.setenv("RASTRO_PG_CONN_FILE", arq)
    info = api._conninfo()
    assert "host=pg.exemplo.com" in info and "port=6543" in info and "sslmode=require" in info
    assert "segredo" not in info


def test_ambiente_vence_o_arquivo(limpo, tmp_path):
    limpo.setenv("RASTRO_PG_CONN_FILE", _arquivo(tmp_path, "RASTRO_PG_HOST=arq\nRASTRO_PG_SSLMODE=require\n"))
    limpo.setenv("RASTRO_PG_HOST", "amb")
    limpo.setenv("RASTRO_PG_SSLMODE", "disable")
    assert api._pg_conn() == ("amb", "5432", "disable")
    limpo.delenv("RASTRO_PG_SSLMODE")
    assert api._pg_conn() == ("amb", "5432", "")  # arquivo nem é consultado com HOST no ambiente
    assert "sslmode" not in api._conninfo()


def test_espera_pelo_arquivo_expira(limpo, tmp_path):
    limpo.setenv("RASTRO_PG_CONN_FILE", str(tmp_path / "nao.env"))
    limpo.setenv("RASTRO_PG_CONN_WAIT_SECS", "12")
    relogio = {"t": 0.0}

    def sleep(s):
        relogio["t"] += s

    assert api._aguardar_conn_file(sleep=sleep, monotonic=lambda: relogio["t"]) is False
    assert relogio["t"] == 15.0


def test_espera_imediata_com_host_ou_arquivo(limpo, tmp_path):
    def sleep(_):
        raise AssertionError("não deveria esperar")

    limpo.setenv("RASTRO_PG_HOST", "h")
    assert api._aguardar_conn_file(sleep=sleep) is True
    limpo.delenv("RASTRO_PG_HOST")
    limpo.setenv("RASTRO_PG_CONN_FILE", _arquivo(tmp_path, "RASTRO_PG_HOST=h\n"))
    assert api._aguardar_conn_file(sleep=sleep) is True
