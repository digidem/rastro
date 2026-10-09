"""Testes da agenda diária (api/clima_agenda.py) e das consultas de clima (queries.py).

Sem rede e sem Postgres: o pool e as conexões são falsos. A conexão falsa recusa mudar
``read_only`` com transação aberta, como o psycopg. Coordenadas são fictícias
(lat -5.0, lon -70.0 e similares) e nunca aparecem em log nas asserções.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

import pytest

from rastro_api.api import queries
from rastro_api.api.clima_agenda import AgendaClima
from rastro_api.api.clima_config import BARCOS_PADRAO, BarcoClima, ConfigClima
from rastro_api.api.clima_previsao import URL_PADRAO, PrevisaoErro

LOG = "rastro_api.clima"
TZ = timezone(timedelta(hours=-5))
UTC = timezone.utc
PROIBIDOS = ("-5.0", "-70.0", "-4.5", "-69.5", "https://", "latitude")
DESDE = datetime(2026, 10, 9, 5, 0, tzinfo=UTC)  # meia-noite local (UTC-5)


@pytest.fixture(autouse=True)
def _log_info(caplog):
    caplog.set_level(logging.INFO, logger=LOG)


def em(h, m=0, s=0, dia=9):
    """Instante local (UTC-5) de 2026-10-dia."""
    return datetime(2026, 10, dia, h, m, s, tzinfo=TZ)


class _Relogio:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _Conn:
    """Conexão falsa de psycopg. Mudar read_only com transação aberta levanta RuntimeError."""

    def __init__(self, handler=None):
        self.handler = handler
        self.sql: list[tuple[str, object]] = []
        self.ro_nas_execucoes: list[bool] = []
        self._ro = True  # padrão do pool (default_transaction_read_only=on)
        self._em_tx = False
        self._aberta = False  # execute fora de transaction() abre transação implícita

    @property
    def read_only(self):
        return self._ro

    @read_only.setter
    def read_only(self, valor):
        if self._em_tx or self._aberta:
            raise RuntimeError("read_only com transação aberta")
        self._ro = valor

    def transaction(self):
        conn = self

        class _Tx:
            def __enter__(self_):
                conn._em_tx = True
                return conn

            def __exit__(self_, *exc):
                conn._em_tx = False
                conn._aberta = False
                return False

        return _Tx()

    def execute(self, query, params=None):
        if not self._em_tx:
            self._aberta = True
        self.sql.append((query, params))
        self.ro_nas_execucoes.append(self._ro)
        rows = self.handler(query, params) if self.handler else []
        return _Cursor(rows)


class _Banco:
    """Estado compartilhado do 'Postgres' falso: posições, outbox e log das consultas."""

    def __init__(self, relogio):
        self.relogio = relogio
        self.posicoes: dict[str, tuple[float, float, timedelta]] = {}  # boat -> (lat, lon, idade)
        self.outbox: list[dict] = []
        self.consultas: list[tuple[str, object]] = []

    def responder(self, query, params):
        self.consultas.append((query, params))
        if "vw_ultima_posicao" in query:
            boat = params[0]
            if boat not in self.posicoes:
                return []
            lat, lon, idade = self.posicoes[boat]
            return [{"lat": lat, "lon": lon, "pos_time": self.relogio() - idade}]
        if "boat_id = %s AND created_by = 'clima' AND created_at >= %s" in query:
            boat, desde = params
            achou = any(
                r["boat_id"] == boat and r["created_at"] >= desde for r in self.outbox
            )
            return [{"enfileirado": achou}]
        if "make_interval" in query:
            (intervalo,) = params
            ultimo = max((r["created_at"] for r in self.outbox), default=None)
            cedo = ultimo is not None and ultimo > self.relogio() - timedelta(seconds=intervalo)
            return [{"cedo": cedo}]
        if "INSERT INTO chat_outbox" in query:
            boat, texto, expira = params
            novo_id = len(self.outbox) + 1
            self.outbox.append(
                {
                    "id": novo_id,
                    "boat_id": boat,
                    "texto": texto,
                    "expires_at": expira,
                    "created_at": self.relogio(),
                }
            )
            return [{"id": novo_id}]
        if "pg_advisory_xact_lock" in query:
            return []
        raise AssertionError("consulta inesperada na agenda")


class _Pool:
    def __init__(self, banco: _Banco):
        self.banco = banco
        self.abertas = 0

    def connection(self):
        pool = self

        class _Ctx:
            def __enter__(ctx):
                pool.abertas += 1
                ctx.conn = _Conn(handler=pool.banco.responder)
                return ctx.conn

            def __exit__(ctx, *exc):
                pool.abertas -= 1
                # Devolvida ao pool: a transação termina e o estado é zerado.
                ctx.conn._aberta = False
                ctx.conn._em_tx = False
                return False

        return _Ctx()


class _Previsao:
    """Substituto de ``clima_previsao.buscar``: registra chamadas e pode falhar ou agir antes."""

    def __init__(self, dados=None, erro=None, antes=None):
        self.dados = dados if dados is not None else _dados()
        self.erro = erro
        self.antes = antes
        self.chamadas: list[tuple[float, float, str | None]] = []
        self.abertas_no_http: list[int] = []
        self.pool: _Pool | None = None

    def __call__(self, lat, lon, base=None):
        self.chamadas.append((lat, lon, base))
        if self.pool is not None:
            self.abertas_no_http.append(self.pool.abertas)
        if self.antes is not None:
            self.antes()
        if self.erro is not None:
            raise self.erro
        return self.dados


def _dados(chuva_mm=0.0, rajada=10):
    horas = [f"2026-10-09T{h:02d}:00" for h in range(24)]
    return {
        "daily": {
            "temperature_2m_min": [22.0],
            "temperature_2m_max": [33.0],
            "precipitation_sum": [chuva_mm],
        },
        "hourly": {
            "time": horas,
            "precipitation_probability": [0] * 24,
            "weather_code": [1] * 24,
            "wind_speed_10m": [10] * 24,
            "wind_gusts_10m": [rajada] * 24,
        },
    }


def _cfg(barcos=BARCOS_PADRAO, hora=8, minuto=0, intervalo_s=60):
    return ConfigClima(
        ativo=True,
        barcos=tuple(barcos),
        hora=hora,
        minuto=minuto,
        intervalo_s=intervalo_s,
        ttl_h=6,
        max_idade_h=48,
        utc_offset_h=-5,
        api_url=URL_PADRAO,
    )


def _banco_cheio(relogio, idade=timedelta(hours=1)):
    banco = _Banco(relogio)
    for i, barco in enumerate(BARCOS_PADRAO):
        banco.posicoes[barco.boat_id] = (-5.0 - 0.1 * i, -70.0, idade)
    return banco


def _agenda(banco, relogio, *, cfg=None, previsao=None, parar=None):
    pool = _Pool(banco)
    previsao = previsao if previsao is not None else _Previsao()
    previsao.pool = pool
    ag = AgendaClima(pool, cfg or _cfg(), buscar=previsao, agora=relogio, parar=parar)
    return ag, previsao


def _msgs(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOG]


def _um_barco(boat_id="itui-1", regional="Ituí", reserva=None):
    return _cfg(barcos=(BarcoClima(boat_id, regional, reserva),))


# --- Agenda ---------------------------------------------------------------------------


def test_antes_das_8h_nao_faz_nada():
    relogio = _Relogio(em(7, 59))
    banco = _banco_cheio(relogio)
    ag, prev = _agenda(banco, relogio)
    assert ag.rodar_uma_vez() == 0
    assert prev.chamadas == []
    assert banco.outbox == []


def test_0800_envia_so_o_barco_0_e_busca_fora_de_conexao(caplog):
    relogio = _Relogio(em(8, 0))
    banco = _banco_cheio(relogio)
    ag, prev = _agenda(banco, relogio)
    assert ag.rodar_uma_vez() == 1
    assert len(prev.chamadas) == 1
    lat, lon, base = prev.chamadas[0]
    assert (lat, lon) == (-5.0, -70.0)
    assert base == URL_PADRAO
    assert prev.abertas_no_http == [0]  # nenhuma conexão do pool aberta durante o HTTP
    assert len(banco.outbox) == 1
    linha = banco.outbox[0]
    assert linha["boat_id"] == "itui-1"
    assert linha["expires_at"] == em(14)  # slot 08:00 + 6 h
    assert linha["texto"].startswith("Ituí – 09/10 08h | Hoje: 22–33°C.")
    # Mesmo minuto de novo: o barco 0 já saiu e o barco 1 ainda não tem slot.
    assert ag.rodar_uma_vez() == 0
    assert len(prev.chamadas) == 1
    assert not [m for m in _msgs(caplog) if any(p in m for p in PROIBIDOS)]


def test_0801_envia_o_barco_1_com_expira_proprio():
    relogio = _Relogio(em(8, 0))
    banco = _banco_cheio(relogio)
    ag, _ = _agenda(banco, relogio)
    assert ag.rodar_uma_vez() == 1
    relogio.t = em(8, 1)
    assert ag.rodar_uma_vez() == 1
    assert [r["boat_id"] for r in banco.outbox] == ["itui-1", "itaquai-1"]
    assert banco.outbox[1]["expires_at"] == em(14, 1)


def test_0810_com_os_cinco_atrasados_envia_um_por_chamada():
    relogio = _Relogio(em(8, 10))
    banco = _banco_cheio(relogio)
    ag, _ = _agenda(banco, relogio)
    assert ag.rodar_uma_vez() == 1
    assert ag.rodar_uma_vez() == 0  # mesmo minuto: espaçamento de 1 min segura o resto
    assert len(banco.outbox) == 1
    for minuto in range(11, 15):
        relogio.t = em(8, minuto)
        assert ag.rodar_uma_vez() == 1
    assert [r["boat_id"] for r in banco.outbox] == [b.boat_id for b in BARCOS_PADRAO]
    relogio.t = em(8, 15)
    assert ag.rodar_uma_vez() == 0


def _banco_com_barcos_0_a_3_enviados(relogio):
    banco = _banco_cheio(relogio)
    for i in range(4):
        banco.outbox.append(
            {
                "id": i + 1,
                "boat_id": BARCOS_PADRAO[i].boat_id,
                "texto": "x",
                "expires_at": em(14),
                "created_at": em(8, i),
            }
        )
    return banco


def test_barco_4_ainda_sai_as_1403_59():
    relogio = _Relogio(em(14, 3, 59))
    banco = _banco_com_barcos_0_a_3_enviados(relogio)
    ag, prev = _agenda(banco, relogio)
    assert ag.rodar_uma_vez() == 1
    assert banco.outbox[-1]["boat_id"] == "jaquirana-1"
    assert len(prev.chamadas) == 1


def test_barco_4_nao_sai_as_1404():
    relogio = _Relogio(em(14, 4, 0))
    banco = _banco_com_barcos_0_a_3_enviados(relogio)
    ag, prev = _agenda(banco, relogio)
    assert ag.rodar_uma_vez() == 0
    assert prev.chamadas == []
    assert len(banco.outbox) == 4


def test_ja_enfileirado_hoje_nao_chama_busca():
    relogio = _Relogio(em(8, 0, 30))
    banco = _banco_cheio(relogio)
    banco.outbox.append(
        {"id": 1, "boat_id": "itui-1", "texto": "x", "expires_at": em(14), "created_at": em(8)}
    )
    ag, prev = _agenda(banco, relogio)
    assert ag.rodar_uma_vez() == 0
    assert prev.chamadas == []


def test_leitura_usa_meia_noite_local_como_desde():
    relogio = _Relogio(em(8, 0))
    banco = _banco_cheio(relogio)
    ag, _ = _agenda(banco, relogio)
    ag.rodar_uma_vez()
    consultas = [p for q, p in banco.consultas if "boat_id = %s AND created_by = 'clima'" in q]
    assert consultas[0] == ("itui-1", DESDE)


def test_fix_velho_usa_a_reserva():
    relogio = _Relogio(em(8, 0))
    banco = _Banco(relogio)
    banco.posicoes["itui-1"] = (-5.0, -70.0, timedelta(hours=49))
    ag, prev = _agenda(banco, relogio, cfg=_um_barco(reserva=(-4.5, -69.5)))
    assert ag.rodar_uma_vez() == 1
    assert prev.chamadas[0][:2] == (-4.5, -69.5)


def test_fix_velho_sem_reserva_pula_o_dia_sem_logar_coordenadas_e_retenta_no_dia_seguinte(caplog):
    relogio = _Relogio(em(8, 0, dia=9))
    banco = _Banco(relogio)
    banco.posicoes["itui-1"] = (-5.0, -70.0, timedelta(hours=49))
    ag, prev = _agenda(banco, relogio, cfg=_um_barco())
    assert ag.rodar_uma_vez() == 0
    relogio.t = em(8, 5, dia=9)
    assert ag.rodar_uma_vez() == 0  # pulado hoje: nem tenta
    assert prev.chamadas == []
    relogio.t = em(8, 0, dia=10)
    assert ag.rodar_uma_vez() == 0  # dia novo: tenta de novo, ainda sem fix
    assert prev.chamadas == []
    avisos = [m for m in _msgs(caplog) if "sem posição recente" in m]
    assert len(avisos) == 2
    assert not [m for m in _msgs(caplog) if any(p in m for p in PROIBIDOS)]
    banco.posicoes["itui-1"] = (-5.0, -70.0, timedelta(hours=1))
    relogio.t = em(8, 0, dia=11)
    assert ag.rodar_uma_vez() == 1
    assert len(prev.chamadas) == 1


def test_previsao_erro_espera_5_minutos(caplog):
    relogio = _Relogio(em(8, 0))
    banco = _banco_cheio(relogio)
    previsao = _Previsao(erro=PrevisaoErro("Open-Meteo: HTTP 503"))
    ag, prev = _agenda(banco, relogio, cfg=_um_barco(), previsao=previsao)
    assert ag.rodar_uma_vez() == 0
    relogio.t = em(8, 4)
    assert ag.rodar_uma_vez() == 0
    assert len(prev.chamadas) == 1  # ainda dentro dos 5 min
    relogio.t = em(8, 5)
    assert ag.rodar_uma_vez() == 0
    assert len(prev.chamadas) == 2
    assert any("Open-Meteo: HTTP 503" in m for m in _msgs(caplog))


def test_erro_generico_loga_so_o_tipo(caplog):
    relogio = _Relogio(em(8, 0))
    banco = _banco_cheio(relogio)
    erro = RuntimeError(
        "falha em https://api.open-meteo.com/v1/forecast?latitude=-5.0&longitude=-70.0"
    )
    ag, _ = _agenda(banco, relogio, cfg=_um_barco(), previsao=_Previsao(erro=erro))
    assert ag.rodar_uma_vez() == 0
    msgs = _msgs(caplog)
    assert any("RuntimeError" in m for m in msgs)
    assert not [m for m in msgs if any(p in m for p in PROIBIDOS)]


def test_parar_durante_a_busca_nao_escreve_nada():
    relogio = _Relogio(em(8, 0))
    banco = _banco_cheio(relogio)
    parar = threading.Event()
    previsao = _Previsao(antes=parar.set)
    ag, prev = _agenda(banco, relogio, previsao=previsao, parar=parar)
    assert ag.rodar_uma_vez() == 0
    assert len(prev.chamadas) == 1
    assert banco.outbox == []


def test_parar_antes_da_passada_nao_busca_nada():
    relogio = _Relogio(em(8, 0))
    banco = _banco_cheio(relogio)
    parar = threading.Event()
    parar.set()
    ag, prev = _agenda(banco, relogio, parar=parar)
    assert ag.rodar_uma_vez() == 0
    assert prev.chamadas == []
    assert banco.consultas == []


def test_busca_que_atravessa_a_meia_noite_nao_escreve():
    relogio = _Relogio(em(23, 59, 50))
    banco = _banco_cheio(relogio)
    previsao = _Previsao(antes=lambda: setattr(relogio, "t", em(0, 0, 10, dia=10)))
    ag, prev = _agenda(banco, relogio, cfg=_cfg(barcos=(BARCOS_PADRAO[0],), hora=23, minuto=59), previsao=previsao)
    assert ag.rodar_uma_vez() == 0
    assert len(prev.chamadas) == 1
    assert banco.outbox == []


def test_enfileirar_none_retorna_zero_sem_erro(monkeypatch, caplog):
    monkeypatch.setattr(queries, "enfileirar_clima", lambda *a, **k: None)
    relogio = _Relogio(em(8, 0))
    banco = _banco_cheio(relogio)
    ag, prev = _agenda(banco, relogio)
    assert ag.rodar_uma_vez() == 0
    assert len(prev.chamadas) == 1
    erros = [r for r in caplog.records if r.name == LOG and r.levelno >= logging.ERROR]
    assert erros == []


def test_duas_instancias_nunca_inserem_com_menos_de_um_minuto():
    relogio = _Relogio(em(8, 5))
    banco = _banco_cheio(relogio)
    ag_b, _ = _agenda(banco, relogio)
    # A perde a corrida: enquanto A busca o HTTP, B enfileira o barco 0.
    ag_a, _ = _agenda(banco, relogio, previsao=_Previsao(antes=lambda: ag_b.rodar_uma_vez()))
    assert ag_a.rodar_uma_vez() == 0
    assert [r["boat_id"] for r in banco.outbox] == ["itui-1"]

    for minuto in range(6, 16):
        relogio.t = em(8, minuto)
        ag_a.rodar_uma_vez()
        ag_b.rodar_uma_vez()
    instantes = [r["created_at"] for r in banco.outbox]
    assert len(instantes) == 5
    assert all(b - a >= timedelta(seconds=60) for a, b in zip(instantes, instantes[1:]))


def test_iniciar_sobe_thread_daemon_e_para_com_parar():
    relogio = _Relogio(em(7, 0))
    banco = _banco_cheio(relogio)
    ag, _ = _agenda(banco, relogio)
    thread = ag.iniciar(passo_s=0.01)
    assert thread.daemon and thread.name == "rastro-clima"
    ag.parar.set()
    thread.join(timeout=5)
    assert not thread.is_alive()


# --- Consultas (queries.py), com conexão roteirizada ----------------------------------


def _rotas(respostas: dict):
    """Handler: para cada trecho de SQL, devolve as linhas (ou levanta, se for exceção)."""

    def handler(query, params):
        for trecho, resultado in respostas.items():
            if trecho in query:
                if isinstance(resultado, Exception):
                    raise resultado
                return resultado
        return []

    return handler


_TRECHO_JA = "boat_id = %s AND created_by = 'clima' AND created_at >= %s"


def _rotas_clima(enfileirado=False, cedo=False, id_=42, erro=None):
    return {
        "pg_advisory_xact_lock": [],
        _TRECHO_JA: [{"enfileirado": enfileirado}],
        "make_interval": [{"cedo": cedo}],
        "INSERT INTO chat_outbox": erro if erro is not None else [{"id": id_}],
    }


def test_enfileirar_clima_ordem_lock_checagens_insert():
    conn = _Conn(handler=_rotas(_rotas_clima(id_=42)))
    assert queries.enfileirar_clima(conn, "itui-1", "texto", em(14), DESDE, 60) == 42
    sqls = [q for q, _ in conn.sql]
    assert len(sqls) == 4
    assert "pg_advisory_xact_lock(hashtext('rastro-clima'))" in sqls[0]
    assert _TRECHO_JA in sqls[1]
    assert "make_interval" in sqls[2] and "clock_timestamp()" in sqls[2]
    assert "INSERT INTO chat_outbox" in sqls[3]
    assert conn.sql[1][1] == ("itui-1", DESDE)
    assert conn.sql[2][1] == (60,)
    assert conn.sql[3][1] == ("itui-1", "texto", em(14))
    assert conn.ro_nas_execucoes == [False] * 4  # READ WRITE durante a transação
    assert conn.read_only is True  # restaurado


def test_enfileirar_clima_nao_insere_se_ja_enviado_hoje():
    conn = _Conn(handler=_rotas(_rotas_clima(enfileirado=True)))
    assert queries.enfileirar_clima(conn, "itui-1", "texto", em(14), DESDE, 60) is None
    sqls = [q for q, _ in conn.sql]
    assert not [q for q in sqls if "INSERT" in q]
    assert not [q for q in sqls if "make_interval" in q]


def test_enfileirar_clima_nao_insere_se_cedo():
    conn = _Conn(handler=_rotas(_rotas_clima(cedo=True)))
    assert queries.enfileirar_clima(conn, "itui-1", "texto", em(14), DESDE, 60) is None
    assert not [q for q, _ in conn.sql if "INSERT" in q]


def test_enfileirar_clima_tabela_vazia_ou_espacado_insere():
    # Em tabela vazia o coalesce do SQL devolve false, então cedo=False.
    conn = _Conn(handler=_rotas(_rotas_clima(cedo=False, id_=7)))
    assert queries.enfileirar_clima(conn, "itui-1", "texto", em(14), DESDE, 60) == 7


def test_enfileirar_clima_restaura_read_only_quando_execute_falha():
    conn = _Conn(handler=_rotas(_rotas_clima(erro=RuntimeError("boom"))))
    with pytest.raises(RuntimeError):
        queries.enfileirar_clima(conn, "itui-1", "texto", em(14), DESDE, 60)
    assert conn.read_only is True


def test_clima_ja_enfileirado_so_le():
    conn = _Conn(handler=_rotas({_TRECHO_JA: [{"enfileirado": True}]}))
    assert queries.clima_ja_enfileirado(conn, "itui-1", DESDE) is True
    assert conn.read_only is True
    assert conn.ro_nas_execucoes == [True]


def test_ultima_posicao_barco_devolve_linha_ou_none():
    linha = {"lat": -5.0, "lon": -70.0, "pos_time": em(8)}
    conn = _Conn(handler=_rotas({"vw_ultima_posicao": [linha]}))
    assert queries.ultima_posicao_barco(conn, "itui-1") == linha
    assert conn.sql[0][1] == ("itui-1",)
    assert queries.ultima_posicao_barco(_Conn(handler=_rotas({})), "itui-1") is None


def test_conexao_falsa_recusa_read_only_com_transacao_aberta():
    conn = _Conn(handler=_rotas({}))
    conn.execute("SELECT 1")
    with pytest.raises(RuntimeError):
        conn.read_only = False
