"""Lifespan da API com a previsão do tempo (api/main.py): agenda ligada/desligada e ordem
do encerramento. Sem Postgres e sem rede: ``ConnectionPool`` e ``AgendaClima`` são falsos.
"""
from __future__ import annotations

import logging
import threading

import pytest
from fastapi.testclient import TestClient

from rastro_api.api import main

LOG = "rastro_api"


class _Evento(threading.Event):
    """Event que registra quando é acionado (ordem do encerramento)."""

    def __init__(self, ordem: list[str]):
        super().__init__()
        self._ordem = ordem

    def set(self) -> None:
        self._ordem.append("parar.set")
        super().set()


class _FakePool:
    check_connection = staticmethod(lambda conn: None)

    def __init__(self, *, ordem: list[str], **kwargs):
        self._ordem = ordem

    def open(self, wait: bool = False) -> None:
        pass

    def close(self) -> None:
        self._ordem.append("pool.close")


class _FakeThread:
    def __init__(self, ordem: list[str], vivo: bool = False):
        self._ordem = ordem
        self._vivo = vivo

    def join(self, timeout: float | None = None) -> None:
        self._ordem.append(f"thread.join({timeout})")

    def is_alive(self) -> bool:
        return self._vivo


@pytest.fixture()
def ambiente(monkeypatch):
    """Lifespan sem preparo do Postgres e sem variáveis de previsão herdadas."""
    monkeypatch.setenv("RASTRO_PG_HOST", "127.0.0.1")
    monkeypatch.delenv("RASTRO_CLIMA_ENABLED", raising=False)
    monkeypatch.delenv("RASTRO_CLIMA_BARCOS", raising=False)
    ordem: list[str] = []
    criadas: list[dict] = []

    class PoolFalso(_FakePool):
        def __init__(self, **kwargs):
            super().__init__(ordem=ordem, **kwargs)

    monkeypatch.setattr(main, "ConnectionPool", PoolFalso)
    return {"ordem": ordem, "criadas": criadas, "monkeypatch": monkeypatch}


def _agenda_falsa(ambiente, vivo: bool = False):
    ordem = ambiente["ordem"]
    criadas = ambiente["criadas"]

    class FakeAgenda:
        def __init__(self, pool, cfg, **kwargs):
            criadas.append({"pool": pool, "cfg": cfg})
            self.parar = _Evento(ordem)
            self.iniciado = False

        def iniciar(self, passo_s: float = 30.0):
            self.iniciado = True
            ordem.append("iniciar")
            return _FakeThread(ordem, vivo=vivo)

    ambiente["monkeypatch"].setattr(main, "AgendaClima", FakeAgenda)


def test_desligada_por_padrao_nao_cria_agenda(ambiente):
    _agenda_falsa(ambiente)
    with TestClient(main.create_app()):
        pass
    assert ambiente["criadas"] == []
    assert ambiente["ordem"] == ["pool.close"]


def test_ligada_inicia_agenda_e_para_antes_do_pool(ambiente, monkeypatch):
    monkeypatch.setenv("RASTRO_CLIMA_ENABLED", "1")
    _agenda_falsa(ambiente)
    with TestClient(main.create_app()):
        assert ambiente["ordem"] == ["iniciar"]
    assert len(ambiente["criadas"]) == 1
    assert len(ambiente["criadas"][0]["cfg"].barcos) == 5
    assert ambiente["ordem"] == [
        "iniciar",
        "parar.set",
        "thread.join(20)",
        "pool.close",
    ]


def test_falha_ao_ligar_agenda_ainda_fecha_pool(ambiente, monkeypatch):
    monkeypatch.setenv("RASTRO_CLIMA_ENABLED", "1")

    class AgendaQueFalha:
        def __init__(self, pool, cfg, **kwargs):
            raise RuntimeError("falha de teste")

    monkeypatch.setattr(main, "AgendaClima", AgendaQueFalha)
    with pytest.raises(RuntimeError, match="falha de teste"):
        with TestClient(main.create_app()):
            pass
    assert ambiente["ordem"] == ["pool.close"]


def test_thread_viva_no_encerramento_gera_aviso(ambiente, monkeypatch, caplog):
    monkeypatch.setenv("RASTRO_CLIMA_ENABLED", "1")
    caplog.set_level(logging.WARNING, logger=LOG)
    _agenda_falsa(ambiente, vivo=True)
    with TestClient(main.create_app()):
        pass
    assert ambiente["ordem"][-1] == "pool.close"
    assert any("ainda ativa" in r.getMessage() for r in caplog.records)
