"""Seed idempotente de ``virtual_gateways``/``boat_devices`` (H1).

O processo de chat roda com o papel ``<db>_ingest``; as duas tabelas são de
CONFIGURAÇÃO (sem coordenadas) — os GRANTs de INSERT/UPDATE foram adicionados
aos três caminhos de deploy (02-native.sql, migrate-02-native.sh e
bootstrap-existing.sh) exatamente iguais.
"""
from __future__ import annotations

from typing import Any, Mapping

from rastro_gateway.native import derive


def seed_from_env(pool: Any, env: Mapping[str, str]) -> tuple[int, int]:
    """Semeia config do ingest nativo a partir de ``RASTRO_NATIVE_NODES``.

    ``!a0000001=b1,!a0000002=b2`` → para cada barco distinto (ordem de
    aparição) faz upsert de ``virtual_gateways`` e, para cada par nó→barco,
    abre vínculo em ``boat_devices`` só quando não existe linha aberta
    (``valid_to IS NULL``) para o mesmo (node_num, boat_id).

    Uma única transação. NUNCA apaga nem desativa linhas fora do env.
    Retorna ``(barcos, nós)``; no-op (0, 0) sem a variável.
    """
    pares = derive.parse_nodes(env.get("RASTRO_NATIVE_NODES", "") or "")
    if not pares:
        return (0, 0)

    barcos = list(dict.fromkeys(barco for _, barco in pares))
    with pool.connection() as conn:
        with conn.transaction():
            for barco in barcos:
                num, gw_id = derive.vgw_for_boat(barco)
                conn.execute(
                    """
                    INSERT INTO virtual_gateways (boat_id, gateway_id, virtual_node_num, active)
                    VALUES (%s, %s, %s, true)
                    ON CONFLICT (boat_id) DO UPDATE
                    SET gateway_id = EXCLUDED.gateway_id,
                        virtual_node_num = EXCLUDED.virtual_node_num,
                        active = true
                    """,
                    (barco, gw_id, num),
                )
            for usuario, barco in pares:
                num_no = int(usuario[1:], 16)
                conn.execute(
                    """
                    INSERT INTO boat_devices (node_num, boat_id)
                    SELECT %s, %s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM boat_devices
                        WHERE node_num = %s AND boat_id = %s AND valid_to IS NULL
                    )
                    """,
                    (num_no, barco, num_no, barco),
                )
    return (len(barcos), len(pares))
