"""Composição do texto da previsão enviado no rádio (texto aprovado pelo dono).

``compor`` monta o texto a partir de um ``Resumo``. Se passar de ``LIMITE_BYTES`` bytes
UTF-8, aplica as reduções em ordem, parando assim que couber. Só o tamanho em bytes vai
ao log; o texto nunca é logado.
"""
from __future__ import annotations

import datetime
import logging

from rastro_api.api.clima_previsao import Resumo

log = logging.getLogger("rastro_api.clima")

LIMITE_BYTES = 200
META_BYTES = 180
REGIONAL_CURTO = 12  # caracteres mantidos quando o regional é cortado

# Reduções em ordem; cada uma só roda enquanto o texto ainda passa de LIMITE_BYTES.
PASSOS = (
    {"com_trovoada": False},  # 1. remove "Sem trovoada."
    {"com_hora": False},  # 2. remove " {hora}"
    {"vento_curto": True},  # 3. vento vira "Vento {rajada} km/h."
    {"regional_curto": True},  # 4. regional cortado em 12 caracteres
)


def _classe_vento(kmh: int) -> str:
    if kmh < 20:
        return "fraco"
    if kmh <= 40:
        return "moderado"
    return "forte"


def _alerta(r: Resumo) -> str:
    chuva_forte = r.chuva_mm >= 30
    ventania = r.rajada_kmh >= 50
    if chuva_forte and ventania:
        return "ALERTA: chuva forte e ventania"
    if chuva_forte:
        return "ALERTA: chuva forte"
    if ventania:
        return "ALERTA: ventania"
    return ""


def _texto(
    regional: str,
    dia: datetime.date,
    hora: str,
    r: Resumo,
    *,
    com_hora: bool,
    com_trovoada: bool,
    vento_curto: bool,
    regional_curto: bool,
) -> str:
    """Monta o texto com as partes que as reduções ainda deixam."""
    nome = regional[:REGIONAL_CURTO].rstrip() if regional_curto else regional
    cabecalho = f"{nome} – {dia.strftime('%d/%m')}"
    if com_hora:
        cabecalho += f" {hora}"

    if r.chance_chuva < 20:
        chuva = "Sem chuva prevista"
    else:
        chuva = f"Chuva {r.periodo}, chance {r.chance_chuva}%"

    frases = [f"Hoje: {r.temp_min}–{r.temp_max}°C.", f"{chuva}."]
    if r.trovoada:
        frases.append("Trovoada provável.")
    elif com_trovoada:
        frases.append("Sem trovoada.")

    if vento_curto:
        frases.append(f"Vento {r.rajada_kmh} km/h.")
    else:
        classe = _classe_vento(r.vento_kmh)
        frases.append(f"Vento {classe}, rajadas {r.rajada_kmh} km/h.")

    texto = f"{cabecalho} | " + " ".join(frases)
    alerta = _alerta(r)
    if alerta:
        texto += f" {alerta}"
    return texto


def compor(regional: str, dia: datetime.date, hora: str, r: Resumo) -> str:
    """Texto da previsão de um barco, com no máximo ``LIMITE_BYTES`` bytes UTF-8.

    ``hora`` é o rótulo do horário de envio (``"08h"`` ou ``"08h30"``).
    """
    flags = {
        "com_hora": True,
        "com_trovoada": True,
        "vento_curto": False,
        "regional_curto": False,
    }
    texto = _texto(regional, dia, hora, r, **flags)
    for passo in PASSOS:
        if len(texto.encode("utf-8")) <= LIMITE_BYTES:
            break
        flags.update(passo)
        texto = _texto(regional, dia, hora, r, **flags)

    # Corte final por bytes; ``ignore`` descarta o caractere UTF-8 incompleto no fim.
    if len(texto.encode("utf-8")) > LIMITE_BYTES:
        texto = texto.encode("utf-8")[:LIMITE_BYTES].decode("utf-8", errors="ignore")

    tamanho = len(texto.encode("utf-8"))
    if tamanho > META_BYTES:
        log.warning("clima: texto da previsão com %d bytes (meta %d)", tamanho, META_BYTES)
    return texto
