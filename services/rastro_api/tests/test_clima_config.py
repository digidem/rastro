"""Testes da configuração da previsão (``api/clima_config.py``). Sem rede e sem Postgres.

Coordenadas de teste são fictícias (lat -5.0, lon -70.0). O log de erro nunca pode
conter esses números nem o JSON cru.
"""
from __future__ import annotations

import json
import logging

import pytest

from rastro_api.api import clima_config as cc
from rastro_api.api.clima_previsao import URL_PADRAO

LOG = "rastro_api.clima"
PROIBIDOS_NO_LOG = ("-5.0", "-70.0", "1e999")


def _env(**extra: str) -> dict[str, str]:
    return {"RASTRO_CLIMA_ENABLED": "1", **extra}


def _erros(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOG and r.levelno == logging.ERROR]


def test_padrao_com_ligada(caplog):
    cfg = cc.carregar(_env())
    assert cfg.ativo is True
    assert cfg.barcos == cc.BARCOS_PADRAO
    assert len(cfg.barcos) == 5
    assert (cfg.hora, cfg.minuto) == (8, 0)
    assert cfg.rotulo_hora == "08h"
    assert cfg.intervalo_s == 60
    assert cfg.ttl_h == 6
    assert cfg.max_idade_h == 48
    assert cfg.utc_offset_h == -5
    assert cfg.api_url == URL_PADRAO
    assert _erros(caplog) == []


def test_desligada_por_padrao_sem_erro(caplog):
    cfg = cc.carregar({})
    assert cfg.ativo is False
    assert cfg.barcos == cc.BARCOS_PADRAO
    assert _erros(caplog) == []


def test_enabled_zero_desliga(caplog):
    assert cc.carregar({"RASTRO_CLIMA_ENABLED": "0"}).ativo is False
    assert _erros(caplog) == []


def test_barcos_padrao_na_ordem_da_tabela():
    assert [(b.boat_id, b.regional) for b in cc.BARCOS_PADRAO] == [
        ("itui-1", "Ituí"),
        ("itaquai-1", "Itaquaí"),
        ("medio-javari-1", "Médio Javari"),
        ("curuca-1", "Curuçá"),
        ("jaquirana-1", "Jaquirana"),
    ]
    assert all(b.reserva is None for b in cc.BARCOS_PADRAO)


def test_barcos_vazio_ou_so_espacos_usa_padrao():
    assert cc.carregar(_env(RASTRO_CLIMA_BARCOS="   ")).barcos == cc.BARCOS_PADRAO
    assert cc.carregar(_env(RASTRO_CLIMA_BARCOS="")).barcos == cc.BARCOS_PADRAO


def test_rotulo_com_minutos():
    cfg = cc.carregar(_env(RASTRO_CLIMA_HORA="08:30"))
    assert (cfg.hora, cfg.minuto) == (8, 30)
    assert cfg.rotulo_hora == "08h30"


def test_json_custom_com_e_sem_reserva(caplog):
    barcos = [
        {"boat_id": "a-1", "regional": "Alfa"},
        {"boat_id": "b-2", "regional": "Beta", "lat": -5.0, "lon": -70.0},
    ]
    cfg = cc.carregar(_env(RASTRO_CLIMA_BARCOS=json.dumps(barcos)))
    assert cfg.ativo is True
    assert cfg.barcos == (
        cc.BarcoClima("a-1", "Alfa", None),
        cc.BarcoClima("b-2", "Beta", (-5.0, -70.0)),
    )
    assert _erros(caplog) == []


def test_reserva_inteira_vira_float():
    barcos = [{"boat_id": "a-1", "regional": "Alfa", "lat": -5, "lon": -70}]
    cfg = cc.carregar(_env(RASTRO_CLIMA_BARCOS=json.dumps(barcos)))
    reserva = cfg.barcos[0].reserva
    assert reserva == (-5.0, -70.0)
    assert all(isinstance(v, float) for v in reserva)


def test_limites_de_coordenada_sao_aceitos(caplog):
    barcos = [
        {"boat_id": "a-1", "regional": "Alfa", "lat": 90, "lon": -180},
        {"boat_id": "b-2", "regional": "Beta", "lat": -90, "lon": 180},
    ]
    cfg = cc.carregar(_env(RASTRO_CLIMA_BARCOS=json.dumps(barcos)))
    assert cfg.ativo is True
    assert _erros(caplog) == []


def test_espacos_em_boat_id_e_regional_sao_aparados():
    barcos = [{"boat_id": "  itui-1 ", "regional": "  Ituí  "}]
    cfg = cc.carregar(_env(RASTRO_CLIMA_BARCOS=json.dumps(barcos)))
    assert cfg.barcos == (cc.BarcoClima("itui-1", "Ituí", None),)


def test_chaves_extras_sao_ignoradas(caplog):
    barcos = [{"boat_id": "a-1", "regional": "Alfa", "nome": "x", "extra": [1, 2]}]
    cfg = cc.carregar(_env(RASTRO_CLIMA_BARCOS=json.dumps(barcos)))
    assert cfg.ativo is True
    assert _erros(caplog) == []


def test_api_url_custom_aceita_http_e_https(caplog):
    assert cc.carregar(_env(RASTRO_CLIMA_API_URL="http://localhost:9/v1")).api_url == (
        "http://localhost:9/v1"
    )
    assert cc.carregar(_env(RASTRO_CLIMA_API_URL="https://exemplo.test/f")).api_url == (
        "https://exemplo.test/f"
    )
    assert _erros(caplog) == []


def test_inteiros_e_hora_customizados(caplog):
    cfg = cc.carregar(
        _env(
            RASTRO_CLIMA_HORA="07:15",
            RASTRO_CLIMA_INTERVALO_S="120",
            RASTRO_CLIMA_TTL_H="12",
            RASTRO_CLIMA_MAX_IDADE_H="720",
            RASTRO_CLIMA_UTC_OFFSET_H="-12",
        )
    )
    assert (cfg.hora, cfg.minuto, cfg.intervalo_s) == (7, 15, 120)
    assert (cfg.ttl_h, cfg.max_idade_h, cfg.utc_offset_h) == (12, 720, -12)
    assert _erros(caplog) == []


def test_ultimo_slot_no_limite_do_dia_e_valido(caplog):
    # 23:59 com um barco: o único slot cai às 23:59, dentro do dia.
    barcos = [{"boat_id": "a-1", "regional": "Alfa"}]
    cfg = cc.carregar(_env(RASTRO_CLIMA_BARCOS=json.dumps(barcos), RASTRO_CLIMA_HORA="23:59"))
    assert cfg.ativo is True
    assert _erros(caplog) == []


def test_log_nunca_cita_coordenadas_nem_json(caplog):
    caplog.set_level(logging.ERROR, logger=LOG)
    bruto = json.dumps([{"boat_id": "a-1", "regional": "Alfa", "lat": -5.0}])
    cfg = cc.carregar(_env(RASTRO_CLIMA_BARCOS=bruto))
    assert cfg.ativo is False
    erros = _erros(caplog)
    assert len(erros) == 1
    mensagem = erros[0].getMessage()
    assert "lat" in mensagem  # só o nome do campo
    for proibido in PROIBIDOS_NO_LOG + (bruto,):
        assert proibido not in mensagem


INVALIDOS = [
    pytest.param({"RASTRO_CLIMA_ENABLED": "true"}, id="enabled-nao-binario"),
    pytest.param({"RASTRO_CLIMA_ENABLED": ""}, id="enabled-vazio"),
    pytest.param({"RASTRO_CLIMA_BARCOS": "["}, id="barcos-json-quebrado"),
    pytest.param({"RASTRO_CLIMA_BARCOS": "[]"}, id="barcos-lista-vazia"),
    pytest.param({"RASTRO_CLIMA_BARCOS": "{}"}, id="barcos-nao-lista"),
    pytest.param({"RASTRO_CLIMA_BARCOS": "[1]"}, id="barco-nao-objeto"),
    pytest.param({"RASTRO_CLIMA_BARCOS": '[{"regional":"A"}]'}, id="boat-id-ausente"),
    pytest.param({"RASTRO_CLIMA_BARCOS": '[{"boat_id":"  ","regional":"A"}]'}, id="boat-id-vazio"),
    pytest.param({"RASTRO_CLIMA_BARCOS": '[{"boat_id":"Itui-1","regional":"A"}]'}, id="boat-id-maiuscula"),
    pytest.param({"RASTRO_CLIMA_BARCOS": '[{"boat_id":"itui_1","regional":"A"}]'}, id="boat-id-underscore"),
    pytest.param({"RASTRO_CLIMA_BARCOS": '[{"boat_id":"a-1","regional":" "}]'}, id="regional-vazio"),
    pytest.param({"RASTRO_CLIMA_BARCOS": '[{"boat_id":"a-1"}]'}, id="regional-ausente"),
    pytest.param(
        {"RASTRO_CLIMA_BARCOS": '[{"boat_id":"a-1","regional":"A"},{"boat_id":"a-1","regional":"B"}]'},
        id="boat-id-repetido",
    ),
    pytest.param(
        {"RASTRO_CLIMA_BARCOS": '[{"boat_id":"a-1","regional":"A","lat":-5.0}]'},
        id="so-lat",
    ),
    pytest.param(
        {"RASTRO_CLIMA_BARCOS": '[{"boat_id":"a-1","regional":"A","lon":-70.0}]'},
        id="so-lon",
    ),
    pytest.param(
        {"RASTRO_CLIMA_BARCOS": '[{"boat_id":"a-1","regional":"A","lat":true,"lon":-70.0}]'},
        id="lat-bool",
    ),
    pytest.param(
        {"RASTRO_CLIMA_BARCOS": '[{"boat_id":"a-1","regional":"A","lat":-5.0,"lon":true}]'},
        id="lon-bool",
    ),
    pytest.param(
        {"RASTRO_CLIMA_BARCOS": '[{"boat_id":"a-1","regional":"A","lat":"-5.0","lon":-70.0}]'},
        id="lat-string",
    ),
    pytest.param(
        {"RASTRO_CLIMA_BARCOS": '[{"boat_id":"a-1","regional":"A","lat":null,"lon":-70.0}]'},
        id="lat-null",
    ),
    pytest.param(
        {"RASTRO_CLIMA_BARCOS": '[{"boat_id":"a-1","regional":"A","lat":1e999,"lon":-70.0}]'},
        id="lat-infinito",
    ),
    pytest.param(
        {"RASTRO_CLIMA_BARCOS": '[{"boat_id":"a-1","regional":"A","lat":-5.0,"lon":1e999}]'},
        id="lon-infinito",
    ),
    pytest.param(
        {"RASTRO_CLIMA_BARCOS": '[{"boat_id":"a-1","regional":"A","lat":91,"lon":-70.0}]'},
        id="lat-acima-90",
    ),
    pytest.param(
        {"RASTRO_CLIMA_BARCOS": '[{"boat_id":"a-1","regional":"A","lat":-5.0,"lon":-181}]'},
        id="lon-abaixo-180",
    ),
    pytest.param({"RASTRO_CLIMA_HORA": "8:00"}, id="hora-sem-zero"),
    pytest.param({"RASTRO_CLIMA_HORA": "08:00 "}, id="hora-com-espaco"),
    pytest.param({"RASTRO_CLIMA_HORA": "08-00"}, id="hora-separador"),
    pytest.param({"RASTRO_CLIMA_HORA": "24:00"}, id="hora-24"),
    pytest.param({"RASTRO_CLIMA_HORA": "08:60"}, id="minuto-60"),
    pytest.param({"RASTRO_CLIMA_INTERVALO_S": "0"}, id="intervalo-zero"),
    pytest.param({"RASTRO_CLIMA_INTERVALO_S": "3601"}, id="intervalo-acima"),
    pytest.param({"RASTRO_CLIMA_INTERVALO_S": "abc"}, id="intervalo-texto"),
    pytest.param({"RASTRO_CLIMA_INTERVALO_S": "1.5"}, id="intervalo-decimal"),
    pytest.param({"RASTRO_CLIMA_TTL_H": "0"}, id="ttl-zero"),
    pytest.param({"RASTRO_CLIMA_TTL_H": "13"}, id="ttl-acima"),
    pytest.param({"RASTRO_CLIMA_MAX_IDADE_H": "0"}, id="idade-zero"),
    pytest.param({"RASTRO_CLIMA_MAX_IDADE_H": "721"}, id="idade-acima"),
    pytest.param({"RASTRO_CLIMA_UTC_OFFSET_H": "-13"}, id="offset-abaixo"),
    pytest.param({"RASTRO_CLIMA_UTC_OFFSET_H": "15"}, id="offset-acima"),
    pytest.param({"RASTRO_CLIMA_API_URL": "ftp://exemplo.test"}, id="url-ftp"),
    pytest.param({"RASTRO_CLIMA_API_URL": "exemplo.test/f"}, id="url-sem-esquema"),
    pytest.param({"RASTRO_CLIMA_HORA": "23:59"}, id="ultimo-slot-cruza-dia"),
    pytest.param(
        {"RASTRO_CLIMA_HORA": "23:00", "RASTRO_CLIMA_BARCOS": '[{"boat_id":"a-1","regional":"A"},{"boat_id":"b-2","regional":"B"}]', "RASTRO_CLIMA_INTERVALO_S": "3600"},
        id="ultimo-slot-exatamente-24h",
    ),
]


@pytest.mark.parametrize("extra", INVALIDOS)
def test_invalido_desliga_e_loga_uma_vez(extra, caplog):
    caplog.set_level(logging.ERROR, logger=LOG)
    cfg = cc.carregar(_env(**extra))
    assert cfg.ativo is False
    assert cfg.barcos == ()
    erros = _erros(caplog)
    assert len(erros) == 1
    mensagem = erros[0].getMessage()
    for proibido in PROIBIDOS_NO_LOG:
        assert proibido not in mensagem
    for valor in extra.values():
        if len(valor) > 8:
            assert valor not in mensagem


def test_invalido_com_enabled_zero_tambem_loga(caplog):
    caplog.set_level(logging.ERROR, logger=LOG)
    cfg = cc.carregar({"RASTRO_CLIMA_ENABLED": "0", "RASTRO_CLIMA_TTL_H": "0"})
    assert cfg.ativo is False
    assert len(_erros(caplog)) == 1


def test_carregar_nunca_levanta_com_mapping_estranho(caplog):
    class Quebrado(dict):
        def get(self, *_a, **_k):  # força erro inesperado no acesso ao ambiente
            raise RuntimeError("boom")

    caplog.set_level(logging.ERROR, logger=LOG)
    cfg = cc.carregar(Quebrado())
    assert cfg.ativo is False
    assert len(_erros(caplog)) == 1
