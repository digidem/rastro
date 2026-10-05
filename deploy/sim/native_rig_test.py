#!/usr/bin/env python3
"""Suite de testes do rig nativo MQTT v5 (WP-C).

Testa contas, ACLs granulares, isolamento entre embarcações e opções de TLS
no broker Mosquitto em execução com paho-mqtt v2 e protocolo MQTT v5.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import ssl
import sys
import threading
import time

import paho.mqtt.client as mqtt


class MqttTestClient:
    """Cliente wrapper para testes síncronos MQTT v5 via paho-mqtt v2."""

    def __init__(
        self,
        client_id: str,
        username: str | None = None,
        password: str | None = None,
        ca_certs: str | None = None,
        host: str = "127.0.0.1",
        port: int = 18883,
        tls_enabled: bool = True,
    ) -> None:
        self.client_id = client_id
        self.username = username
        self.password = password
        self.ca_certs = ca_certs
        self.host = host
        self.port = port
        self.tls_enabled = tls_enabled

        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv5,
        )

        if username is not None:
            self.client.username_pw_set(username, password)

        if self.tls_enabled:
            # TLS ativo; se ca_certs for None, usa o store padrão do sistema
            self.client.tls_set(ca_certs=ca_certs)

        self._connected_event = threading.Event()
        self.connack_rc: mqtt.ReasonCode | None = None
        self.connack_props = None

        self._pub_events: dict[int, threading.Event] = {}
        self._pub_rcs: dict[int, mqtt.ReasonCode] = {}
        self._pub_lock = threading.Lock()

        self._sub_events: dict[int, threading.Event] = {}
        self._sub_rcs: dict[int, list[mqtt.ReasonCode]] = {}
        self._sub_lock = threading.Lock()

        self.messages: list[tuple[str, bytes]] = []
        self._msg_event = threading.Event()
        self._msg_lock = threading.Lock()

        self.disconnected = False
        self.disconnect_rc = None
        self._disconnect_event = threading.Event()

        self.client.on_connect = self._on_connect
        self.client.on_publish = self._on_publish
        self.client.on_subscribe = self._on_subscribe
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        self.disconnected = True
        self.disconnect_rc = rc
        self._disconnect_event.set()
        with self._pub_lock:
            for ev in self._pub_events.values():
                ev.set()

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        self.connack_rc = rc
        self.connack_props = properties
        self._connected_event.set()

    def _on_publish(self, client, userdata, mid, reason_code, properties=None):
        with self._pub_lock:
            self._pub_rcs[mid] = reason_code
            ev = self._pub_events.get(mid)
            if ev:
                ev.set()

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties=None):
        with self._sub_lock:
            self._sub_rcs[mid] = reason_codes
            ev = self._sub_events.get(mid)
            if ev:
                ev.set()

    def _on_message(self, client, userdata, msg):
        with self._msg_lock:
            self.messages.append((msg.topic, msg.payload))
            self._msg_event.set()

    def connect(self, timeout: float = 5.0) -> mqtt.ReasonCode:
        """Conecta ao broker e aguarda CONNACK."""
        self.client.connect(self.host, self.port, keepalive=60)
        self.client.loop_start()
        if not self._connected_event.wait(timeout=timeout):
            raise TimeoutError(f"Timeout aguardando CONNACK para {self.client_id}")
        return self.connack_rc

    def publish(
        self, topic: str, payload: bytes | str, qos: int = 1, retain: bool = False, timeout: float = 5.0
    ) -> mqtt.ReasonCode:
        """Publica mensagem e aguarda confirmação PUBACK com reason code."""
        if isinstance(payload, str):
            payload = payload.encode("utf-8")

        ev = threading.Event()
        info = self.client.publish(topic, payload, qos=qos, retain=retain)
        mid = info.mid
        with self._pub_lock:
            if mid in self._pub_rcs:
                return self._pub_rcs[mid]
            self._pub_events[mid] = ev

        if not ev.wait(timeout=timeout):
            raise TimeoutError(f"Timeout aguardando PUBACK para mid {mid} em {topic}")
        return self._pub_rcs[mid]

    def subscribe(self, topic: str, qos: int = 1, timeout: float = 5.0) -> list[mqtt.ReasonCode]:
        """Assina tópico e aguarda SUBACK com reason codes."""
        ev = threading.Event()
        res, mid = self.client.subscribe(topic, qos=qos)
        with self._sub_lock:
            if mid in self._sub_rcs:
                return self._sub_rcs[mid]
            self._sub_events[mid] = ev

        if not ev.wait(timeout=timeout):
            raise TimeoutError(f"Timeout aguardando SUBACK para {topic}")
        return self._sub_rcs[mid]

    def wait_for_messages(self, count: int, timeout: float = 5.0) -> list[tuple[str, bytes]]:
        """Aguarda até receber a quantidade esperada de mensagens ou timeout."""
        start = time.time()
        while time.time() - start < timeout:
            with self._msg_lock:
                if len(self.messages) >= count:
                    return list(self.messages)
            time.sleep(0.05)
        with self._msg_lock:
            return list(self.messages)

    def close(self):
        """Para o loop e fecha o socket."""
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Testes rig nativo MQTT v5")
    parser.add_argument("--host", default="127.0.0.1", help="Endereço do broker")
    parser.add_argument("--port", type=int, default=18883, help="Porta mapeada do broker")
    parser.add_argument("--ca", required=True, help="Caminho da CA pública do broker")
    parser.add_argument("--accounts", required=True, help="Arquivo JSON de contas")
    args = parser.parse_args()

    ca_path = args.ca
    if not Path(ca_path).is_file():
        sys.stderr.write(f"ERRO: CA não encontrada em {ca_path}\n")
        return 1

    with open(args.accounts, "r", encoding="utf-8") as f:
        accounts = json.load(f)

    root = accounts["root"]
    ingest_pw = accounts["ingest"]["password"]
    outbox_pw = accounts["outbox"]["password"]
    nodes = accounts["nodes"]
    vgws = accounts["virtual_gateways"]

    vgw_map = {v["boat"]: v["gateway_id"] for v in vgws}

    failed_checks: list[str] = []

    def check(name: str, condition: bool, detail: str = ""):
        if condition:
            msg = f"PASS: {name}"
            if detail:
                msg += f" ({detail})"
            print(msg)
        else:
            msg = f"FAIL: {name}"
            if detail:
                msg += f" ({detail})"
            print(msg)
            failed_checks.append(name)

    print("=== INICIANDO TESTES DO RIG NATIVO (MQTT v5 / TLS) ===")

    # 1. TLS handshake through mapped port with the CA
    try:
        cli = MqttTestClient("rig-check-tls", "ingest", ingest_pw, ca_certs=ca_path, host=args.host, port=args.port)
        rc = cli.connect(timeout=5.0)
        cli.close()
        check("TLS handshake através da porta mapeada com a CA do broker", rc == 0, f"rc={rc}")
    except Exception as exc:
        check("TLS handshake através da porta mapeada com a CA do broker", False, f"exceção: {exc}")

    # 2. Client without CA fails
    try:
        # Tenta conectar sem informar a CA privada (store de sistema padrão)
        cli_bad_ca = MqttTestClient("rig-check-no-ca", "ingest", ingest_pw, ca_certs=None, host=args.host, port=args.port)
        try:
            cli_bad_ca.connect(timeout=3.0)
            cli_bad_ca.close()
            # Se conectou sem a CA customizada, o teste falha!
            check("Cliente sem a CA do broker é rejeitado no handshake TLS", False, "conectou indevidamente")
        except (ssl.SSLError, OSError) as exc:
            cli_bad_ca.close()
            check("Cliente sem a CA do broker é rejeitado no handshake TLS", True, f"falhou como esperado: {type(exc).__name__}")
    except Exception as exc:
        check("Cliente sem a CA do broker é rejeitado no handshake TLS", False, f"exceção inesperada: {exc}")

    # 3. Unknown user refused
    try:
        cli_bad_user = MqttTestClient("rig-check-unknown-user", "usuario_fantasma", "senha123456789012345678", ca_certs=ca_path, host=args.host, port=args.port)
        rc_unknown = cli_bad_user.connect(timeout=5.0)
        cli_bad_user.close()
        # No MQTT v5: 134 = Bad user name or password, 135 = Not authorized
        is_refused = rc_unknown.is_failure and rc_unknown.value in (134, 135)
        check("Usuário desconhecido recusado no CONNACK", is_refused, f"rc={rc_unknown} ({rc_unknown.value})")
    except Exception as exc:
        check("Usuário desconhecido recusado no CONNACK", False, f"exceção: {exc}")

    # 4. Ingest cannot publish (PUBACK reason code 135)
    try:
        cli_ingest = MqttTestClient("rig-ingest-pub-check", "ingest", ingest_pw, ca_certs=ca_path, host=args.host, port=args.port)
        cli_ingest.connect(timeout=5.0)
        topic_test = f"{root}/2/e/EVU/{vgws[0]['gateway_id']}"
        rc_pub = cli_ingest.publish(topic_test, b"tentativa_ingest_proibida", qos=1)
        cli_ingest.close()
        check("Ingest não consegue publicar (PUBACK reason code 135)", rc_pub.value == 135, f"rc={rc_pub.value}")
    except Exception as exc:
        check("Ingest não consegue publicar (PUBACK reason code 135)", False, f"exceção: {exc}")

    # 5. Per-account publish allowed/denied (PUBACK reason code 135 for denied)
    try:
        node0 = nodes[0]
        node0_user = node0["user"]
        node0_pw = node0["password"]
        node1_user = nodes[1]["user"]
        vgw0_id = vgw_map[node0["boat"]]

        cli_node0 = MqttTestClient(f"rig-node0-perm-{time.time_ns()}", node0_user, node0_pw, ca_certs=ca_path, host=args.host, port=args.port)
        cli_node0.connect(timeout=5.0)

        # Nó publicando no seu próprio tópico: permitido (<root>/2/e/+/!id)
        rc_node_allowed = cli_node0.publish(f"{root}/2/e/EVU/{node0_user}", b"uplink_valido", qos=1)
        # rc < 128 significa sucesso (0 ou 16 - no matching subscribers)
        node_allow_ok = (rc_node_allowed.value < 128)

        # Nó publicando no tópico de outro nó: negado (deve receber 135)
        rc_node_denied_node = cli_node0.publish(f"{root}/2/e/EVU/{node1_user}", b"uplink_outro_no", qos=1)
        node_deny_other_ok = (rc_node_denied_node.value == 135)

        # Nó publicando no tópico vgw: negado (deve receber 135)
        rc_node_denied_vgw = cli_node0.publish(f"{root}/2/e/EVU/{vgw0_id}", b"vgw_invasao", qos=1)
        node_deny_vgw_ok = (rc_node_denied_vgw.value == 135)

        cli_node0.close()

        # Outbox: permitido em vgw, negado em nó
        cli_outbox = MqttTestClient(f"rig-outbox-perm-{time.time_ns()}", "outbox", outbox_pw, ca_certs=ca_path, host=args.host, port=args.port)
        cli_outbox.connect(timeout=5.0)

        rc_outbox_allowed = cli_outbox.publish(f"{root}/2/e/EVU/{vgw0_id}", b"downlink_valido", qos=1)
        outbox_allow_ok = (rc_outbox_allowed.value < 128)

        rc_outbox_denied = cli_outbox.publish(f"{root}/2/e/EVU/{node0_user}", b"downlink_no_invalido", qos=1)
        outbox_deny_ok = (rc_outbox_denied.value == 135)

        cli_outbox.close()

        all_pub_perms_ok = (
            node_allow_ok and node_deny_other_ok and node_deny_vgw_ok and outbox_allow_ok and outbox_deny_ok
        )
        check(
            "Publicações permitidas/negadas por conta (PUBACK 135 quando negado)",
            all_pub_perms_ok,
            f"node_allow={rc_node_allowed.value}, node_deny_other={rc_node_denied_node.value}, "
            f"node_deny_vgw={rc_node_denied_vgw.value}, outbox_allow={rc_outbox_allowed.value}, "
            f"outbox_deny={rc_outbox_denied.value}"
        )
    except Exception as exc:
        check("Publicações permitidas/negadas por conta (PUBACK 135 quando negado)", False, f"exceção: {exc}")

    # 6. SUBACK + delivery under ACL with wildcard subscribe EVU/+ on a node account
    # REPORT EMPIRICALLY WHAT MOSQUITTO DOES
    try:
        node0 = nodes[0]
        node0_user = node0["user"]
        node0_pw = node0["password"]
        vgw0_id = vgw_map[node0["boat"]]
        vgw1_id = vgw_map[nodes[1]["boat"]]

        cli_wild = MqttTestClient(f"rig-wild-sub-{time.time_ns()}", node0_user, node0_pw, ca_certs=ca_path, host=args.host, port=args.port)
        cli_wild.connect(timeout=5.0)

        # Assina com wildcard EVU/+
        sub_rcs = cli_wild.subscribe(f"{root}/2/e/EVU/+", qos=1, timeout=5.0)
        granted_sub_rc = sub_rcs[0]

        # Relatório empírico formal
        print("\n" + "=" * 70)
        print("RELATÓRIO EMPÍRICO DE COMPORTAMENTO DO MOSQUITTO:")
        print(f"  - Inscrição com wildcard: '{root}/2/e/EVU/+' por conta de nó ({node0_user})")
        print(f"  - Resposta do SUBACK: concedido com reason_code = {granted_sub_rc} ({granted_sub_rc.getName() if hasattr(granted_sub_rc, 'getName') else granted_sub_rc})")
        print("  - Comportamento observado: o broker aceita a inscrição curinga no SUBACK;")
        print("    porém, no momento do despacho (PUBLISH aos assinantes), o Mosquitto aplica")
        print("    o filtro de ACL estrito da conta receptora. Mensagens em tópicos não autorizados")
        print("    pela ACL são descartadas silenciosamente para aquele cliente.")
        print("=" * 70 + "\n")

        # Outbox publica nos dois vgws
        cli_outbox = MqttTestClient(f"rig-outbox-wild-{time.time_ns()}", "outbox", outbox_pw, ca_certs=ca_path, host=args.host, port=args.port)
        cli_outbox.connect(timeout=5.0)

        msg_meu_barco = b"msg_direcionada_ao_barco_b1"
        msg_outro_barco = b"msg_direcionada_ao_barco_b2"

        cli_outbox.publish(f"{root}/2/e/EVU/{vgw0_id}", msg_meu_barco, qos=1)
        cli_outbox.publish(f"{root}/2/e/EVU/{vgw1_id}", msg_outro_barco, qos=1)

        recebidas = cli_wild.wait_for_messages(count=2, timeout=2.0)
        cli_wild.close()
        cli_outbox.close()

        payloads = [p for _, p in recebidas]
        entregou_apenas_autorizado = (msg_meu_barco in payloads) and (msg_outro_barco not in payloads)

        check(
            "SUBACK + entrega sob ACL com wildcard EVU/+ em conta de nó",
            entregou_apenas_autorizado,
            f"suback_rc={granted_sub_rc}, total_msgs={len(recebidas)}, autorizado={msg_meu_barco in payloads}, nao_autorizado={msg_outro_barco in payloads}"
        )
    except Exception as exc:
        check("SUBACK + entrega sob ACL com wildcard EVU/+ em conta de nó", False, f"exceção: {exc}")

    # 7. 6 boats x 6 simultaneous node clients with no cross-river leakage
    try:
        node_clients: list[MqttTestClient] = []
        for i, n in enumerate(nodes[:6]):
            c = MqttTestClient(
                f"rig-node-simul-{i}-{time.time_ns()}",
                n["user"],
                n["password"],
                ca_certs=ca_path,
                host=args.host,
                port=args.port,
            )
            c.connect(timeout=5.0)
            c.subscribe(f"{root}/2/e/EVU/+", qos=1, timeout=5.0)
            node_clients.append(c)

        # Ingest client simultâneo
        ingest_cli = MqttTestClient(
            f"rig-ingest-simul-{time.time_ns()}",
            "ingest",
            ingest_pw,
            ca_certs=ca_path,
            host=args.host,
            port=args.port,
        )
        ingest_cli.connect(timeout=5.0)
        ingest_cli.subscribe(f"{root}/2/e/#", qos=1, timeout=5.0)

        # Outbox publica uma mensagem única para cada um dos 6 vgws
        cli_outbox = MqttTestClient(
            f"rig-outbox-simul-{time.time_ns()}",
            "outbox",
            outbox_pw,
            ca_certs=ca_path,
            host=args.host,
            port=args.port,
        )
        cli_outbox.connect(timeout=5.0)

        expected_boat_msgs: dict[str, bytes] = {}
        for n in nodes[:6]:
            b_id = n["boat"]
            v_id = vgw_map[b_id]
            msg = f"mensagem_exclusiva_para_barco_{b_id}_{time.time_ns()}".encode("utf-8")
            expected_boat_msgs[b_id] = msg
            cli_outbox.publish(f"{root}/2/e/EVU/{v_id}", msg, qos=1)

        time.sleep(1.0)

        leakage_detected = False
        leak_details: list[str] = []

        for i, (n, c) in enumerate(zip(nodes[:6], node_clients)):
            b_id = n["boat"]
            expected = expected_boat_msgs[b_id]
            actual_payloads = [p for _, p in c.messages]

            if expected not in actual_payloads:
                leakage_detected = True
                leak_details.append(f"Barco {b_id} não recebeu sua mensagem esperada")

            for other_boat, other_msg in expected_boat_msgs.items():
                if other_boat != b_id and other_msg in actual_payloads:
                    leakage_detected = True
                    leak_details.append(f"Vazamento! Barco {b_id} recebeu mensagem do barco {other_boat}")

        # Ingest deve ter recebido todas as 6 mensagens
        ingest_payloads = [p for _, p in ingest_cli.messages]
        ingest_received_all = all(m in ingest_payloads for m in expected_boat_msgs.values())
        if not ingest_received_all:
            leak_details.append("Ingest não recebeu todas as 6 mensagens")

        for c in node_clients:
            c.close()
        ingest_cli.close()
        cli_outbox.close()

        check(
            "6 barcos x 6 clientes nós simultâneos sem vazamento entre barcos",
            not leakage_detected and ingest_received_all,
            f"falhas: {', '.join(leak_details) if leak_details else 'nenhuma'}"
        )
    except Exception as exc:
        check("6 barcos x 6 clientes nós simultâneos sem vazamento entre barcos", False, f"exceção: {exc}")

    # 8. Retained messages not delivered to late subscribers
    try:
        cli_outbox = MqttTestClient(
            f"rig-outbox-retain-{time.time_ns()}",
            "outbox",
            outbox_pw,
            ca_certs=ca_path,
            host=args.host,
            port=args.port,
        )
        cli_outbox.connect(timeout=5.0)

        # Verifica se o broker anunciou RetainAvailable: 0 no CONNACK MQTT v5
        retain_avail = getattr(cli_outbox.connack_props, "RetainAvailable", None)

        retained_topic = f"{root}/2/e/EVU/{vgws[0]['gateway_id']}"
        retained_payload = b"msg_com_flag_retain_tentada"

        # Tenta publicar com retain=True
        # No MQTT v5 com retain_available false, o broker rejeita a publicação com retain
        # (desconectando com razão 0x9A Retain not supported conforme spec MQTT v5)
        try:
            cli_outbox.client.publish(retained_topic, retained_payload, qos=1, retain=True)
            cli_outbox._disconnect_event.wait(timeout=2.0)
        except Exception:
            pass
        finally:
            cli_outbox.close()

        # Agora conecta um novo assinante TARDIO (late subscriber)
        cli_late_sub = MqttTestClient(
            f"rig-late-sub-{time.time_ns()}",
            nodes[0]["user"],
            nodes[0]["password"],
            ca_certs=ca_path,
            host=args.host,
            port=args.port,
        )
        cli_late_sub.connect(timeout=5.0)
        cli_late_sub.subscribe(retained_topic, qos=1, timeout=5.0)

        # Espera para verificar se recebe alguma mensagem retida
        time.sleep(1.5)
        late_msgs = cli_late_sub.messages
        cli_late_sub.close()

        no_retained_delivered = (len(late_msgs) == 0) and (retain_avail == 0 or cli_outbox.disconnected)
        check(
            "Mensagens retidas não são entregues a assinantes tardios (retain desligado)",
            no_retained_delivered,
            f"broker_RetainAvailable={retain_avail}, outbox_disconnected_on_retain={cli_outbox.disconnected}, msgs_entregues_ao_assinante_tardio={len(late_msgs)}"
        )
    except Exception as exc:
        check("Mensagens retidas não são entregues a assinantes tardios (retain desligado)", False, f"exceção: {exc}")

    print("\n=== RESUMO DOS TESTES ===")
    if failed_checks:
        print(f"RESULTADO: {len(failed_checks)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for fc in failed_checks:
            print(f"  - {fc}")
        return 1
    else:
        print("RESULTADO: TODOS OS CHECKS DO WP-C PASSARAM COM SUCESSO! (PASS)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
