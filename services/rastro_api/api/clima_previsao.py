"""Busca e resumo da previsão do tempo diária para os barcos (Open-Meteo).

Fluxo: ``montar_url`` monta a consulta, ``buscar`` faz o GET (só biblioteca padrão,
como ``api/osm.py``) e ``resumir`` reduz as séries horárias e diárias a um ``Resumo``
com as regras do texto aprovado pelo dono (janela 06–22 local).

Sensibilidade: a posição do barco é dado sensível. Este módulo NUNCA põe coordenadas,
URL ou corpo da resposta em log, mensagem de erro ou repr. Erros de rede saem só com o
tipo da exceção.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

URL_PADRAO = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_PADRAO = 15.0
JANELA_INICIO = 6  # hora local, inclusive
JANELA_FIM = 22  # hora local, inclusive
CODIGOS_TROVOADA = frozenset({95, 96, 99})
PERIODOS = (
    ("de manhã", 6, 11),
    ("à tarde", 12, 17),
    ("à noite", 18, 22),
)


class PrevisaoErro(Exception):
    """Falha ao buscar ou interpretar a previsão. A mensagem nunca tem URL nem coordenadas."""


@dataclass(frozen=True)
class Resumo:
    """Previsão do dia para um ponto, já reduzida ao que vai no texto do rádio."""

    temp_min: int
    temp_max: int
    chance_chuva: int
    periodo: str | None  # None quando chance_chuva < 20
    trovoada: bool
    vento_kmh: int
    rajada_kmh: int
    chuva_mm: float


def montar_url(lat: float, lon: float, base: str = URL_PADRAO) -> str:
    """URL do forecast para um ponto, com as séries horárias e diárias usadas no resumo."""
    consulta = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "hourly": "precipitation_probability,weather_code,wind_speed_10m,wind_gusts_10m",
            "daily": "temperature_2m_min,temperature_2m_max,precipitation_sum",
            "timezone": "America/Eirunepe",
            "forecast_days": 1,
            "wind_speed_unit": "kmh",
        },
        quote_via=urllib.parse.quote,
        safe=",/",
    )
    return f"{base}?{consulta}"


def buscar(
    lat: float,
    lon: float,
    *,
    base: str | None = None,
    timeout: float = TIMEOUT_PADRAO,
    abrir: Callable[..., Any] = urllib.request.urlopen,
) -> dict:
    """GET no Open-Meteo e devolve o JSON. Qualquer falha vira ``PrevisaoErro`` sem URL."""
    base = base or os.environ.get("RASTRO_CLIMA_API_URL") or URL_PADRAO
    url = montar_url(lat, lon, base=base)
    try:
        resp = abrir(url, timeout=timeout)
    except urllib.error.HTTPError as erro:
        raise PrevisaoErro(f"Open-Meteo: HTTP {erro.code}") from None
    except urllib.error.URLError as erro:
        # Timeout chega embrulhado em URLError; o motivo é o nome da classe, sem str().
        motivo = erro.reason if isinstance(erro.reason, OSError) else erro
        raise PrevisaoErro(f"Open-Meteo: {type(motivo).__name__}") from None
    except Exception as erro:  # noqa: BLE001 - qualquer falha de transporte
        raise PrevisaoErro(f"Open-Meteo: {type(erro).__name__}") from None

    try:
        status = getattr(resp, "status", 200)
        if status != 200:
            raise PrevisaoErro(f"Open-Meteo: HTTP {status}")
        corpo = resp.read()
    except PrevisaoErro:
        raise
    except Exception as erro:  # noqa: BLE001
        raise PrevisaoErro(f"Open-Meteo: {type(erro).__name__}") from None
    finally:
        fechar = getattr(resp, "close", None)
        if callable(fechar):
            fechar()

    try:
        dados = json.loads(corpo)
    except (ValueError, TypeError):
        raise PrevisaoErro("Open-Meteo: JSON inválido") from None
    if not isinstance(dados, dict):
        raise PrevisaoErro("Open-Meteo: JSON inválido")
    return dados


def _serie(bloco: dict, chave: str) -> list:
    valor = bloco.get(chave)
    return valor if isinstance(valor, list) else []


def _valor(lista: Sequence, i: int) -> Any:
    return lista[i] if i < len(lista) else None


def _hora(texto: str) -> int | None:
    """Hora (0–23) de ``"2026-10-09T06:00"``; ``None`` se o formato não bater."""
    try:
        return int(texto.split("T", 1)[1].split(":", 1)[0])
    except (IndexError, ValueError):
        return None


def _diario(dados: dict, chave: str) -> float:
    """Primeiro valor (índice 0) de uma série diária; ausente ou nulo é erro."""
    daily = dados.get("daily")
    serie = daily.get(chave) if isinstance(daily, dict) else None
    if not isinstance(serie, list) or not serie or serie[0] is None:
        raise PrevisaoErro("Open-Meteo: resposta incompleta")
    try:
        return float(serie[0])
    except (TypeError, ValueError):
        raise PrevisaoErro("Open-Meteo: resposta incompleta") from None


def resumir(dados: dict) -> Resumo:
    """Reduz a resposta do Open-Meteo ao ``Resumo`` (regras do texto aprovado)."""
    temp_min = _diario(dados, "temperature_2m_min")
    temp_max = _diario(dados, "temperature_2m_max")
    chuva_mm = _diario(dados, "precipitation_sum")

    hourly = dados.get("hourly")
    hourly = hourly if isinstance(hourly, dict) else {}
    horas = _serie(hourly, "time")
    probs = _serie(hourly, "precipitation_probability")
    codigos = _serie(hourly, "weather_code")
    ventos = _serie(hourly, "wind_speed_10m")
    rajadas = _serie(hourly, "wind_gusts_10m")

    # Pares (hora, prob) da janela 06–22; o empate de pico fica com a primeira hora.
    janela = []
    for i, texto in enumerate(horas):
        hora = _hora(texto) if isinstance(texto, str) else None
        if hora is not None and JANELA_INICIO <= hora <= JANELA_FIM:
            janela.append((i, hora))

    def maximo(lista: Sequence, indices: Sequence[int]) -> float | None:
        valores = [_valor(lista, i) for i in indices]
        valores = [v for v in valores if isinstance(v, (int, float))]
        return max(valores) if valores else None

    indices = [i for i, _ in janela]
    chance_bruta = maximo(probs, indices)
    chance = int(round(chance_bruta)) if chance_bruta is not None else 0

    periodo: str | None = None
    if chance >= 20:
        maximos_periodo = []
        for _, inicio, fim in PERIODOS:
            idx = [i for i, hora in janela if inicio <= hora <= fim]
            m = maximo(probs, idx)
            maximos_periodo.append(m if m is not None else 0)
        if all(m >= 50 for m in maximos_periodo):
            periodo = "o dia todo"
        else:
            pico_i = None
            pico_v = None
            for i in indices:
                v = _valor(probs, i)
                if isinstance(v, (int, float)) and (pico_v is None or v > pico_v):
                    pico_i, pico_v = i, v
            hora_pico = _hora(horas[pico_i]) if pico_i is not None else None
            for nome, inicio, fim in PERIODOS:
                if hora_pico is not None and inicio <= hora_pico <= fim:
                    periodo = nome
                    break

    trovoada = any(
        isinstance(_valor(codigos, i), (int, float))
        and int(_valor(codigos, i)) in CODIGOS_TROVOADA
        for i in indices
    )

    vento = maximo(ventos, indices)
    rajada = maximo(rajadas, indices)

    return Resumo(
        temp_min=int(round(temp_min)),
        temp_max=int(round(temp_max)),
        chance_chuva=chance,
        periodo=periodo,
        trovoada=trovoada,
        vento_kmh=int(round(vento)) if vento is not None else 0,
        rajada_kmh=int(round(rajada)) if rajada is not None else 0,
        chuva_mm=chuva_mm,
    )
