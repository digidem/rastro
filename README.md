# Rastro

Rastro tracks Meshtastic radios (boats, vehicles, people) over a LoRa mesh and shows them on a self-hosted map, with no dependency on public Meshtastic servers or third-party maps.

```
[Meshtastic trackers] --LoRa--> [routers] --> [gateway node]
                                                  | USB
                                                  v
                                  rastro-gateway (host, systemd)
                                  filter -> disk spool -> MQTT (QoS 1, TLS)
                                                  |
                                                  v
          rastro-broker (Mosquitto, TLS only) -> rastro-ingest -> PostgreSQL
                                                                     |
                           browser <- rastro-web (viewer + /api proxy) <- rastro-api (read-only)
```

- **Gateway** (`services/rastro_gateway/bridge`): reads decoded packets from a Meshtastic node over USB, keeps only position and telemetry, writes each record to a disk spool before publishing, and replays the spool after an outage.
- **Broker** (Mosquitto): TLS on 8883 only, password + ACL per user (`gateway` publishes, `ingest` subscribes).
- **Ingest** (`services/rastro_gateway/ingest`): subscribes with a persistent session and acknowledges each message only after the database commit, so nothing is lost between broker and database. Replays are deduplicated by the database.
- **API** (`services/rastro_api`): FastAPI, read-only role, token or session-cookie auth, GeoJSON responses.
- **Viewer** (`web/`): SolidJS + MapLibre, offline basemap from a PMTiles file served next to the app.

Position tracks can be sensitive. Rastro keeps them on infrastructure you control: nothing is sent to public maps or third-party services.

## Deployment options

- **Single host with Docker Compose** — `deploy/docker-compose.yml`; everything bound to loopback. See `docs/DESENVOLVIMENTO.md`.
- **CapRover** — one-click app `rastro` in the digidem store (`digidem/caprover-one-click-apps`), using an existing PostgreSQL; the only required field is the Postgres admin password (all other passwords and the broker TLS certificate are generated automatically; a temporary `<app>-setup` app prepares the database). See `docs/OPERACAO-caprover.md`.

The gateway always runs on the machine that has the Meshtastic node plugged in (a Raspberry Pi at the base station, for example) and connects to the broker over TLS.

## Development

Requirements: Python 3.11+, Node 20+ with pnpm 9, Docker (for PostgreSQL in tests).

```bash
# gateway + ingest
cd services/rastro_gateway && pip install -e . pytest && pytest -q
# API (needs a PostgreSQL; see docs/DESENVOLVIMENTO.md)
cd services/rastro_api && pip install -e ".[dev]" && pytest -q
# viewer
cd web && pnpm install --frozen-lockfile && pnpm test && pnpm build
```

Operator-facing strings and operations docs are in Portuguese; code identifiers are in English.

## License

GPL-3.0-only. The viewer is derived from [meshtastic/map](https://github.com/meshtastic/map) (GPL-3.0); see `web/NOTICE`.
