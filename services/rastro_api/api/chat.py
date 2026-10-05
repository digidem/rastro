"""Rotas da API de chat e fila de mensagens da malha Rastro (WP-F1).

Regras de segurança e privacidade:
- NUNCA logar texto da mensagem nem coordenadas nem chaves/payloads.
- Rótulos de entrega refletem apenas o gateway do barco ("enviado ao gateway do barco",
  "expirada (não entregue)", "na fila", "falhou") — NUNCA o termo "lido".
- Todas as rotas exigem autenticação ativa.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Annotated, Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from rastro_api.api import geojson, queries

LOGGER = logging.getLogger("rastro_api.chat")

STATUS_LABELS: dict[str, str] = {
    "queued": "na fila",
    "sent": "enviado ao gateway do barco",
    "expired": "expirada (não entregue)",
    "failed": "falhou",
}


class ChatSendRequest(BaseModel):
    """Corpo para envio de mensagem de chat."""
    boat: str | None = None
    boat_id: str | None = None
    text: str = Field(default="")

    @property
    def resolved_boat(self) -> str:
        return (self.boat or self.boat_id or "").strip()


def _session_user_label(request: Request) -> str:
    """Deriva o identificador do autor a partir da sessão autenticada.

    Nunca aceita cabeçalhos fornecidos pelo cliente (como X-User-Label ou X-User).
    Como a autenticação da API opera via token compartilhado ou sessão assinada única
    (sem modelo de usuários individuais em main.py), utiliza o rótulo fixo 'escritorio'.
    """
    user = getattr(request.state, "user", None)
    if user and isinstance(user, str) and user.strip():
        return user.strip()
    return "escritorio"


def _parse_since(since_raw: str | None) -> dt.datetime | None:
    """Valida parâmetro since ISO 8601 UTC; levanta 400 se inválido."""
    if not since_raw:
        return None
    try:
        val = dt.datetime.fromisoformat(since_raw.strip().replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="parâmetro 'since' inválido: use data ISO 8601",
        ) from None
    if val.tzinfo is None:
        val = val.replace(tzinfo=dt.timezone.utc)
    return val.astimezone(dt.timezone.utc)


def create_router(fetch_fn: Callable[..., Any] | None = None) -> APIRouter:
    """Cria roteador FastAPI com prefixo /chat para ser montado em /api."""
    router = APIRouter(prefix="/chat")

    def _do_fetch(request: Request, fn: Any, *args: Any) -> Any:
        if fetch_fn is not None:
            return fetch_fn(request, fn, *args)
        # fallback caso não injetado: busca pool direto em app.state
        pool = request.app.state.pool
        with pool.connection() as conn:
            return fn(conn, *args)

    @router.get("/messages")
    def list_messages(
        request: Request,
        since: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1)] = 200,
        before_id: Annotated[int | None, Query(ge=1)] = None,
    ) -> list[dict]:
        """Lista mensagens de chat em ordem cronológica (newest-last)."""
        since_dt = _parse_since(since)
        clamped_limit = min(limit, 500)
        rows = _do_fetch(
            request, queries.chat_messages, since_dt, clamped_limit, before_id
        )
        LOGGER.info("chat messages listadas: count=%d", len(rows))
        return [
            {
                "id": r["id"],
                "direction": r["direction"],
                "boat_id": r["boat_id"],
                "from_num": r["from_num"],
                "text": r["text"],
                "is_alert": bool(r.get("is_alert", False)),
                "observed_at": geojson.iso_utc(r["observed_at"]) if r.get("observed_at") else None,
                "received_at": geojson.iso_utc(r["received_at"]) if r.get("received_at") else None,
                "outbox_id": r.get("outbox_id"),
            }
            for r in rows
        ]

    @router.get("/boats")
    def list_boats(request: Request) -> list[dict]:
        """Lista barcos com gateways virtuais ativos."""
        rows = _do_fetch(request, queries.chat_boats)
        LOGGER.info("chat boats listados: count=%d", len(rows))
        return [
            {
                "boat_id": r["boat_id"],
                "boat": r["boat_id"],
                "gateway_id": r["gateway_id"],
                "virtual_node_num": r["virtual_node_num"],
                "active": bool(r.get("active", True)),
            }
            for r in rows
        ]

    @router.post("/send", status_code=200)
    def send_message(request: Request, body: ChatSendRequest) -> dict:
        """Enfileira mensagem no chat_outbox com TTL configurado."""
        boat_id = body.resolved_boat
        if not boat_id:
            raise HTTPException(status_code=404, detail="barco não informado ou inexistente")

        # Validação estrita de bytes UTF-8 (1..200 bytes)
        text_bytes = body.text.encode("utf-8")
        if len(text_bytes) < 1 or len(text_bytes) > 200:
            raise HTTPException(
                status_code=422,
                detail="o texto da mensagem deve conter entre 1 e 200 bytes UTF-8",
            )

        # Barco deve existir e estar ativo em virtual_gateways
        active_boat = _do_fetch(request, queries.get_active_boat, boat_id)
        if not active_boat:
            raise HTTPException(
                status_code=404,
                detail="barco não encontrado ou inativo na malha",
            )

        # TTL de expiração configurável via RASTRO_CHAT_TTL_SECS (default 900 s)
        try:
            ttl_secs = int(os.environ.get("RASTRO_CHAT_TTL_SECS", "900"))
            if ttl_secs <= 0:
                ttl_secs = 900
        except ValueError:
            ttl_secs = 900

        now = dt.datetime.now(dt.timezone.utc)
        expires_at = now + dt.timedelta(seconds=ttl_secs)
        created_by = _session_user_label(request)

        # Inserção no chat_outbox (status 'queued')
        row = _do_fetch(
            request,
            queries.insert_outbox_message,
            boat_id,
            body.text,
            created_by,
            expires_at,
        )

        LOGGER.info("chat outbox enfileirado: id=%s boat_id=%s", row["id"], boat_id)
        return {
            "id": row["id"],
            "status": "queued",
            "expires_at": geojson.iso_utc(row["expires_at"]),
        }

    @router.get("/outbox/{outbox_id}")
    def get_outbox_status(request: Request, outbox_id: int) -> dict:
        """Consulta status da mensagem na fila com rótulo em português — NUNCA 'lido'."""
        row = _do_fetch(request, queries.get_outbox_message, outbox_id)
        if not row:
            raise HTTPException(
                status_code=404,
                detail="mensagem não encontrada na fila",
            )

        status = row["status"]
        label = STATUS_LABELS.get(status, status)

        return {
            "id": row["id"],
            "boat_id": row["boat_id"],
            "status": status,
            "label": label,
            "status_label": label,
            "created_by": row.get("created_by"),
            "created_at": geojson.iso_utc(row["created_at"]) if row.get("created_at") else None,
            "expires_at": geojson.iso_utc(row["expires_at"]) if row.get("expires_at") else None,
            "sent_at": geojson.iso_utc(row["sent_at"]) if row.get("sent_at") else None,
            "error": row.get("error"),
        }

    return router


router = create_router()
