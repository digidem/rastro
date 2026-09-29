"""Ponto de entrada da ponte: ``python -m rastro_gateway.bridge``.

Porta serial ausente sai com código 3 — o systemd (``Restart=always``,
``RestartSec=10``) cuida da retentativa; sem loop interno de retry.
SIGTERM/SIGINT → desconecta SerialInterface + MQTT e sai limpo.
"""
from __future__ import annotations

import sys

from rastro_gateway.bridge.gateway import main

if __name__ == "__main__":
    sys.exit(main())
