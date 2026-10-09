"""Configuração da previsão do tempo diária (variáveis ``RASTRO_CLIMA_*``).

``carregar`` lê o ambiente e devolve um ``ConfigClima``. Qualquer valor inválido loga um
erro em ``rastro_api.clima`` e devolve ``ativo=False`` com os padrões (a API sobe normal,
só sem previsão). Nunca levanta exceção.

Sensibilidade: lat/lon são dados sensíveis. O log de erro diz só o nome da variável ou
o motivo, NUNCA o valor, as coordenadas ou o JSON cru.
"""
from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

from rastro_api.api.clima_previsao import URL_PADRAO

log = logging.getLogger("rastro_api.clima")

SEGUNDOS_DIA = 24 * 3600
HORA_PADRAO = "08:00"
INTERVALO_PADRAO_S = 60
TTL_PADRAO_H = 6
MAX_IDADE_PADRAO_H = 48
UTC_OFFSET_PADRAO_H = -5


@dataclass(frozen=True)
class BarcoClima:
    """Barco que recebe a previsão. ``reserva`` é o ponto (lat, lon) usado sem fix recente."""

    boat_id: str
    regional: str
    reserva: tuple[float, float] | None = None


@dataclass(frozen=True)
class ConfigClima:
    """Configuração da agenda de previsão. ``ativo`` vem de ``RASTRO_CLIMA_ENABLED``."""

    ativo: bool
    barcos: tuple[BarcoClima, ...]
    hora: int
    minuto: int
    intervalo_s: int
    ttl_h: int
    max_idade_h: int
    utc_offset_h: int
    api_url: str

    @property
    def rotulo_hora(self) -> str:
        """Horário de envio como aparece no texto: ``"08h"`` ou ``"08h30"``."""
        if self.minuto == 0:
            return f"{self.hora:02d}h"
        return f"{self.hora:02d}h{self.minuto:02d}"


BARCOS_PADRAO = (
    BarcoClima("itui-1", "Ituí"),
    BarcoClima("itaquai-1", "Itaquaí"),
    BarcoClima("medio-javari-1", "Médio Javari"),
    BarcoClima("curuca-1", "Curuçá"),
    BarcoClima("jaquirana-1", "Jaquirana"),
)


class _Invalido(Exception):
    """Valor inválido. A mensagem nomeia a variável ou o motivo, nunca o valor."""


def _desligada() -> ConfigClima:
    """Configuração segura com padrões, usada quando algo no ambiente é inválido."""
    return ConfigClima(
        ativo=False,
        barcos=(),
        hora=8,
        minuto=0,
        intervalo_s=INTERVALO_PADRAO_S,
        ttl_h=TTL_PADRAO_H,
        max_idade_h=MAX_IDADE_PADRAO_H,
        utc_offset_h=UTC_OFFSET_PADRAO_H,
        api_url=URL_PADRAO,
    )


def _inteiro(env: Mapping[str, str], chave: str, padrao: int, minimo: int, maximo: int) -> int:
    bruto = env.get(chave)
    if bruto is None:
        return padrao
    try:
        valor = int(bruto)
    except (TypeError, ValueError):
        raise _Invalido(f"{chave} não é inteiro") from None
    if not minimo <= valor <= maximo:
        raise _Invalido(f"{chave} fora de {minimo}..{maximo}")
    return valor


def _hora(env: Mapping[str, str]) -> tuple[int, int]:
    bruto = env.get("RASTRO_CLIMA_HORA", HORA_PADRAO)
    if not re.fullmatch(r"[0-9]{2}:[0-9]{2}", bruto):
        raise _Invalido("RASTRO_CLIMA_HORA deve ser HH:MM")
    hora, minuto = int(bruto[:2]), int(bruto[3:])
    if hora > 23 or minuto > 59:
        raise _Invalido("RASTRO_CLIMA_HORA fora de 00:00..23:59")
    return hora, minuto


def _coordenada(valor: object, limite: float, nome: str, n: int) -> float:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise _Invalido(f"barco {n}: {nome} não é número")
    if not math.isfinite(valor):
        raise _Invalido(f"barco {n}: {nome} não é finito")
    if not -limite <= valor <= limite:
        raise _Invalido(f"barco {n}: {nome} fora do intervalo")
    return float(valor)


def _barco(obj: object, n: int) -> BarcoClima:
    if not isinstance(obj, dict):
        raise _Invalido(f"barco {n}: não é objeto")

    boat_id = obj.get("boat_id")
    if not isinstance(boat_id, str) or not boat_id.strip():
        raise _Invalido(f"barco {n}: boat_id ausente ou vazio")
    boat_id = boat_id.strip()
    if not re.fullmatch(r"[a-z0-9-]+", boat_id):
        raise _Invalido(f"barco {n}: boat_id com caracteres inválidos")

    regional = obj.get("regional")
    if not isinstance(regional, str) or not regional.strip():
        raise _Invalido(f"barco {n}: regional ausente ou vazio")
    regional = regional.strip()

    tem_lat = "lat" in obj
    tem_lon = "lon" in obj
    if tem_lat != tem_lon:
        raise _Invalido(f"barco {n}: lat e lon devem vir juntos")
    reserva = None
    if tem_lat:
        reserva = (
            _coordenada(obj["lat"], 90, "lat", n),
            _coordenada(obj["lon"], 180, "lon", n),
        )
    return BarcoClima(boat_id, regional, reserva)


def _barcos(env: Mapping[str, str]) -> tuple[BarcoClima, ...]:
    bruto = env.get("RASTRO_CLIMA_BARCOS")
    if bruto is None or not bruto.strip():
        return BARCOS_PADRAO
    try:
        dados = json.loads(bruto)
    except ValueError:
        raise _Invalido("RASTRO_CLIMA_BARCOS não é JSON válido") from None
    if not isinstance(dados, list) or not dados:
        raise _Invalido("RASTRO_CLIMA_BARCOS deve ser lista JSON não vazia")

    barcos = []
    vistos: set[str] = set()
    for n, obj in enumerate(dados, start=1):
        barco = _barco(obj, n)
        if barco.boat_id in vistos:
            raise _Invalido(f"barco {n}: boat_id repetido")
        vistos.add(barco.boat_id)
        barcos.append(barco)
    return tuple(barcos)


def _api_url(env: Mapping[str, str]) -> str:
    url = env.get("RASTRO_CLIMA_API_URL", URL_PADRAO)
    if not url.startswith(("https://", "http://")):
        raise _Invalido("RASTRO_CLIMA_API_URL deve começar com https:// ou http://")
    return url


def _carregar(env: Mapping[str, str]) -> ConfigClima:
    ligada = env.get("RASTRO_CLIMA_ENABLED", "0")
    if ligada not in ("0", "1"):
        raise _Invalido("RASTRO_CLIMA_ENABLED deve ser 0 ou 1")

    barcos = _barcos(env)
    hora, minuto = _hora(env)
    intervalo_s = _inteiro(env, "RASTRO_CLIMA_INTERVALO_S", INTERVALO_PADRAO_S, 1, 3600)
    ttl_h = _inteiro(env, "RASTRO_CLIMA_TTL_H", TTL_PADRAO_H, 1, 12)
    max_idade_h = _inteiro(env, "RASTRO_CLIMA_MAX_IDADE_H", MAX_IDADE_PADRAO_H, 1, 720)
    utc_offset_h = _inteiro(env, "RASTRO_CLIMA_UTC_OFFSET_H", UTC_OFFSET_PADRAO_H, -12, 14)
    api_url = _api_url(env)

    # O último envio tem de cair no mesmo dia local (antes de 24:00).
    ultimo_slot_s = (hora * 60 + minuto) * 60 + (len(barcos) - 1) * intervalo_s
    if ultimo_slot_s >= SEGUNDOS_DIA:
        raise _Invalido("o último envio cruza a meia-noite local")

    return ConfigClima(
        ativo=ligada == "1",
        barcos=barcos,
        hora=hora,
        minuto=minuto,
        intervalo_s=intervalo_s,
        ttl_h=ttl_h,
        max_idade_h=max_idade_h,
        utc_offset_h=utc_offset_h,
        api_url=api_url,
    )


def carregar(env: Mapping[str, str]) -> ConfigClima:
    """Lê as variáveis ``RASTRO_CLIMA_*``. Inválida → erro logado e ``ativo=False``."""
    try:
        return _carregar(env)
    except _Invalido as erro:
        log.error("clima: configuração inválida (%s); previsão desligada", erro)
    except Exception as erro:  # noqa: BLE001 - nunca derrubar a API por causa da previsão
        log.error("clima: configuração inválida (%s); previsão desligada", type(erro).__name__)
    return _desligada()
