"""Vetores dourados da derivação determinística compartilhada (H1).

Os vetores foram computados UMA vez com a implementação de referência e
congelados aqui. Eles trancam o lado Python e o template CapRover (bash) na
mesma derivação: se um dos dois lados mudar sozinho, estes testes quebram.
"""
from __future__ import annotations

import pytest

from rastro_gateway.native.derive import node_password, parse_nodes, vgw_for_boat

# --- Vetores dourados: vgw_for_boat(boat) -> (node_num, gateway_id) ---
VGW_VETORES = [
    ("b1", (3825633914, "!e4068a7a")),
    ("b2", (4264915960, "!fe3573f8")),
    ("univaja-3", (4093112930, "!f3f7f262")),
]

# --- Vetores dourados: node_password(secret, user) -> senha (NUNCA logar em produção) ---
PW_VETORES = [
    ("teste-secreto-h1", "!a0000001", "eIugm-YbMQVV6CCvwqSrIkRH4rJlVq2y"),
    ("teste-secreto-h1", "!a0000002", "SgArIutL4UOvk5TWmbDhdexP-M0RuiMj"),
]


@pytest.mark.parametrize("barco,esperado", VGW_VETORES)
def test_vgw_for_boat_vetores_dourados(barco: str, esperado: tuple[int, str]) -> None:
    assert vgw_for_boat(barco) == esperado


@pytest.mark.parametrize("segredo,usuario,esperado", PW_VETORES)
def test_node_password_vetores_dourados(segredo: str, usuario: str, esperado: str) -> None:
    assert node_password(segredo, usuario) == esperado


def test_vgw_range_e_paridade():
    """MSB da faixa de gateway (0xE0000000) sempre setado; LSB (paridade) sempre zerado."""
    for barco in ("b1", "b2", "univaja-3", "x", "zz-barco-42"):
        num, gw_id = vgw_for_boat(barco)
        assert num & 0xE0000000 == 0xE0000000
        assert num & 1 == 0
        assert gw_id == "!%08x" % num


def test_vgw_deterministico_e_distinto():
    assert vgw_for_boat("b1") == vgw_for_boat("b1")
    assert vgw_for_boat("b1") != vgw_for_boat("b2")


def test_parse_nodes_formato_basico():
    pares = parse_nodes("!a0000001=b1,!a0000002=b2")
    assert pares == [("!a0000001", "b1"), ("!a0000002", "b2")]


def test_parse_nodes_ordem_e_duplicados():
    pares = parse_nodes("!a0000002=b2,!a0000001=b1,!a0000003=b1")
    assert [b for _, b in pares] == ["b2", "b1", "b1"]
    # barcos distintos preservam ordem de aparição
    assert list(dict.fromkeys(b for _, b in pares)) == ["b2", "b1"]


def test_parse_nodes_tolerar_vazios():
    assert parse_nodes("") == []
    assert parse_nodes(None) == []  # type: ignore[arg-type]
    assert parse_nodes(" , , ") == []


def test_parse_nodes_invalido():
    for valor in (
        "b1=!a0000001",          # invertido: usuário não casa com '!' + hex
        "!a0000001",             # sem '='
        "!A0000001=b1",          # hex maiúsculo
        "!a000001=b1",           # 7 hex
        "no=a=b",                # partition pega o primeiro '='; usuário 'no' inválido
        "!xyzw=b1",              # curto demais
    ):
        with pytest.raises(ValueError):
            parse_nodes(valor)
