"""Agenda diária da previsão do tempo para os barcos (thread dentro da API).

Uma passada (``rodar_uma_vez``) percorre os barcos na ordem, cada um no seu slot
(``hora:minuto + i * intervalo_s``), e enfileira no máximo UMA mensagem por chamada em
``chat_outbox`` (``created_by='clima'``). A idempotência e o espaçamento valem no banco
(lock advisory + checagens na transação, em ``queries.enfileirar_clima``); o estado em
memória só evita consultas repetidas.

Conexões: leitura e escrita usam conexões separadas do pool, e a busca HTTP acontece
fora de qualquer conexão (psycopg não troca ``read_only`` com transação aberta).

Sensibilidade: a posição do barco é dado sensível. Este módulo NUNCA loga coordenadas,
URL ou texto da mensagem. Exceções que não são ``PrevisaoErro`` saem só com o tipo.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from rastro_api.api import clima_previsao, queries
from rastro_api.api.clima_config import BarcoClima, ConfigClima
from rastro_api.api.clima_previsao import PrevisaoErro, resumir
from rastro_api.api.clima_texto import compor

log = logging.getLogger("rastro_api.clima")

_TENTAR_DEPOIS_ERRO = timedelta(minutes=5)
_MINIMO = datetime.min.replace(tzinfo=timezone.utc)


class AgendaClima:
    """Envia, uma vez por dia e por barco, a previsão do tempo para a posição do barco."""

    def __init__(
        self,
        pool: Any,
        cfg: ConfigClima,
        *,
        buscar: Callable[..., dict] = clima_previsao.buscar,
        agora: Callable[[], datetime] | None = None,
        parar: threading.Event | None = None,
    ) -> None:
        self.pool = pool
        self.cfg = cfg
        self._buscar = buscar
        self._agora = agora if agora is not None else (lambda: datetime.now(timezone.utc))
        self.parar = parar if parar is not None else threading.Event()
        # Só otimização: boat_id -> dia local em que foi pulado; boat_id -> próxima tentativa.
        self._pulados: dict[str, date] = {}
        self._tentar_depois: dict[str, datetime] = {}

    def rodar_uma_vez(self) -> int:
        """Uma passada da agenda. Devolve 1 se enfileirou uma previsão, senão 0."""
        cfg = self.cfg
        tz = timezone(timedelta(hours=cfg.utc_offset_h))
        agora_utc = self._agora()
        local = agora_utc.astimezone(tz)
        hoje = local.date()
        base = local.replace(hour=cfg.hora, minute=cfg.minuto, second=0, microsecond=0)
        desde = local.replace(hour=0, minute=0, second=0, microsecond=0)

        for i, barco in enumerate(cfg.barcos):
            if self.parar.is_set():
                return 0

            slot = base + timedelta(seconds=i * cfg.intervalo_s)
            expira = slot + timedelta(hours=cfg.ttl_h)
            if local < slot or local >= expira:
                continue
            if self._pulados.get(barco.boat_id) == hoje:
                continue
            if self._tentar_depois.get(barco.boat_id, _MINIMO) > agora_utc:
                continue

            try:
                with self.pool.connection() as conn:
                    if queries.clima_ja_enfileirado(conn, barco.boat_id, desde):
                        continue
                    posicao = queries.ultima_posicao_barco(conn, barco.boat_id)
            except Exception as erro:  # noqa: BLE001 - só o tipo, nunca a mensagem
                log.error("clima: erro de banco ao ler %s (%s)", barco.boat_id, type(erro).__name__)
                return 0

            coord = self._coordenada(barco, posicao, agora_utc)
            if coord is None:
                log.warning("clima: %s sem posição recente; pulado hoje", barco.boat_id)
                self._pulados[barco.boat_id] = hoje
                continue
            lat, lon = coord

            try:
                dados = self._buscar(lat, lon, base=cfg.api_url)
                texto = compor(barco.regional, hoje, cfg.rotulo_hora, resumir(dados))
            except PrevisaoErro as erro:
                log.warning("clima: %s falhou: %s", barco.boat_id, str(erro))
                self._tentar_depois[barco.boat_id] = agora_utc + _TENTAR_DEPOIS_ERRO
                continue
            except Exception as erro:  # noqa: BLE001 - str() pode trazer URL com coordenadas
                log.warning("clima: %s falhou: %s", barco.boat_id, type(erro).__name__)
                self._tentar_depois[barco.boat_id] = agora_utc + _TENTAR_DEPOIS_ERRO
                continue

            # Depois da rede: a busca pode ter demorado além do dia ou da validade.
            if self.parar.is_set():
                return 0
            agora_utc = self._agora()
            local = agora_utc.astimezone(tz)
            if local.date() != hoje or local >= expira:
                return 0

            try:
                with self.pool.connection() as conn:
                    novo_id = queries.enfileirar_clima(
                        conn, barco.boat_id, texto, expira, desde, cfg.intervalo_s
                    )
            except Exception as erro:  # noqa: BLE001
                log.error(
                    "clima: erro de banco ao enfileirar %s (%s)", barco.boat_id, type(erro).__name__
                )
                return 0

            if novo_id is None:
                # Já enviado por outra instância ou espaçamento ainda não venceu: tenta na próxima.
                return 0
            log.info(
                "clima: previsão enfileirada para %s (id=%s, %d bytes)",
                barco.boat_id,
                novo_id,
                len(texto.encode("utf-8")),
            )
            return 1
        return 0

    def iniciar(self, passo_s: float = 30.0) -> threading.Thread:
        """Sobe a thread daemon ``rastro-clima`` que roda ``rodar_uma_vez`` a cada ``passo_s``."""

        def laco() -> None:
            while not self.parar.is_set():
                try:
                    self.rodar_uma_vez()
                except Exception as erro:  # noqa: BLE001
                    log.error("clima: erro inesperado na agenda (%s)", type(erro).__name__)
                self.parar.wait(passo_s)

        thread = threading.Thread(target=laco, name="rastro-clima", daemon=True)
        thread.start()
        return thread

    def _coordenada(
        self, barco: BarcoClima, posicao: dict | None, agora_utc: datetime
    ) -> tuple[float, float] | None:
        """Último fix se tiver até ``max_idade_h``; senão a reserva do barco; senão None."""
        if (
            posicao
            and posicao["lat"] is not None
            and posicao["lon"] is not None
            and agora_utc - posicao["pos_time"] <= timedelta(hours=self.cfg.max_idade_h)
        ):
            return posicao["lat"], posicao["lon"]
        return barco.reserva
