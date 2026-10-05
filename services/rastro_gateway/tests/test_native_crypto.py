"""Testes da cripto Meshtastic pura (WP-A): expand_psk, channel_hash e crypt."""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from rastro_gateway.native import crypto

TEST_PSK = bytes(range(32))

# Chave padrão do firmware Meshtastic (crypto.PSK_PADRAO_HEX), citada de forma
# independente aqui para não herdar erro de digitação da implementação.
_CHAVE_PADRAO = bytes.fromhex("d4f1bb3a20290759f0bcffabcf4e6901")


def test_expand_psk_indice_1_igual_chave_padrao() -> None:
    assert crypto.expand_psk(b"\x01") == _CHAVE_PADRAO


def test_expand_psk_indice_2_ajusta_ultimo_byte() -> None:
    chave = crypto.expand_psk(b"\x02")
    assert len(chave) == 16
    assert chave[:15] == _CHAVE_PADRAO[:15]
    assert chave[-1] == 0x02  # último byte 0x01 + (2 - 1)


def test_expand_psk_indice_0_rejeitado() -> None:
    with pytest.raises(ValueError):
        crypto.expand_psk(b"\x00")


@pytest.mark.parametrize("tamanho", [0, 2, 5, 15, 17, 24, 64])
def test_expand_psk_tamanho_invalido_levanta(tamanho: int) -> None:
    with pytest.raises(ValueError):
        crypto.expand_psk(bytes(range(tamanho)))


def test_expand_psk_16_e_32_bytes_passam_direto() -> None:
    chave16 = bytes(range(16))
    assert crypto.expand_psk(chave16) == chave16
    assert crypto.expand_psk(TEST_PSK) == TEST_PSK


def test_channel_hash_xor_manual_independente() -> None:
    # Cálculo manual em dois loops separados (nome do canal XOR chave expandida),
    # sem reaproveitar a lógica interna de channel_hash.
    esperado = 0
    for byte in b"EVU":
        esperado ^= byte
    for byte in crypto.expand_psk(TEST_PSK):
        esperado ^= byte
    assert crypto.channel_hash("EVU", TEST_PSK) == esperado
    assert 0 <= esperado <= 0xFF


def test_crypt_nonce_layout_e_keystream_manual() -> None:
    packet_id = 0x0102030405060708
    from_num = 0x0A0B0C0D
    dados = bytes(range(48))

    # Nonce montado byte a byte, independente da implementação:
    # id como uint64 little-endian + from como uint32 LE + 4 bytes zero.
    nonce = (
        packet_id.to_bytes(8, "little")
        + from_num.to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
    )
    assert nonce == bytes(
        [
            0x08, 0x07, 0x06, 0x05, 0x04, 0x03, 0x02, 0x01,  # id u64 LE
            0x0D, 0x0C, 0x0B, 0x0A,  # from u32 LE
            0x00, 0x00, 0x00, 0x00,
        ]
    )

    cifrador = Cipher(algorithms.AES(TEST_PSK), modes.CTR(nonce))
    encriptador = cifrador.encryptor()
    esperado = encriptador.update(dados) + encriptador.finalize()

    assert crypto.crypt(TEST_PSK, packet_id, from_num, dados) == esperado


def test_ctr_e_simetrico_aplicacao_dupla_identidade() -> None:
    packet_id = 0x1122334455667788
    from_num = 0xA0000001
    dados = b"posicao fake 123" * 3
    cifra = crypto.crypt(TEST_PSK, packet_id, from_num, dados)
    assert cifra != dados
    assert crypto.crypt(TEST_PSK, packet_id, from_num, cifra) == dados
