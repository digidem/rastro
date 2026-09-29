"""API FastAPI somente-leitura sobre o Postgres da malha Rastro (Fase 7).

Somente leitura: nenhuma rota escreve no banco e o pool abre conexões com
``default_transaction_read_only=on`` — o próprio servidor recusaria escrita.
Autenticação: Bearer (RASTRO_API_TOKEN) ou cookie de sessão HttpOnly assinado
com HMAC derivado do token; ``RASTRO_API_AUTH=desativada`` dispensa credencial
somente para Hosts locais (a lista não pode ficar vazia nesse modo). Única
rota aberta sem credencial: /api/healthz. Atrás do proxy reverso,
``RASTRO_REQUIRE_HTTPS=1`` exige ``X-Forwarded-Proto: https`` em todo /api
(exceto /api/healthz) — 403 aplicado antes de qualquer autenticação.
Trilhas de monitores são sensíveis: nunca logar coordenadas nem credenciais;
erro de banco vira 503 genérico, sem detalhes internos no response.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import psycopg
from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from psycopg.rows import dict_row

try:  # psycopg >= 3.2: pool embutido; versões antigas usam o pacote psycopg_pool
    from psycopg.pool import ConnectionPool, PoolTimeout
except ImportError:  # pragma: no cover
    from psycopg_pool import ConnectionPool, PoolTimeout  # type: ignore[no-redef]

from rastro_api.api import geojson, queries
from rastro_api.api.osm import OsmTiles, TileIndisponivel

LOGGER = logging.getLogger("rastro_api")

_MAX_LIMIT = 2000
_DB_ERRORS = (psycopg.Error, PoolTimeout)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


_CONN_FILE_PADRAO = "/rastro-pgconn/conn.env"
_CONN_WAIT_PADRAO_SECS = 300.0
_CONN_WAIT_POLL_SECS = 5.0


def _conn_file_path() -> str:
    return os.environ.get("RASTRO_PG_CONN_FILE") or _CONN_FILE_PADRAO


def _ler_conn_file(caminho: str) -> dict[str, str]:
    """``KEY=VALUE`` do conn.env publicado pelo app -setup (HOST/PORT/SSLMODE).

    Ausente/ilegível → ``{}``. Só essas 3 chaves: nunca lê senha do arquivo.
    """
    try:
        with open(caminho, encoding="utf-8") as fh:
            linhas = fh.read().splitlines()
    except OSError:
        return {}
    out: dict[str, str] = {}
    for linha in linhas:
        chave, sep, valor = linha.strip().partition("=")
        if sep and chave.strip() in ("RASTRO_PG_HOST", "RASTRO_PG_PORT", "RASTRO_PG_SSLMODE"):
            out[chave.strip()] = valor.strip()
    return out


def _pg_conn() -> tuple[str, str, str]:
    """(host, porta, sslmode): o ambiente vence; o conn.env só entra sem RASTRO_PG_HOST."""
    arq = {} if os.environ.get("RASTRO_PG_HOST") else _ler_conn_file(_conn_file_path())
    return (
        os.environ.get("RASTRO_PG_HOST") or arq.get("RASTRO_PG_HOST") or "localhost",
        os.environ.get("RASTRO_PG_PORT") or arq.get("RASTRO_PG_PORT") or "5432",
        os.environ.get("RASTRO_PG_SSLMODE") or arq.get("RASTRO_PG_SSLMODE") or "",
    )


def _aguardar_conn_file(*, sleep=time.sleep, monotonic=time.monotonic) -> bool:
    """Sem RASTRO_PG_HOST, espera o app -setup publicar o conn.env (poll 5 s)."""
    if os.environ.get("RASTRO_PG_HOST"):
        return True
    caminho = _conn_file_path()
    if os.path.isfile(caminho):
        return True
    espera = float(os.environ.get("RASTRO_PG_CONN_WAIT_SECS") or _CONN_WAIT_PADRAO_SECS)
    LOGGER.info("aguardando o preparo do Postgres publicar a conexão em %s", caminho)
    prazo = monotonic() + max(0.0, espera)
    while monotonic() < prazo:
        sleep(_CONN_WAIT_POLL_SECS)
        if os.path.isfile(caminho):
            return True
    return os.path.isfile(caminho)


def _pg_user() -> str:
    """Papel do Postgres: RASTRO_PG_USER, senão <banco>_viewer (nome do bootstrap)."""
    return os.environ.get("RASTRO_PG_USER") or f"{_env('RASTRO_PG_DB', 'rastro')}_viewer"


def _conninfo() -> str:
    """Conninfo SEM a senha — a senha vai por kwargs e nunca aparece em logs."""
    host, porta, sslmode = _pg_conn()
    info = (
        f"host={host}"
        f" port={porta}"
        f" dbname={_env('RASTRO_PG_DB', 'rastro')}"
        f" user={_pg_user()}"
    )
    return f"{info} sslmode={sslmode}" if sslmode else info


def _ping(conn: psycopg.Connection) -> None:
    conn.execute("SELECT 1").fetchone()


def _fetch(request: Request, fn, *args):
    """Executa ``fn(conn, *args)`` com conexão do pool; erro de banco → 503."""
    pool: ConnectionPool = request.app.state.pool
    try:
        with pool.connection() as conn:
            return fn(conn, *args)
    except _DB_ERRORS as exc:
        # log só com tipo da exceção: mensagem do driver pode ecoar valores
        LOGGER.error(
            "banco indisponível em %s %s (%s)",
            request.method, request.url.path, type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail="banco indisponível") from None


def _window(from_s: str | None, to_s: str | None) -> tuple[datetime, datetime]:
    """Janela temporal; default = últimas 24 h (ambos os lados independentes)."""
    def parse(bruto: str, name: str) -> datetime:
        try:
            # replace('Z') mantém compat com interpretadores < 3.11
            value = datetime.fromisoformat(bruto.strip().replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"parâmetro '{name}' inválido: use data ISO 8601",
            ) from None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    end = parse(to_s, "to") if to_s else datetime.now(timezone.utc)
    start = parse(from_s, "from") if from_s else end - timedelta(hours=24)
    return start, end


def create_app() -> FastAPI:
    modo = _env("RASTRO_API_AUTH", "token").strip().lower()
    if modo not in ("token", "desativada"):
        raise RuntimeError(
            "RASTRO_API_AUTH inválida: use 'token' (padrão) ou 'desativada'"
        )
    if modo == "token" and not _env("RASTRO_API_TOKEN", "").strip():
        raise RuntimeError(
            "RASTRO_API_TOKEN não definida: a API recusa subir sem token de acesso"
        )
    hosts_locais = {
        h.strip().lower()
        for h in _env(
            "RASTRO_API_HOSTS_LOCAIS",
            "127.0.0.1:8081,localhost:8081,127.0.0.1:8080,"
            "localhost:8080,[::1]:8081,[::1]:8080",
        ).split(",")
        if h.strip()
    }
    if modo == "desativada":
        # _env devolve "" quando a variável está definida vazia (o default só
        # vale para variável AUSENTE): aqui isso é lista de Hosts VAZIA, e
        # desativar auth sem nenhum Host local seria abrir a API ao mundo.
        if not hosts_locais:
            raise RuntimeError(
                "RASTRO_API_AUTH=desativada exige RASTRO_API_HOSTS_LOCAIS não vazio"
            )
        LOGGER.warning(
            "AUTENTICAÇÃO DESATIVADA (RASTRO_API_AUTH=desativada) — credencial"
            " dispensada só para Hosts locais: %s",
            ", ".join(sorted(hosts_locais)),
        )
    token = os.environ.get("RASTRO_API_TOKEN", "")
    bruto_dias = _env("RASTRO_SESSAO_DIAS", "30").strip()
    if not re.fullmatch(r"-?[0-9]+", bruto_dias):
        raise RuntimeError("RASTRO_SESSAO_DIAS inválida: use um inteiro entre 1 e 90")
    dias = int(bruto_dias)
    if not 1 <= dias <= 90:
        raise RuntimeError("RASTRO_SESSAO_DIAS inválida: use um inteiro entre 1 e 90")
    level = os.environ.get("RASTRO_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO))
    require_https = (
        _env("RASTRO_REQUIRE_HTTPS", "").strip().lower() in ("1", "true", "yes")
    )
    LOGGER.info(
        "HTTPS obrigatório em /api (X-Forwarded-Proto): %s",
        "sim" if require_https else "não",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not _aguardar_conn_file():
            raise RuntimeError(
                "sem RASTRO_PG_HOST e o preparo do Postgres não publicou a conexão em "
                f"{_conn_file_path()} (RASTRO_PG_CONN_WAIT_SECS)"
            )
        pool = ConnectionPool(
            conninfo=_conninfo(),
            kwargs={
                "row_factory": dict_row,
                # defesa em profundidade: o servidor recusa qualquer escrita
                "options": "-c default_transaction_read_only=on",
                "password": os.environ.get("RASTRO_PG_PASSWORD", ""),
            },
            min_size=1,
            max_size=4,
            check=ConnectionPool.check_connection,
            open=False,
        )
        pool.open(wait=False)
        app.state.pool = pool
        LOGGER.info(
            "pool de conexões criado (host=%s porta=%s banco=%s usuário=%s)",
            _pg_conn()[0],
            _pg_conn()[1],
            _env("RASTRO_PG_DB", "rastro"),
            _pg_user(),
        )
        try:
            yield
        finally:
            pool.close()
            LOGGER.info("pool de conexões encerrado")

    def _is_local(request: Request) -> bool:
        """Sem credencial SÓ localmente (navegador do monitor no caddy local)."""
        if modo != "desativada":
            return False
        host = request.headers.get("host", "").strip().lower()
        if host not in hosts_locais:
            return False
        # falha fechada: qualquer presença do header conta como remoto,
        # mesmo com valor vazio (o caddy o injeta em acesso externo)
        return request.headers.get("x-rastro-via") is None

    def _session_key() -> bytes:
        """Subchave HMAC derivada do token — rotação de token revoga cookies."""
        return hmac.new(token.encode(), b"rastro-sessao-v1", hashlib.sha256).digest()

    def _make_cookie(exp: int) -> str:
        """Valor do cookie: ``<exp>.<hmac-sha256(exp)>`` — exp no corpo e na tag."""
        return f"{exp}.{hmac.new(_session_key(), f'rastro-sessao-v1:{exp}'.encode(), hashlib.sha256).hexdigest()}"

    def _valid_bearer(authorization: str | None) -> bool:
        """Credencial Bearer válida; False se token vazio — antes de qualquer
        comparação (compare_digest com b"" autenticaria um Bearer vazio)."""
        if not token:
            return False
        supplied = ""
        if authorization and authorization.startswith("Bearer "):
            supplied = authorization[len("Bearer "):].strip()
        return secrets.compare_digest(supplied.encode("utf-8"), token.encode("utf-8"))

    def _valid_cookie(valor: str | None) -> bool:
        """Cookie de sessão válido: formato, exp dentro da janela, assinatura."""
        if not token or not valor:
            return False
        exp_s, _, sig = valor.partition(".")
        # [0-9] ASCII: isdigit() aceita "²" e int() estouraria o limite do campo
        if not re.fullmatch(r"[0-9]{1,12}", exp_s) or len(sig) != 64:
            return False
        agora = int(time.time())
        exp = int(exp_s)
        if exp <= agora or exp > agora + dias * 86400 + 300:
            return False
        esperada = hmac.new(
            _session_key(), f"rastro-sessao-v1:{exp}".encode(), hashlib.sha256
        ).hexdigest()
        return secrets.compare_digest(sig.encode(), esperada.encode())

    def require_auth(
        request: Request,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
        rastro_sessao: Annotated[str | None, Cookie()] = None,
    ) -> None:
        """Autenticação da rota: Host local (modo desativada), Bearer ou cookie
        de sessão — 401 com mensagem estável em qualquer outro caso."""
        if _is_local(request):
            return
        if _valid_bearer(authorization) or _valid_cookie(rastro_sessao):
            return
        raise HTTPException(status_code=401, detail="token inválido")

    app = FastAPI(
        title="Rastro API",
        version="0.1.0",
        lifespan=lifespan,
        # menor superfície pública: sem docs/OpenAPI expostos (trilhas sensíveis)
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        """Toda resposta sem caches (trilhas sensíveis + sessão; OWASP)."""
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.middleware("http")
    async def exigir_https(request: Request, call_next):
        """HTTPS obrigatório (RASTRO_REQUIRE_HTTPS) atrás do proxy reverso:
        todo caminho que começa com /api — exceto exatamente /api/healthz —
        só passa com ``X-Forwarded-Proto: https`` (comparação case-insensitive;
        qualquer outro valor, vazio ou ausente é recusado — falha fechada).
        Registrado por último, roda PRIMEIRO (mais externo) e antes das
        dependências de rota: o 403 vem antes de qualquer autenticação."""
        if (
            require_https
            and request.url.path.startswith("/api")
            and request.url.path != "/api/healthz"
            and request.headers.get("x-forwarded-proto", "").strip().lower()
            != "https"
        ):
            return JSONResponse(
                status_code=403, content={"detail": "HTTPS obrigatório"}
            )
        return await call_next(request)

    router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

    @app.get("/api/healthz")
    def healthz(request: Request) -> dict:
        started = time.perf_counter()
        _fetch(request, _ping)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return {"status": "ok", "db_latency_ms": latency_ms}

    class SessaoCorpo(BaseModel):
        """Corpo do POST /api/auth/sessao — nada além do ``lembrar`` é lido."""
        lembrar: bool = False

    @app.get("/api/auth/estado")
    def estado_auth(request: Request) -> dict:
        """Estado da autenticação — público: o viewer decide se pede token."""
        authorization = request.headers.get("authorization")
        return {
            "exigida": not _is_local(request),
            "autenticado": _valid_bearer(authorization)
            or _valid_cookie(request.cookies.get("rastro_sessao")),
            "dias_lembrar": dias,
        }

    @app.post("/api/auth/sessao")
    async def criar_sessao(
        request: Request,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> Response:
        """Emite o cookie de sessão; exige JSON e Bearer válido."""
        if not request.headers.get("content-type", "").startswith("application/json"):
            raise HTTPException(
                status_code=415, detail="content-type deve ser application/json"
            )
        if not token:
            raise HTTPException(
                status_code=409,
                detail="autenticação por token não configurada",
            )
        if not _valid_bearer(authorization):
            raise HTTPException(status_code=401, detail="token inválido")
        try:
            corpo = SessaoCorpo.model_validate(await request.json())
        except (ValidationError, ValueError):
            raise HTTPException(status_code=422, detail="corpo inválido") from None
        agora = int(time.time())
        if corpo.lembrar:
            exp = agora + dias * 86400
            max_age: int | None = dias * 86400
        else:
            exp = agora + 12 * 3600  # sem "lembrar": sessão de navegador, ~12 h
            max_age = None
        resposta = Response(status_code=204)
        resposta.set_cookie(
            "rastro_sessao",
            _make_cookie(exp),
            max_age=max_age,
            httponly=True,
            samesite="strict",
            path="/api",
            # Secure sem condição: a API pública só serve por HTTPS (proxy
            # reverso) — nunca emitir cookie de sessão trafegando em claro.
            secure=True,
        )
        return resposta

    @app.delete("/api/auth/sessao")
    def apagar_sessao() -> Response:
        """Logout: apaga o cookie (a revogação real é a rotação do token)."""
        resposta = Response(status_code=204)
        resposta.delete_cookie(
            "rastro_sessao", path="/api", httponly=True, samesite="strict"
        )
        return resposta

    osm = OsmTiles.from_env()  # None = desligado (RASTRO_OSM_TILES=0)

    @router.get("/osm/{z}/{x}/{y}.png")
    def osm_tile(z: int, x: int, y: int) -> Response:
        """Tile do basemap OSM via proxy (só com auth; IP do monitor não chega ao OSM).
        Nunca loga z/x/y: a área observada é dado sensível."""
        if osm is None:
            raise HTTPException(status_code=404, detail="mapa base desativado")
        if not osm.valido(z, x, y):
            raise HTTPException(status_code=404, detail="tile fora do intervalo")
        try:
            dados = osm.get(z, x, y)
        except TileIndisponivel:
            raise HTTPException(
                status_code=502, detail="mapa base indisponível"
            ) from None
        # Cache-Control: no-store vem do middleware de /api (a área observada é
        # sensível); o cache útil é o da própria API (osm.OsmTiles).
        return Response(content=dados, media_type="image/png")

    @router.get("/nodes/latest")
    def latest(request: Request) -> dict:
        rows = _fetch(request, queries.latest_nodes)
        features = [
            geojson.point_feature(r["lon"], r["lat"], {
                "node_num": r["node_num"],
                "node_id": r["node_id"],
                "nome": r["nome"],
                "pos_time": geojson.iso_utc(r["pos_time"]),
                "time_source": r["time_source"],
                "altitude_m": r["altitude_m"],
                "sats": r["sats_in_view"],
                "battery": r["battery"],
                "received_at": geojson.iso_utc(r["received_at"]),
            })
            for r in rows
        ]
        return geojson.collection(features)

    @router.get("/nodes/{node}/track")
    def track(
        node: str,
        request: Request,
        from_: Annotated[str | None, Query(alias="from")] = None,
        to: str | None = None,
        limit: Annotated[int, Query(ge=1)] = _MAX_LIMIT,
    ) -> dict:
        ts_from, ts_to = _window(from_, to)
        rows = _fetch(request, queries.track, node, ts_from, ts_to, min(limit, _MAX_LIMIT))
        if not rows:
            # nó desconhecido OU sem fixes na janela: coleção vazia, nunca 404
            return geojson.collection([])
        node_meta = {
            "node": rows[0]["node_id"],
            "nome": rows[0]["nome"],
            "from": ts_from,
            "to": ts_to,
        }
        return geojson.collection(geojson.track_features(rows, node_meta))

    @router.get("/nodes/{node}/telemetry")
    def telemetry(
        node: str,
        request: Request,
        from_: Annotated[str | None, Query(alias="from")] = None,
        to: str | None = None,
        limit: Annotated[int, Query(ge=1)] = _MAX_LIMIT,
    ) -> dict:
        ts_from, ts_to = _window(from_, to)
        rows = _fetch(request, queries.telemetry, node, ts_from, ts_to, min(limit, _MAX_LIMIT))
        features = [
            geojson.telemetry_feature({
                "battery_level": r["battery_level"],
                "voltage": r["voltage"],
                "channel_util": r["channel_util"],
                "air_util_tx": r["air_util_tx"],
                "uptime_s": r["uptime_s"],
                "telem_time": geojson.iso_utc(r["telem_time"]),
            })
            for r in rows
        ]
        return geojson.collection(features)

    app.include_router(router)
    return app


app = create_app()
