"""Derivação determinística compartilhada do ingest nativo (H1).

As MESMAS fórmulas existem em dois lugares — aqui e no template CapRover
(bash). Os vetores dourados em ``tests/test_native_derive.py`` trancam as
duas implementações juntas: mudou aqui, muda lá e os vetores.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re

# Usuário de nó Meshtastic: '!' + 8 hex minúsculos (ex.: '!a0000001').
_USUARIO_RE = re.compile(r"^![0-9a-f]{8}$")


def vgw_for_boat(boat: str) -> tuple[int, str]:
    """Número de nó e id de gateway virtual determinísticos da chave do barco.

    ``n = (sha256(b"rastro-vgw:" + boat)[:4] big-endian | 0xE0000000) & 0xFFFFFFFE``
    — MSB da faixa de gateway setado, LSB (paridade do hex) zerado.
    """
    digest = hashlib.sha256(b"rastro-vgw:" + boat.encode()).digest()
    n = (int.from_bytes(digest[:4], "big") | 0xE0000000) & 0xFFFFFFFE
    return n, "!%08x" % n


def node_password(secret: str, user: str) -> str:
    """Senha MQTT do nó ``user`` derivada do segredo de instalação (HMAC-SHA256).

    Nunca logar o resultado: é uma senha.
    """
    digest = hmac.new(secret.encode(), b"node:" + user.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:32]


def parse_nodes(valor: str) -> list[tuple[str, str]]:
    """Parseia ``RASTRO_NATIVE_NODES`` (ex.: ``!a0000001=b1,!a0000002=b2``).

    Devolve pares ``(usuário, barco)`` na ordem do texto. Itens vazios
    (vírgula sobrando) são tolerados; item malformado gera ``ValueError``.
    """
    pares: list[tuple[str, str]] = []
    for item in (valor or "").split(","):
        item = item.strip()
        if not item:
            continue
        usuario, sep, barco = item.partition("=")
        usuario = usuario.strip()
        barco = barco.strip()
        if not sep or not barco:
            raise ValueError(
                f"Item inválido em RASTRO_NATIVE_NODES: '{item}' (esperado usuario=barco)"
            )
        if not _USUARIO_RE.match(usuario):
            raise ValueError(
                f"Usuário de nó inválido em RASTRO_NATIVE_NODES: '{usuario}' "
                "(esperado '!' + 8 hex minúsculos)"
            )
        pares.append((usuario, barco))
    return pares
