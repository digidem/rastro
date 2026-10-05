"""Fila de saída (outbox) para envio de mensagens do chat via MQTT nativo.

Ver docs/native-ingest-design.md (§1 Protocolo, WP-E).
Cria envelopes ServiceEnvelope criptografados com AES-CTR, publica no tópico
do gateway virtual do barco com QoS 1 e retain=False, e confirma o envio
apenas após o recebimento do PUBACK do broker.
NUNCA logar texto de mensagem, chaves/PSK ou coordenadas geográficas.
"""
from __future__ import annotations

import collections
import datetime
import inspect
import logging
import os
import random
import time
from typing import Any, Callable

from meshtastic.protobuf import mesh_pb2, mqtt_pb2
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from rastro_gateway.native import crypto

log = logging.getLogger("rastro.chat.outbox")

_PORTNUM_TEXT = 1  # TEXT_MESSAGE_APP
_PORTNUM_NODEINFO = 4  # NODEINFO_APP
_BROADCAST_NUM = 0xFFFFFFFF
_DEFAULT_CHANNEL = "EVU"
_DEFAULT_ROOT = "univaja/mesh"
_DEFAULT_RATE_PER_MIN = 6
_DEFAULT_LONG_NAME = "Rastro"
_DEFAULT_SHORT_NAME = "RSTR"


class OutboxResult(int):
    """Resultado da execução de run_once().

    Comporta-se como int (para `assert run_once() == n`) e expõe
    atributos para detalhamento de métricas.
    """

    def __new__(
        cls,
        sent: int = 0,
        failed: int = 0,
        rate_limited: int = 0,
        claimed: int = 0,
    ) -> "OutboxResult":
        obj = super().__new__(cls, sent)
        obj.sent = sent
        obj.failed = failed
        obj.rate_limited = rate_limited
        obj.claimed = claimed
        return obj

    def __getitem__(self, item: str) -> int:
        return getattr(self, item)


def build_text_packet(
    text: str,
    from_num: int | None = None,
    packet_id: int | None = None,
    psk: bytes | None = None,
    *,
    to_num: int = _BROADCAST_NUM,
    channel_name: str = _DEFAULT_CHANNEL,
    hop_limit: int = 3,
    want_ack: bool = False,
    **kwargs: Any,
) -> mesh_pb2.MeshPacket:
    """Monta o MeshPacket cifrado contendo mensagem de texto (portnum=1)."""
    if from_num is None:
        from_num = kwargs.get("from_node", kwargs.get("from_num"))
    if packet_id is None:
        packet_id = kwargs.get("id", kwargs.get("packet_id"))
    if psk is None:
        psk = kwargs.get("key", kwargs.get("psk"))

    if from_num is None or packet_id is None or psk is None:
        raise ValueError("from_num, packet_id e psk são parâmetros obrigatórios")

    data = mesh_pb2.Data(portnum=_PORTNUM_TEXT, payload=text.encode("utf-8"))
    chave = crypto.expand_psk(psk)
    cifrado = crypto.crypt(chave, packet_id, from_num, data.SerializeToString())

    mp = mesh_pb2.MeshPacket()
    setattr(mp, "from", from_num)
    mp.to = to_num
    mp.id = packet_id
    mp.channel = crypto.channel_hash(channel_name, psk)
    mp.hop_limit = hop_limit
    mp.hop_start = hop_limit
    mp.want_ack = want_ack
    mp.encrypted = cifrado
    return mp


def build_text_envelope(
    text: str,
    from_num: int | None = None,
    packet_id: int | None = None,
    psk: bytes | None = None,
    *,
    gateway_id: str | None = None,
    to_num: int = _BROADCAST_NUM,
    channel_name: str = _DEFAULT_CHANNEL,
    hop_limit: int = 3,
    want_ack: bool = False,
    **kwargs: Any,
) -> bytes:
    """Monta e serializa o ServiceEnvelope com MeshPacket de texto cifrado."""
    if from_num is None:
        from_num = kwargs.get("from_node", kwargs.get("from_num"))
    if packet_id is None:
        packet_id = kwargs.get("id", kwargs.get("packet_id"))
    if psk is None:
        psk = kwargs.get("key", kwargs.get("psk"))

    if from_num is None or packet_id is None or psk is None:
        raise ValueError("from_num, packet_id e psk são parâmetros obrigatórios")

    gw_id = gateway_id or kwargs.get("gateway_id") or f"!{from_num:08x}"
    packet = build_text_packet(
        text,
        from_num=from_num,
        packet_id=packet_id,
        psk=psk,
        to_num=to_num,
        channel_name=channel_name,
        hop_limit=hop_limit,
        want_ack=want_ack,
    )
    env = mqtt_pb2.ServiceEnvelope(
        packet=packet,
        channel_id=channel_name,
        gateway_id=gw_id,
    )
    return env.SerializeToString()


def build_nodeinfo_packet(
    from_num: int | None = None,
    packet_id: int | None = None,
    psk: bytes | None = None,
    *,
    gateway_id: str | None = None,
    to_num: int = _BROADCAST_NUM,
    channel_name: str = _DEFAULT_CHANNEL,
    long_name: str = _DEFAULT_LONG_NAME,
    short_name: str = _DEFAULT_SHORT_NAME,
    hw_model: int = mesh_pb2.HardwareModel.PRIVATE_HW,
    hop_limit: int = 3,
    want_ack: bool = False,
    **kwargs: Any,
) -> mesh_pb2.MeshPacket:
    """Monta o MeshPacket cifrado contendo NodeInfo (portnum=4, User)."""
    if from_num is None:
        from_num = kwargs.get("from_node", kwargs.get("from_num"))
    if packet_id is None:
        packet_id = kwargs.get("id", kwargs.get("packet_id"))
    if psk is None:
        psk = kwargs.get("key", kwargs.get("psk"))

    if from_num is None or packet_id is None or psk is None:
        raise ValueError("from_num, packet_id e psk são parâmetros obrigatórios")

    user_id = f"!{from_num:08x}"
    user = mesh_pb2.User(
        id=user_id,
        long_name=long_name,
        short_name=short_name,
        hw_model=hw_model,
    )
    data = mesh_pb2.Data(portnum=_PORTNUM_NODEINFO, payload=user.SerializeToString())
    chave = crypto.expand_psk(psk)
    cifrado = crypto.crypt(chave, packet_id, from_num, data.SerializeToString())

    mp = mesh_pb2.MeshPacket()
    setattr(mp, "from", from_num)
    mp.to = to_num
    mp.id = packet_id
    mp.channel = crypto.channel_hash(channel_name, psk)
    mp.hop_limit = hop_limit
    mp.hop_start = hop_limit
    mp.want_ack = want_ack
    mp.encrypted = cifrado
    return mp


def build_nodeinfo_envelope(
    from_num: int | None = None,
    packet_id: int | None = None,
    psk: bytes | None = None,
    *,
    gateway_id: str | None = None,
    to_num: int = _BROADCAST_NUM,
    channel_name: str = _DEFAULT_CHANNEL,
    long_name: str = _DEFAULT_LONG_NAME,
    short_name: str = _DEFAULT_SHORT_NAME,
    hw_model: int = mesh_pb2.HardwareModel.PRIVATE_HW,
    hop_limit: int = 3,
    want_ack: bool = False,
    **kwargs: Any,
) -> bytes:
    """Monta e serializa o ServiceEnvelope com MeshPacket de NodeInfo cifrado."""
    if from_num is None:
        from_num = kwargs.get("from_node", kwargs.get("from_num"))
    if packet_id is None:
        packet_id = kwargs.get("id", kwargs.get("packet_id"))
    if psk is None:
        psk = kwargs.get("key", kwargs.get("psk"))

    if from_num is None or packet_id is None or psk is None:
        raise ValueError("from_num, packet_id e psk são parâmetros obrigatórios")

    gw_id = gateway_id or kwargs.get("gateway_id") or f"!{from_num:08x}"
    packet = build_nodeinfo_packet(
        from_num=from_num,
        packet_id=packet_id,
        psk=psk,
        gateway_id=gw_id,
        to_num=to_num,
        channel_name=channel_name,
        long_name=long_name,
        short_name=short_name,
        hw_model=hw_model,
        hop_limit=hop_limit,
        want_ack=want_ack,
    )
    env = mqtt_pb2.ServiceEnvelope(
        packet=packet,
        channel_id=channel_name,
        gateway_id=gw_id,
    )
    return env.SerializeToString()


class Outbox:
    """Processador de fila de saída para envio de mensagens via MQTT nativo."""

    def __init__(
        self,
        db: Any,
        publish_fn: Callable[..., bool],
        psk: bytes,
        root: str = _DEFAULT_ROOT,
        clock: Callable[[], float | datetime.datetime] | None = None,
        rng: random.Random | None = None,
        *,
        client: Any = None,
        is_connected: Callable[[], bool] | None = None,
        id_in_use: Callable[[int, int], bool] | None = None,
        record_outgoing: Callable[..., Any] | None = None,
        get_virtual_gateway: Callable[[str], dict[str, Any] | None] | None = None,
        get_all_virtual_gateways: Callable[[], list[dict[str, Any]]] | None = None,
        rate_limit_per_min: int | None = None,
        channel_name: str = _DEFAULT_CHANNEL,
    ) -> None:
        self.db = db
        self.publish_fn = publish_fn
        self.psk = psk
        self.root = root
        self.clock = clock or time.time
        self.rng = rng or random.Random()
        self.client = client
        self.is_connected_fn = is_connected
        self.id_in_use = id_in_use
        self.record_outgoing = record_outgoing
        self.get_virtual_gateway = get_virtual_gateway
        self.get_all_virtual_gateways = get_all_virtual_gateways
        self.channel_name = channel_name

        if rate_limit_per_min is not None:
            self.rate_limit_per_min = int(rate_limit_per_min)
        else:
            env_val = os.environ.get("RASTRO_OUTBOX_RATE_PER_MIN", str(_DEFAULT_RATE_PER_MIN)).strip()
            try:
                self.rate_limit_per_min = int(env_val)
            except ValueError:
                self.rate_limit_per_min = _DEFAULT_RATE_PER_MIN

        # Controle de unicidade e recência de packet_id
        self._recent_ids: set[int] = set()
        self._recent_pairs: set[tuple[int, int]] = set()
        self._recent_queue: collections.deque[tuple[int, int]] = collections.deque(maxlen=100_000)

        # Rastreamento de nós virtuais ativos para detecção a cada loop
        self._known_active_node_nums: set[int] = set()

        # Rate limiting por barco: timestamps de envios confirmados nos últimos 60 s
        self._boat_sent_timestamps: dict[str, list[float]] = collections.defaultdict(list)

    def _is_connected(self) -> bool:
        """Verifica se o cliente MQTT está conectado antes da publicação."""
        if self.is_connected_fn is not None:
            try:
                return bool(self.is_connected_fn())
            except Exception:
                return False
        if self.client is not None and hasattr(self.client, "is_connected"):
            try:
                return bool(self.client.is_connected())
            except Exception:
                return False
        if hasattr(self.publish_fn, "__self__"):
            obj = getattr(self.publish_fn, "__self__")
            if hasattr(obj, "is_connected"):
                try:
                    return bool(obj.is_connected())
                except Exception:
                    return False
            if hasattr(obj, "client") and hasattr(obj.client, "is_connected"):
                try:
                    return bool(obj.client.is_connected())
                except Exception:
                    return False
        return True

    @staticmethod
    def _extract_vgw_info(vgw: Any) -> tuple[int | None, str | None, bool]:
        """Extrai (virtual_node_num, gateway_id, active) de objeto ou dict de gateway."""
        if isinstance(vgw, dict):
            return vgw.get("virtual_node_num"), vgw.get("gateway_id"), bool(vgw.get("active", True))
        return (
            getattr(vgw, "virtual_node_num", None),
            getattr(vgw, "gateway_id", None),
            bool(getattr(vgw, "active", True)),
        )

    def _get_now_s(self) -> float:
        """Obtém o instante atual em segundos epoch."""
        t = self.clock()
        if isinstance(t, (int, float)):
            return float(t)
        if isinstance(t, datetime.datetime):
            return t.timestamp()
        return time.time()

    def _get_now_dt(self) -> datetime.datetime:
        """Obtém o instante atual como datetime com fuso UTC."""
        t = self.clock()
        if isinstance(t, datetime.datetime):
            return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)
        if isinstance(t, (int, float)):
            return datetime.datetime.fromtimestamp(float(t), tz=datetime.timezone.utc)
        return datetime.datetime.now(tz=datetime.timezone.utc)

    @staticmethod
    def _exp_to_seconds(exp: Any) -> float | None:
        """Converte expiração (float, int, datetime ou str ISO) para epoch em segundos."""
        if exp is None:
            return None
        if isinstance(exp, (int, float)):
            return float(exp)
        if isinstance(exp, datetime.datetime):
            dt = exp if exp.tzinfo else exp.replace(tzinfo=datetime.timezone.utc)
            return dt.timestamp()
        if isinstance(exp, str):
            try:
                dt = datetime.datetime.fromisoformat(exp)
                dt = dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
                return dt.timestamp()
            except Exception:
                return None
        return None

    @classmethod
    def _is_expired(cls, exp: Any, now_s: float, now_dt: datetime.datetime) -> bool:
        """Verifica se o timestamp de expiração já foi atingido."""
        if exp is None:
            return False
        if isinstance(exp, (int, float)):
            return exp <= now_s
        if isinstance(exp, datetime.datetime):
            exp_cmp = exp if exp.tzinfo else exp.replace(tzinfo=datetime.timezone.utc)
            return exp_cmp <= now_dt
        if isinstance(exp, str):
            try:
                dt = datetime.datetime.fromisoformat(exp)
                exp_cmp = dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
                return exp_cmp <= now_dt
            except Exception:
                return False
        return False

    def _lookup_vgw(self, boat_id: str) -> dict[str, Any] | None:
        """Localiza o gateway virtual cadastrado para o barco."""
        if self.get_virtual_gateway is not None:
            return self.get_virtual_gateway(boat_id)
        if hasattr(self.db, "get_virtual_gateway"):
            return self.db.get_virtual_gateway(boat_id)
        if hasattr(self.db, "virtual_gateways") and isinstance(self.db.virtual_gateways, dict):
            return self.db.virtual_gateways.get(boat_id)
        if hasattr(self.db, "_pool"):
            try:
                with self.db._pool.connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT gateway_id, virtual_node_num, active
                            FROM virtual_gateways
                            WHERE boat_id = %s
                            """,
                            (boat_id,),
                        )
                        row = cur.fetchone()
                        if row:
                            return {
                                "gateway_id": row[0],
                                "virtual_node_num": row[1],
                                "active": bool(row[2]),
                            }
            except Exception as exc:
                log.warning("Erro ao consultar virtual_gateways no banco: %s", type(exc).__name__)
        return None

    def _lookup_all_vgws(self) -> list[dict[str, Any]]:
        """Recupera todos os gateways virtuais ativos."""
        if self.get_all_virtual_gateways is not None:
            return self.get_all_virtual_gateways()
        if hasattr(self.db, "get_all_virtual_gateways"):
            return self.db.get_all_virtual_gateways()
        if hasattr(self.db, "virtual_gateways") and isinstance(self.db.virtual_gateways, dict):
            return list(self.db.virtual_gateways.values())
        if hasattr(self.db, "_pool"):
            try:
                with self.db._pool.connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT gateway_id, virtual_node_num, active
                            FROM virtual_gateways
                            WHERE active = true
                            """
                        )
                        return [
                            {"gateway_id": r[0], "virtual_node_num": r[1], "active": bool(r[2])}
                            for r in cur.fetchall()
                        ]
            except Exception as exc:
                log.warning("Erro ao consultar todos os virtual_gateways: %s", type(exc).__name__)
        return []

    def _is_id_in_use(self, from_num: int, packet_id: int) -> bool:
        """Verifica se o par (from_num, packet_id) já foi registrado no banco."""
        if self.id_in_use is not None:
            try:
                return bool(self.id_in_use(from_num, packet_id))
            except TypeError:
                return bool(self.id_in_use(from_num=from_num, id=packet_id))
        if hasattr(self.db, "id_in_use"):
            return bool(self.db.id_in_use(from_num, packet_id))
        if hasattr(self.db, "_pool"):
            try:
                with self.db._pool.connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT 1 FROM chat_messages WHERE from_num = %s AND packet_id = %s",
                            (from_num, packet_id),
                        )
                        return cur.fetchone() is not None
            except Exception:
                return False
        return False

    def _generate_packet_id(self, from_num: int) -> int:
        """Gera identificador de 32 bits aleatório nunca reutilizado."""
        while True:
            cand = self.rng.randint(1, 0xFFFFFFFF)
            if cand in self._recent_ids or (from_num, cand) in self._recent_pairs:
                continue
            if self._is_id_in_use(from_num, cand):
                continue

            if len(self._recent_queue) == self._recent_queue.maxlen:
                old_from, old_id = self._recent_queue.popleft()
                self._recent_ids.discard(old_id)
                self._recent_pairs.discard((old_from, old_id))

            self._recent_ids.add(cand)
            self._recent_pairs.add((from_num, cand))
            self._recent_queue.append((from_num, cand))
            return cand

    def _check_rate_limit(self, boat_id: str, now: float) -> bool:
        """Verifica se o barco ainda possui cota de envio na janela de 60 segundos."""
        if self.rate_limit_per_min <= 0:
            return True
        janela = now - 60.0
        filtrados = [ts for ts in self._boat_sent_timestamps[boat_id] if ts > janela]
        self._boat_sent_timestamps[boat_id] = filtrados
        return len(filtrados) < self.rate_limit_per_min

    def _record_rate_limit(self, boat_id: str, now: float) -> None:
        """Registra o timestamp do envio confirmado para o cálculo do rate limit."""
        self._boat_sent_timestamps[boat_id].append(now)

    def _record_outgoing_message(
        self,
        boat_id: str,
        from_num: int,
        packet_id: int,
        text: str,
    ) -> bool:
        """Registra a mensagem saindo na tabela chat_messages com direction='out'.

        Retorna True em caso de sucesso, False se falhar.
        """
        if self.record_outgoing is not None:
            try:
                res = self.record_outgoing(
                    boat_id=boat_id,
                    from_num=from_num,
                    packet_id=packet_id,
                    text=text,
                )
            except TypeError:
                res = self.record_outgoing(boat_id, from_num, packet_id, text)
            if res is False:
                return False
            return True

        if hasattr(self.db, "record_outgoing_message"):
            try:
                res = self.db.record_outgoing_message(boat_id, from_num, packet_id, text)
                if res is False:
                    return False
                return True
            except Exception as exc:
                log.error("Falha ao registrar mensagem via db.record_outgoing_message: %s", type(exc).__name__)
                return False

        if hasattr(self.db, "_pool"):
            try:
                with self.db._pool.connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO chat_messages (
                                direction, boat_id, from_num, packet_id, text, is_alert, observed_at, received_at
                            ) VALUES (
                                'out', %s, %s, %s, %s, %s, now(), now()
                            )
                            ON CONFLICT (from_num, packet_id) DO NOTHING
                            """,
                            (boat_id, from_num, packet_id, text, "\x07" in text),
                        )
                return True
            except Exception as exc:
                log.error("Falha ao registrar mensagem saindo em chat_messages: %s", type(exc).__name__)
                return False

        return True

    def publish_nodeinfo(
        self,
        virtual_node_num: int,
        gateway_id: str | None = None,
        packet_id: int | None = None,
        *,
        long_name: str = _DEFAULT_LONG_NAME,
        short_name: str = _DEFAULT_SHORT_NAME,
        hw_model: int = mesh_pb2.HardwareModel.PRIVATE_HW,
    ) -> bool:
        """Publica NodeInfo para um nó virtual."""
        gw_id = gateway_id or f"!{virtual_node_num:08x}"
        if packet_id is None:
            packet_id = self._generate_packet_id(virtual_node_num)
        payload = build_nodeinfo_envelope(
            from_num=virtual_node_num,
            packet_id=packet_id,
            psk=self.psk,
            gateway_id=gw_id,
            channel_name=self.channel_name,
            long_name=long_name,
            short_name=short_name,
            hw_model=hw_model,
        )
        topic = f"{self.root.strip('/')}/2/e/{self.channel_name}/{gw_id}"
        try:
            confirmed = self.publish_fn(topic, payload, qos=1, retain=False)
        except TypeError:
            confirmed = self.publish_fn(topic, payload)
        except Exception as exc:
            log.warning("Erro ao publicar NodeInfo: topic=%s (erro=%s)", topic, type(exc).__name__)
            return False

        if confirmed:
            log.info("NodeInfo publicado com sucesso: topic=%s gw_id=%s node_num=%d", topic, gw_id, virtual_node_num)
        return bool(confirmed)

    def publish_all_nodeinfos(self) -> int:
        """Publica NodeInfo para todos os gateways virtuais ativos."""
        vgws = self._lookup_all_vgws()
        count = 0
        current_active: set[int] = set()
        for vgw in vgws:
            node_num, gw_id, active = self._extract_vgw_info(vgw)
            if not active or node_num is None:
                continue
            current_active.add(node_num)
            if self.publish_nodeinfo(node_num, gateway_id=gw_id):
                count += 1
                self._known_active_node_nums.add(node_num)
        self._known_active_node_nums = {n for n in self._known_active_node_nums if n in current_active}
        return count

    def publish_newly_active_nodeinfos(self) -> int:
        """Detecta gateways virtuais recém-ativados e publica NodeInfo para eles."""
        vgws = self._lookup_all_vgws()
        count = 0
        current_active: set[int] = set()
        for vgw in vgws:
            node_num, gw_id, active = self._extract_vgw_info(vgw)
            if not active or node_num is None:
                continue
            current_active.add(node_num)
            if node_num not in self._known_active_node_nums:
                if self.publish_nodeinfo(node_num, gateway_id=gw_id):
                    count += 1
                    self._known_active_node_nums.add(node_num)
                    log.info(
                        "NodeInfo publicado para novo gateway virtual ativo: gw_id=%s node_num=%d",
                        gw_id,
                        node_num,
                    )
        self._known_active_node_nums = {n for n in self._known_active_node_nums if n in current_active}
        return count

    def run_once(self, limit: int = 10) -> OutboxResult:
        """Executa um ciclo da fila de saída (claim, validação, publicação e marcação)."""
        now_s = self._get_now_s()
        now_dt = self._get_now_dt()

        # Verifica se claim_outbox aceita per_boat_limit
        accepts_per_boat = False
        try:
            sig = inspect.signature(self.db.claim_outbox)
            accepts_per_boat = "per_boat_limit" in sig.parameters
        except (ValueError, TypeError):
            accepts_per_boat = False

        kwargs: dict[str, Any] = {"limit": limit}
        if accepts_per_boat:
            kwargs["per_boat_limit"] = 3

        rows: list[dict[str, Any]] = []
        try:
            rows = self.db.claim_outbox(**kwargs, now=now_dt)
        except TypeError:
            try:
                rows = self.db.claim_outbox(**kwargs)
            except TypeError:
                try:
                    rows = self.db.claim_outbox(limit=limit, now=now_dt)
                except TypeError:
                    rows = self.db.claim_outbox(limit=limit)
        except Exception as exc:
            log.error("Erro ao chamar claim_outbox: %s", type(exc).__name__)
            return OutboxResult(sent=0, failed=0, rate_limited=0, claimed=0)

        claimed_count = len(rows)
        sent_count = 0
        failed_count = 0
        rate_limited_count = 0

        for row in rows:
            row_id = row["id"]
            boat_id = row["boat_id"]
            text = row["text"]

            # Atualiza o relógio a cada linha para evitar timestamps defasados entre linhas
            now_s = self._get_now_s()
            now_dt = self._get_now_dt()

            # 1. Validação de TTL (linhas expiradas não devem ser enviadas)
            exp = row.get("expires_at")
            if self._is_expired(exp, now_s, now_dt):
                self.db.mark_outbox(row_id, "expired")
                log.info("Mensagem expirada por TTL: outbox_id=%d boat=%s", row_id, boat_id)
                continue

            # 2. Localização do Gateway Virtual
            vgw = self._lookup_vgw(boat_id)
            if not vgw:
                self.db.mark_outbox(row_id, "failed", error="sem gateway virtual")
                failed_count += 1
                log.warning("Barco sem gateway virtual: outbox_id=%d boat=%s", row_id, boat_id)
                continue

            node_num, gw_id, is_active = self._extract_vgw_info(vgw)
            gw_id = gw_id or (f"!{node_num:08x}" if node_num is not None else None)

            if not is_active or not gw_id or node_num is None:
                self.db.mark_outbox(row_id, "failed", error="sem gateway virtual")
                failed_count += 1
                log.warning("Barco sem gateway virtual ativo: outbox_id=%d boat=%s", row_id, boat_id)
                continue

            # 3. Limite de taxa (Rate Limit) por barco
            if not self._check_rate_limit(boat_id, now_s):
                rate_limited_count += 1
                log.debug("Taxa limite excedida para boat=%s outbox_id=%d; mantém enfileirado", boat_id, row_id)
                continue

            # 4. Checagem de conexão do cliente MQTT antes de publicar
            if not self._is_connected():
                log.warning("Cliente MQTT desconectado; mantendo mensagem na fila: outbox_id=%d boat=%s", row_id, boat_id)
                continue

            # 5. Obtenção ou persistência do packet_id ANTES do envio (reutiliza em retentativas)
            packet_id = row.get("packet_id")
            if packet_id is None:
                packet_id = self._generate_packet_id(node_num)
                try:
                    ok = self.db.mark_outbox(row_id, "queued", packet_id=packet_id)
                    if ok is False:
                        log.error("Falha ao persistir packet_id=%d para outbox_id=%d; abortando envio", packet_id, row_id)
                        continue
                    row["packet_id"] = packet_id
                except Exception as exc:
                    log.error("Exceção ao persistir packet_id para outbox_id=%d: %s", row_id, type(exc).__name__)
                    continue

            # 6. Gravação prévia da mensagem de saída em chat_messages (direction='out')
            # Falha na gravação DEVE abortar a tentativa (mantém na fila), não apenas emitir aviso
            try:
                recorded = self._record_outgoing_message(boat_id, node_num, packet_id, text)
            except Exception as exc:
                log.error("Exceção ao registrar mensagem em chat_messages: %s", type(exc).__name__)
                recorded = False

            if not recorded:
                log.error(
                    "Falha ao registrar mensagem em chat_messages para outbox_id=%d boat=%s packet_id=%d; abortando tentativa",
                    row_id,
                    boat_id,
                    packet_id,
                )
                continue

            # 7. Construção do envelope e tópico
            payload = build_text_envelope(
                text=text,
                from_num=node_num,
                packet_id=packet_id,
                psk=self.psk,
                gateway_id=gw_id,
                channel_name=self.channel_name,
            )
            topic = f"{self.root.strip('/')}/2/e/{self.channel_name}/{gw_id}"

            # 8. Re-checagem de conectividade antes do publish
            if not self._is_connected():
                log.warning("Cliente MQTT desconectado antes do envio; mantendo mensagem na fila: outbox_id=%d boat=%s", row_id, boat_id)
                continue

            # Atualiza o relógio imediatamente antes da publicação e re-checa expiração (Claim 2)
            now_s = self._get_now_s()
            now_dt = self._get_now_dt()
            if self._is_expired(exp, now_s, now_dt):
                self.db.mark_outbox(row_id, "expired")
                log.info("Mensagem expirada por TTL imediatamente antes da publicação: outbox_id=%d boat=%s", row_id, boat_id)
                continue

            # Prepara propriedades MQTT v5 (Message Expiry Interval com TTL restante, mín 1s) (Claim 1)
            properties: Properties | None = None
            if exp is not None:
                exp_s = self._exp_to_seconds(exp)
                if exp_s is not None:
                    remaining_ttl = max(1, int(exp_s - now_s))
                    properties = Properties(PacketTypes.PUBLISH)
                    properties.MessageExpiryInterval = remaining_ttl

            # 9. Publicação QoS 1, retain=False
            confirmed = False
            try:
                confirmed = self.publish_fn(topic, payload, qos=1, retain=False, properties=properties)
            except TypeError:
                try:
                    confirmed = self.publish_fn(topic, payload, qos=1, retain=False)
                except TypeError:
                    confirmed = self.publish_fn(topic, payload)
            except Exception as exc:
                log.warning("Exceção na publicação: topic=%s boat=%s erro=%s", topic, boat_id, type(exc).__name__)
                confirmed = False

            # 10. Tratamento pós-publicação
            if confirmed:
                # Contabiliza no limitador de taxa ANTES da atualização do banco (Claim 3)
                self._record_rate_limit(boat_id, now_s)

                mark_ok = False
                try:
                    mark_ok = self.db.mark_outbox(row_id, "sent", packet_id=packet_id)
                except Exception as exc:
                    log.error("Exceção ao marcar outbox_id=%d como 'sent': %s", row_id, type(exc).__name__)
                    mark_ok = False

                if mark_ok is not False:
                    sent_count += 1
                    log.info("Mensagem enviada com sucesso: outbox_id=%d boat=%s topic=%s packet_id=%d", row_id, boat_id, topic, packet_id)
                else:
                    # Falha na marcação do banco: mantém packet_id no registro local e na fila para retentativa reusar o MESMO id (Claim 3)
                    row["packet_id"] = packet_id
                    log.warning("Falha ao marcar outbox_id=%d como 'sent'; mantém packet_id=%d para retentativa", row_id, packet_id)
            else:
                log.warning("Publicação não confirmada: outbox_id=%d boat=%s topic=%s; mantém na fila", row_id, boat_id, topic)

        return OutboxResult(
            sent=sent_count,
            failed=failed_count,
            rate_limited=rate_limited_count,
            claimed=claimed_count,
        )
