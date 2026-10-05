"""Cripto do protocolo Meshtastic (AES-CTR, expansão de PSK e hash de canal). Ver docs/native-ingest-design.md §1."""
from __future__ import annotations

import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# PSK padrão do Meshtastic: 1 byte N expande para esta chave com o último byte
# ajustado em +(N-1). Índice 0 = "sem cifra" → rejeitado (design §1).
PSK_PADRAO_HEX = "d4f1bb3a20290759f0bcffabcf4e6901"

_NONCE = struct.Struct("<QI4s")  # id u64 LE + from u32 LE + 4 bytes zero


def expand_psk(psk: bytes) -> bytes:
    """Expande PSK: 1 byte N → chave padrão com último byte +(N-1); 16/32 → igual; senão ValueError."""
    if len(psk) == 1:
        indice = psk[0]
        if indice == 0:
            raise ValueError("psk indice 0 = sem cifra (rejeitado)")
        chave = bytearray(bytes.fromhex(PSK_PADRAO_HEX))
        chave[15] = (chave[15] + indice - 1) & 0xFF
        return bytes(chave)
    if len(psk) in (16, 32):
        return bytes(psk)
    raise ValueError(f"psk com tamanho invalido: {len(psk)} bytes (use 1, 16 ou 32)")


def channel_hash(channel_name: str, key: bytes) -> int:
    """Hash de canal = XOR dos bytes do nome XOR dos bytes da chave expandida (a mesma da cifra)."""
    chave = expand_psk(key)
    h = 0
    for b in channel_name.encode("utf-8") + chave:
        h ^= b
    return h


def crypt(key: bytes, packet_id: int, from_num: int, data: bytes) -> bytes:
    """Cifra/decifra AES-CTR; nonce = id u64 LE + from u32 LE + 4 zeros. CTR é simétrico."""
    nonce = _NONCE.pack(packet_id & 0xFFFFFFFFFFFFFFFF, from_num & 0xFFFFFFFF, b"")
    cipher = Cipher(algorithms.AES(key), modes.CTR(nonce))
    enc = cipher.encryptor()
    return enc.update(data) + enc.finalize()
