<div align="center">

  <img src="docs/rastro_logo.svg" alt="Rastro Logo" width="160" />

  # Rastro

  <p><strong>Autonomous, offline-first tactical Meshtastic LoRa tracking platform for sovereign territorial monitoring.</strong></p>

  <p>
    <a href="https://github.com/digidem/rastro/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-blue.svg?style=flat-square" alt="License: GPL-3.0"></a>
    <a href="https://hub.docker.com/u/communityfirst"><img src="https://img.shields.io/badge/docker-communityfirst%2Frastro-2496ED.svg?style=flat-square&logo=docker&logoColor=white" alt="Docker Hub"></a>
    <a href="https://meshtastic.org/"><img src="https://img.shields.io/badge/mesh-Meshtastic%202.x-green.svg?style=flat-square&logo=signal&logoColor=white" alt="Meshtastic"></a>
    <a href="https://solidjs.com/"><img src="https://img.shields.io/badge/frontend-SolidJS-446b9e.svg?style=flat-square&logo=solid&logoColor=white" alt="SolidJS"></a>
    <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/backend-FastAPI-009688.svg?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI"></a>
    <a href="https://www.postgresql.org/"><img src="https://img.shields.io/badge/database-PostgreSQL%2014%2F17-336791.svg?style=flat-square&logo=postgresql&logoColor=white" alt="PostgreSQL"></a>
    <a href="https://maplibre.org/"><img src="https://img.shields.io/badge/maps-MapLibre%20GL%20%2B%20PMTiles-3960D9.svg?style=flat-square&logo=maplibre&logoColor=white" alt="MapLibre"></a>
  </p>

  <p>
    <a href="#-quick-start">Quick Start</a> •
    <a href="#-overview">Overview</a> •
    <a href="#-key-features">Key Features</a> •
    <a href="#-system-architecture">Architecture</a> •
    <a href="#-hardware--fleet-support">Hardware Support</a> •
    <a href="#-deployment-modes">Deployment</a> •
    <a href="#-security--privacy-architecture">Security & Privacy</a> •
    <a href="#-documentation">Docs</a> •
    <a href="#-license--notices">License</a>
  </p>

</div>

---

## 🚀 Quick Start

### 1. Instant Viewer Preview (Mock Mode)

To inspect the tactical interface in under a minute without configuring Docker, PostgreSQL, MQTT, or physical radios:

```bash
# Clone the repository
git clone https://github.com/digidem/rastro.git
cd rastro/web

# Install frontend dependencies and start mock environment
pnpm install
pnpm dev:test
```

Open **`http://localhost:5173`** in your browser. The viewer immediately loads simulated tactical fleet fixtures (`FLEET_NODES`) with boats, rangers, base stations, and live breadcrumb trails.

> **Tip for mobile/field tablet testing:** Run `VITE_HOST=0.0.0.0 pnpm dev:test` to expose the mock viewer to your local network or VPN.

---

### 2. Full Local Stack (Docker Compose)

To spin up the integrated core pipeline (Mosquitto TLS, PostgreSQL, Ingestion worker, FastAPI, and Caddy Web Proxy) on a development workstation:

```bash
# From the repository root:
# 1. Generate local broker TLS certificates and CA (defaults to localhost, 127.0.0.1, mosquitto)
./scripts/rastro_gen_certs.sh

# 2. Configure environment secrets
cp deploy/env.example deploy/.env
# In deploy/.env, set POSTGRES_PASSWORD, RASTRO_API_TOKEN, and your desired MQTT passwords

# 3. Create Mosquitto credentials in the password file
docker compose -f deploy/docker-compose.yml run --rm mosquitto \
  mosquitto_passwd -c /mosquitto/config/passwd gateway
docker compose -f deploy/docker-compose.yml run --rm mosquitto \
  mosquitto_passwd -b /mosquitto/config/passwd ingest 'INGEST_PASSWORD_FROM_ENV'

# 4. Launch the stack bound strictly to loopback (127.0.0.1)
docker compose -f deploy/docker-compose.yml up -d

# 5. Verify service health
docker compose -f deploy/docker-compose.yml ps
```

Access the viewer through the local proxy at **`http://localhost:8081`**. Authenticate using the `RASTRO_API_TOKEN` configured in `deploy/.env`. See [`docs/DESENVOLVIMENTO.md`](docs/DESENVOLVIMENTO.md) for full setup nuances.

---

## 🧭 Overview

**Rastro** is an autonomous, self-hosted tracking and telemetry platform engineered for monitoring tactical teams, river patrol vessels, and territorial defenders across remote regions using **Meshtastic LoRa mesh networks**.

Built specifically for sovereign operations with intermittent or zero cellular connectivity (such as indigenous territories, remote river basins, and protected conservation reserves), Rastro provides complete local control over geographic tracks. It eliminates reliance on public Meshtastic MQTT brokers (`mqtt.meshtastic.org`) and commercial cloud mapping providers.

### Operating & Mapping Modes
- **Offline-First Mode**: By deploying a local vector tile file (`basemap.pmtiles`) alongside the web viewer and selecting the **"Local"** basemap mode, maps render completely within the browser with zero external network requests.
- **Online / Satellite Mode**: When internet access is available, operators can switch to satellite or road layers (Google, Esri, or OpenStreetMap). OpenStreetMap requests pass through the local authenticated API proxy (`/api/osm/`), which caches tiles in memory (6h TTL) without logging client geographic coordinates. Google and Esri layers connect directly when selected.
- **Dual Uplink Ingestion**: Ingests positions via a local USB-connected base station radio (`services/rastro_gateway/bridge`) or direct native encrypted MQTT packets from remote Wi-Fi/LTE-enabled Meshtastic nodes (`services/rastro_gateway/native`).

---

## ✨ Key Features

- 🛰️ **Airgapped & Offline-First Mapping**: Browser-native vector tile rendering powered by **MapLibre GL** and **PMTiles**. Deploy entirely without internet connectivity by selecting the local basemap.
- ⛵ **Dynamic Geodesic Vessel Heading**: Computes true boat headings from recent track vectors (`web/src/lib/bearing.ts`), rotating native boat silhouettes accurately even when GPS trackers do not output compass heading data.
- 💾 **Resilient Disk Spooling**: Gateway spools incoming radio packets to disk before publishing to MQTT with QoS 1, buffering updates across network outages and replaying upon reconnect (bounded by local disk storage and broker queue limits).
- 🔒 **Defense-in-Depth Security**: TLS-only Mosquitto broker (port 8883), strict per-node topic ACL dispatch filtering, Scram-SHA-256 PostgreSQL authentication, and memory-managed session authentication.
- ⚡ **Idempotent Ingestion & Deduplication**: Database-level deduplication (`UNIQUE(node_num, pos_time)`) guarantees replayed mesh packets never corrupt track histories or inflate database storage.
- 💬 **Tactical Mesh Messaging**: Integrated chat outbox (`/api/chat/send` + `rastro-chat` worker) allowing dispatchers to send direct messages and alerts back to field radios over the LoRa mesh.
- 📡 **Hardware-Aware Representation**: Tailored silhouettes and status reporting for field devices (**Heltec V3 / V4**, **RAK4631**, **RAK WisMesh Tag**, **LilyGO T-Beam / T-Echo**, and **Tracker T1000-E**).

---

## 🏗️ System Architecture

Rastro coordinates field radios, serial/native gateways, secure messaging brokers, transactional storage, and reactive web interfaces:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Meshtastic LoRa Mesh Fleet                          │
│     [River Vessel Heltec]        [Field Ranger RAK]       [Patrol T-Beam]   │
└───────────────────────┬──────────────────────────────▲──────────────────────┘
                        │                              │
         (LoRa RF / USB)▼               (Native MQTT)  │ (Downlink via LoRa)
┌───────────────────────────────┐                      │
│     rastro-gateway (Host)     │                      │
│  • Serial Bridge & Filter     │                      │
│  • Spool Buffer on Disk       │                      │
│  • MQTT QoS 1 Publisher       │                      │
└───────────────┬───────────────┘                      │
                │ (TLS 8883 / QoS 1)                   │
                ▼                                      │
┌──────────────────────────────────────────────────────┴──────────────────────┐
│                          rastro-broker (Mosquitto)                          │
│  • TLS-Only Listener (Port 8883)        • Per-Node HMAC-SHA256 Auth         │
│  • Isolated Boat Topics (rastro-vgw:*)  • ACL Dispatch Message Filtering    │
└───────────────────┬──────────────────────────────────▲──────────────────────┘
                    │ (QoS 1 Persistent Session)       │ (Publish Downlink)
                    ▼                                  │
┌──────────────────────────────────────┐  ┌────────────┴──────────────────────┐
│         rastro-ingest Worker         │  │         rastro-chat Worker        │
│  • Ingests Serial & Native Packets   │  │  • Drains outbox from PostgreSQL  │
│  • Batches commits to PostgreSQL     │  │  • Publishes to broker for radio  │
└───────────────────┬──────────────────┘  └────────────▲──────────────────────┘
                    │ (psycopg / SCRAM-SHA-256)        │ (Poll / Notify)
                    ▼                                  │
┌──────────────────────────────────────────────────────┴──────────────────────┐
│                           PostgreSQL Database                               │
│  • Database: `rastro`                 • Schema: `rastro`                    │
│  • Roles: <db>_ingest (Write)         • Role: <db>_viewer (Read + Chat)     │
│  • Deduplication: UNIQUE(node_num, pos_time) • Table: chat_outbox           │
└───────────────────────┬──────────────────────────────▲──────────────────────┘
                        │                              │
        (Read Feeds)    ▼                              │ (Chat Outbox Enqueue)
┌──────────────────────────────────────────────────────┴──────────────────────┐
│                            rastro-api (FastAPI)                             │
│  • Read-Only Feeds: /api/nodes/latest, /api/tracks/:id                      │
│  • Chat Endpoint: /api/chat/send (writes to outbox)                         │
│  • Bearer Token & HttpOnly Cookie Authentication                            │
│  • Authenticated OSM Tile Proxy (6h memory cache)                           │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ (JSON / GeoJSON / Tiles)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           web/ (SolidJS Viewer)                             │
│  • MapLibre GL Vector Engine          • PMTiles Offline Basemap             │
│  • Dynamic Geodesic Vessel Heading    • Node Filters, Battery & SNR Monitor │
└─────────────────────────────────────────────────────────────────────────────┘
```

> *Note on Local Compose Stack:* `deploy/docker-compose.yml` deploys the core ingestion, database, API, and viewer pipeline. Full bi-directional chat downlink dispatch with the `rastro-chat` background worker is deployed in production via CapRover.

### Component Inventory

| Component | Repository Path / Container | Technology | Role & Scope |
|---|---|---|---|
| **Gateway Bridge** | `services/rastro_gateway/bridge` | Python 3.11+, PySerial | Drains serial packets from base radio, filters telemetry, spools to disk, publishes to MQTT. |
| **Native Ingest** | `services/rastro_gateway/native` | Python 3.11+ | Decodes direct Meshtastic native protobuf packets over MQTT. |
| **MQTT Broker** | `broker/` (`communityfirst/rastro-broker`) | Mosquitto 2, Python | TLS-only on 8883, per-node ACL derivation (`derive.py`), filters message delivery per boat. |
| **Ingest Service** | `services/rastro_gateway/ingest` | Python, `psycopg` | Drains broker queue, commits batches to PostgreSQL, acknowledges messages only after database commit. |
| **Chat Worker** | `services/rastro_gateway/chat` | Python, `paho-mqtt` | Drains outgoing messages from `chat_outbox` in PostgreSQL and publishes them to the broker for downlink. |
| **Database** | `deploy/postgres/` | PostgreSQL 14 / 17 | Dedicated `rastro` database; schema isolation, conflict deduplication, and partitioned roles. |
| **REST API** | `services/rastro_api` (`communityfirst/rastro-api`) | FastAPI, Uvicorn | Serves read-only GeoJSON tracks and telemetry; provides chat outbox enqueue (`/api/chat/send`) and tile proxy. |
| **Tactical Viewer** | `web/` (`communityfirst/rastro-web`) | SolidJS, MapLibre GL | Browser viewer with offline PMTiles, real-time node cards, vessel bearing calculation, and chat UI. |

---

## 🚢 Hardware & Fleet Support

### Bench-Tested Hardware
- **Heltec WiFi LoRa 32 V4** (ESP32-S3 + SX1262): Rigorously bench-tested with official firmware 2.7.26 and validated in production riverboat configurations (`!a35a8024`), confirming strict broker ACL dispatch filtering and zero cross-boat leakage.

### Supported Mesh Models & Iconography
Rastro parses standard Meshtastic 2.x position and device telemetry, providing specialized SVG silhouettes in `web/public/devices/`:
- **Heltec WiFi LoRa 32 V3 / V4** (`heltec_v4.svg`, `heltec-v3.svg`)
- **RAK Wireless RAK4631 / WisMesh** (`rak4631.svg`, `rak_wismesh_tag.svg`)
- **LilyGO T-Beam & T-Echo** (`tbeam.svg`, `t-echo.svg`)
- **SenseCAP Tracker T1000-E** (`tracker-t1000-e.svg`)
- **Custom River Vessels** (`boat.svg`) — dynamically rotated using computed geodesic heading.

---

## 📦 Deployment Modes

### 1. CapRover One-Click App (Production Multi-Container)
Rastro is packaged as a verified one-click app for CapRover clusters (`digidem/caprover-one-click-apps`).
- Automated database schema bootstrap via `<app>-setup`.
- Automatic TLS certificate generation and SCRAM role provisioning.
- Automated inter-container routing over Docker Swarm overlay networks.
- Full operational runbook: [`docs/OPERACAO-caprover.md`](docs/OPERACAO-caprover.md).

> **Production Warning:** Rastro operates in production with frozen radio contracts (30-character HMAC secret derivation, EVU channel keys, and virtual gateway IDs). Review the frozen contract in [`AGENTS.md`](AGENTS.md) before altering broker auth or database schemas.

### 2. Base Station Gateway (Raspberry Pi / Linux Host)
The gateway bridge runs as a hardened systemd service on the host connected to the base station USB radio:
- Systemd sandboxing: `ProtectSystem=strict`, state directory at `/var/lib/rastro-gateway`.
- Restricted device access: `DeviceAllow` granted only to serial ports.
- Radio USB provisioning guide: [`docs/PROVISAO-noes.md`](docs/PROVISAO-noes.md).

---

## 🔐 Security & Privacy Architecture

Given the sensitivity of monitoring indigenous lands and tactical patrols, Rastro enforces defense-in-depth across every boundary:

1. **Transport Isolation**:
   - The Mosquitto broker listens exclusively on TLS port 8883 (plaintext port 1883 is disabled).
   - Gateway and ingest clients require strict CA certificate validation; there is no unverified TLS mode.
2. **Cryptographic Topic Isolation & ACL Dispatch**:
   - Node credentials are cryptographically derived (`derive.py`) via HMAC-SHA256 from `RASTRO_NATIVE_SECRET`.
   - Each boat is isolated to its virtual gateway topic (`rastro-vgw:*`). While the firmware may issue broad subscriptions, Mosquitto ACLs strictly filter message dispatch, guaranteeing no vessel receives packets from other vessels.
3. **Least-Privilege Database Access**:
   - Database roles are partitioned strictly: `<db>_ingest` possesses insert privileges on telemetry/positions without track-reading permissions; `<db>_viewer` operates in read-only mode across tracking tables with narrow write access to `chat_outbox`.
4. **Session Authentication & CSRF/CSP Protection**:
   - Operator sessions use HttpOnly cookies (with optional persistent session) or in-memory Bearer tokens.
   - Strict Content Security Policy (CSP) on the web proxy prevents external script injection and unauthorized outbound connections.
5. **Operational Privacy**:
   - Raw radio packets, channel encryption keys, and credentials are never logged to stdout or persistent files.
   - For troubleshooting, see the threat model and operational guide in [`docs/SEGURANCA.md`](docs/SEGURANCA.md).

---

## 🛠️ Development & Testing

### Prerequisites
- **Python**: 3.11+ (in a virtual environment)
- **Node.js**: 20+ with **pnpm** 9+
- **Docker**: 28+ (for PostgreSQL test containers and compose testing)

### Running Tests

```bash
# 1. Gateway & Ingestion tests (Pytest)
cd services/rastro_gateway
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q

# 2. Tactical Web Viewer unit tests (Vitest)
cd ../../web
pnpm install --frozen-lockfile
pnpm test

# 3. Web code formatting and linting (Biome)
pnpm biome check

# 4. API unit tests (requires a disposable test PostgreSQL; see docs/DESENVOLVIMENTO.md)
cd ../services/rastro_api
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q

# 5. CapRover simulation rig (end-to-end template verification)
cd ../..
deploy/sim/run.sh <path-to-app-template.yml>
```

> **Repository Conventions:**
> - Code identifiers, internal API models, and database columns are in English.
> - Operator documentation, command-line tool messages, and field user interfaces are maintained in Brazilian Portuguese (PT-BR).

---

## 📚 Documentation

| Document | Purpose |
|---|---|
| [`docs/DESENVOLVIMENTO.md`](docs/DESENVOLVIMENTO.md) | Comprehensive development setup, testing workflows, and conventions. |
| [`docs/OPERACAO-caprover.md`](docs/OPERACAO-caprover.md) | CapRover deployment, maintenance, backups, and cluster operations. |
| [`docs/PROVISAO-noes.md`](docs/PROVISAO-noes.md) | Step-by-step USB serial provisioning runbook for Meshtastic field nodes. |
| [`docs/SEGURANCA.md`](docs/SEGURANCA.md) | Threat model, cryptographic controls, and security incident response. |
| [`AGENTS.md`](AGENTS.md) | Architectural memory, production contracts, and system invariant rules. |

---

## 📄 License & Notices

Rastro is free software licensed under the **GNU General Public License v3.0** ([GPL-3.0-only](LICENSE)).

### Third-Party Notices
- **Viewer Frontend**: Derived from [`meshtastic/map`](https://github.com/meshtastic/map) (GPL-3.0-only); see [`web/NOTICE`](web/NOTICE).
- **Map Glyphs & Typography**: Noto Sans glyphs generated with Fontnik, © The Noto Project Authors, licensed under the **SIL Open Font License 1.1** ([OFL-1.1](web/public/glyphs/OFL.txt)).
- **Default Map Data**: Vector basemaps and OpenStreetMap tiles © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), licensed under the **Open Database License (ODbL)**.

---

<div align="center">
  <sub>Developed for community-first territorial defense and open-source geospatial sovereignty.</sub>
</div>
