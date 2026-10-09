"""Previsão do tempo (api/clima_previsao.py) — sem rede e com coordenadas fictícias.

``buscar`` recebe um ``abrir`` falso; ``resumir`` recebe dicionários no formato do
Open-Meteo. Nenhuma coordenada real aparece aqui (lat -5.0, lon -70.0 são fictícias).
"""
import io
import urllib.error
import urllib.parse

import pytest

from rastro_api.api import clima_previsao as cp
from rastro_api.api.clima_previsao import PrevisaoErro, Resumo, buscar, montar_url, resumir

LAT, LON = -5.0, -70.0


class _Resp:
    def __init__(self, corpo=b"{}", status=200):
        self._corpo, self.status = corpo, status
        self.fechado = False

    def read(self, n=-1):
        return self._corpo

    def close(self):
        self.fechado = True


def _abrir_que_responde(resp):
    def abrir(url, timeout=None):
        return resp

    return abrir


def _abrir_que_levanta(erro):
    def abrir(url, timeout=None):
        raise erro

    return abrir


def _horas():
    return [f"2026-10-09T{h:02d}:00" for h in range(24)]


def _serie(mapa, padrao=0):
    """Lista de 24 valores (uma por hora local) a partir de ``{hora: valor}``."""
    return [mapa.get(h, padrao) for h in range(24)]


def _dados(prob=None, codigo=None, vento=None, rajada=None, *, temp=(22.0, 33.0), chuva=0.0):
    return {
        "daily": {
            "temperature_2m_min": [temp[0]],
            "temperature_2m_max": [temp[1]],
            "precipitation_sum": [chuva],
        },
        "hourly": {
            "time": _horas(),
            "precipitation_probability": _serie(prob or {}),
            "weather_code": _serie(codigo or {}),
            "wind_speed_10m": _serie(vento or {}),
            "wind_gusts_10m": _serie(rajada or {}),
        },
    }


# ---------------------------------------------------------------- montar_url


def test_montar_url_tem_todos_os_parametros():
    url = montar_url(LAT, LON)
    assert url.startswith(cp.URL_PADRAO + "?")
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert qs["latitude"] == [str(LAT)]
    assert qs["longitude"] == [str(LON)]
    assert qs["hourly"] == [
        "precipitation_probability,weather_code,wind_speed_10m,wind_gusts_10m"
    ]
    assert qs["daily"] == ["temperature_2m_min,temperature_2m_max,precipitation_sum"]
    assert qs["timezone"] == ["America/Eirunepe"]
    assert qs["forecast_days"] == ["1"]
    assert qs["wind_speed_unit"] == ["kmh"]


def test_montar_url_usa_base_informada():
    url = montar_url(LAT, LON, base="http://fake.local/v1")
    assert url.startswith("http://fake.local/v1?")


# ---------------------------------------------------------------- buscar


def test_buscar_sucesso_devolve_json_e_fecha_resposta():
    resp = _Resp(b'{"daily": {}}')
    assert buscar(LAT, LON, abrir=_abrir_que_responde(resp)) == {"daily": {}}
    assert resp.fechado


def test_buscar_usa_base_da_env(monkeypatch):
    monkeypatch.setenv("RASTRO_CLIMA_API_URL", "http://env.local/forecast")
    usados = []

    def abrir(url, timeout=None):
        usados.append(url)
        return _Resp()

    buscar(LAT, LON, abrir=abrir)
    assert usados[0].startswith("http://env.local/forecast?")


def test_buscar_http_500_levanta_sem_url_nem_coordenadas():
    erro = urllib.error.HTTPError(cp.URL_PADRAO, 500, "boom", None, io.BytesIO(b""))
    with pytest.raises(PrevisaoErro) as info:
        buscar(LAT, LON, abrir=_abrir_que_levanta(erro))
    assert str(info.value) == "Open-Meteo: HTTP 500"


def test_buscar_status_nao_200_levanta():
    with pytest.raises(PrevisaoErro) as info:
        buscar(LAT, LON, abrir=_abrir_que_responde(_Resp(status=503)))
    assert str(info.value) == "Open-Meteo: HTTP 503"


def test_buscar_timeout_levanta_nome_da_classe():
    with pytest.raises(PrevisaoErro) as info:
        buscar(LAT, LON, abrir=_abrir_que_levanta(TimeoutError("lento")))
    assert str(info.value) == "Open-Meteo: TimeoutError"


def test_buscar_timeout_embrulhado_em_urlerror():
    erro = urllib.error.URLError(TimeoutError("lento"))
    with pytest.raises(PrevisaoErro) as info:
        buscar(LAT, LON, abrir=_abrir_que_levanta(erro))
    assert str(info.value) == "Open-Meteo: TimeoutError"


def test_buscar_json_invalido_levanta():
    with pytest.raises(PrevisaoErro) as info:
        buscar(LAT, LON, abrir=_abrir_que_responde(_Resp(b"<html>")))
    assert str(info.value) == "Open-Meteo: JSON inválido"


def test_buscar_json_que_nao_e_objeto_levanta():
    with pytest.raises(PrevisaoErro):
        buscar(LAT, LON, abrir=_abrir_que_responde(_Resp(b"[1, 2]")))


def test_erros_de_buscar_nao_vazam_coordenadas_nem_url():
    casos = [
        _abrir_que_levanta(TimeoutError()),
        _abrir_que_responde(_Resp(status=500)),
        _abrir_que_responde(_Resp("{não json".encode("utf-8"))),
        _abrir_que_levanta(ConnectionResetError("reset")),
    ]
    for abrir in casos:
        with pytest.raises(PrevisaoErro) as info:
            buscar(LAT, LON, abrir=abrir)
        msg = str(info.value)
        assert "-5.0" not in msg and "-70.0" not in msg
        assert "://" not in msg and "api.open-meteo" not in msg
        assert "latitude" not in msg


# ---------------------------------------------------------------- resumir


def test_resumir_manha():
    r = resumir(_dados(prob={8: 60}))
    assert r.chance_chuva == 60
    assert r.periodo == "de manhã"


def test_resumir_tarde():
    r = resumir(_dados(prob={14: 40}))
    assert r.periodo == "à tarde"


def test_resumir_noite():
    r = resumir(_dados(prob={20: 45}))
    assert r.periodo == "à noite"


def test_resumir_o_dia_todo_quando_os_tres_periodos_passam_de_50():
    r = resumir(_dados(prob={8: 50, 14: 60, 20: 55}))
    assert r.periodo == "o dia todo"
    assert r.chance_chuva == 60


def test_resumir_dois_periodos_altos_nao_e_o_dia_todo():
    r = resumir(_dados(prob={8: 80, 14: 70}))
    assert r.periodo == "de manhã"


def test_resumir_chance_abaixo_de_20_zera_periodo():
    r = resumir(_dados(prob={14: 19}))
    assert r.chance_chuva == 19
    assert r.periodo is None


def test_resumir_chance_de_20_tem_periodo():
    r = resumir(_dados(prob={14: 20}))
    assert r.periodo == "à tarde"


def test_resumir_empate_de_pico_fica_com_a_primeira_hora():
    r = resumir(_dados(prob={9: 70, 15: 70}))
    assert r.periodo == "de manhã"


def test_resumir_ignora_horas_fora_da_janela():
    # chuva às 03:00 e às 23:00 não entra na janela 06–22
    r = resumir(_dados(prob={3: 90, 23: 90}))
    assert r.chance_chuva == 0
    assert r.periodo is None


@pytest.mark.parametrize("codigo", [95, 96, 99])
def test_resumir_trovoada_nos_codigos_95_96_99(codigo):
    assert resumir(_dados(codigo={15: codigo})).trovoada is True


def test_resumir_codigo_95_fora_da_janela_nao_conta():
    assert resumir(_dados(codigo={3: 95})).trovoada is False


def test_resumir_sem_trovoada_com_outro_codigo():
    assert resumir(_dados(codigo={15: 61})).trovoada is False


def test_resumir_vento_e_rajada_maximos_da_janela():
    r = resumir(_dados(vento={10: 25.4, 11: 12}, rajada={10: 30.0, 16: 48.6}))
    assert r.vento_kmh == 25
    assert r.rajada_kmh == 49


def test_resumir_vento_fora_da_janela_nao_conta():
    r = resumir(_dados(vento={3: 90}, rajada={23: 99}))
    assert r.vento_kmh == 0
    assert r.rajada_kmh == 0


def test_resumir_temperaturas_arredondadas_e_chuva_em_mm():
    r = resumir(_dados(temp=(21.6, 32.4), chuva=12.5))
    assert r.temp_min == 22
    assert r.temp_max == 32
    assert r.chuva_mm == 12.5


def test_resumir_listas_com_none_sao_ignoradas():
    dados = _dados(prob={14: 40})
    dados["hourly"]["precipitation_probability"][8] = None
    dados["hourly"]["wind_speed_10m"] = [None] * 24
    dados["hourly"]["wind_speed_10m"][12] = 30
    dados["hourly"]["weather_code"][15] = None
    r = resumir(dados)
    assert r.chance_chuva == 40
    assert r.periodo == "à tarde"
    assert r.vento_kmh == 30
    assert r.trovoada is False


def test_resumir_janela_vazia_zera_valores():
    dados = {
        "daily": {
            "temperature_2m_min": [20.0],
            "temperature_2m_max": [30.0],
            "precipitation_sum": [0.0],
        },
        "hourly": {"time": ["2026-10-09T03:00", "2026-10-09T23:00"]},
    }
    r = resumir(dados)
    assert r == Resumo(
        temp_min=20,
        temp_max=30,
        chance_chuva=0,
        periodo=None,
        trovoada=False,
        vento_kmh=0,
        rajada_kmh=0,
        chuva_mm=0.0,
    )


def test_resumir_sem_bloco_hourly_zera_valores():
    dados = _dados()
    del dados["hourly"]
    r = resumir(dados)
    assert r.chance_chuva == 0
    assert r.trovoada is False


def test_resumir_daily_ausente_levanta():
    dados = _dados()
    del dados["daily"]
    with pytest.raises(PrevisaoErro) as info:
        resumir(dados)
    assert str(info.value) == "Open-Meteo: resposta incompleta"


@pytest.mark.parametrize("chave", ["temperature_2m_min", "temperature_2m_max", "precipitation_sum"])
def test_resumir_serie_diaria_vazia_ou_nula_levanta(chave):
    dados = _dados()
    dados["daily"][chave] = [None]
    with pytest.raises(PrevisaoErro) as info:
        resumir(dados)
    assert str(info.value) == "Open-Meteo: resposta incompleta"
    dados["daily"][chave] = []
    with pytest.raises(PrevisaoErro):
        resumir(dados)
