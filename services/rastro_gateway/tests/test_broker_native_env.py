"""Testes de provisionamento nativo de broker via variáveis de ambiente (WP-C / H2).

Testa:
- Vetores dourados (golden vectors) compartilhados para vgw e senhas de nós
- Formato RASTRO_NATIVE_NODES e barcos distintos em ordem de aparição
- Construção de contas a partir do ambiente e geração de passwd/ACL
- Sem vazamento de segredos em logs e saídas
- Comportamento inalterado no modo legado
- Rejeição de segredos e senhas inválidas
- Utilitário CLI scripts/rastro_node_credentials.py
- Dry-run do entrypoint.sh nos modos nativo e legado
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BROKER_DIR = _REPO_ROOT / "broker"
_DERIVE_PY = _BROKER_DIR / "derive.py"
_ACCOUNTS_PY = _BROKER_DIR / "accounts.py"
_ENTRYPOINT_SH = _BROKER_DIR / "entrypoint.sh"
_CREDENTIALS_PY = _REPO_ROOT / "scripts" / "rastro_node_credentials.py"

assert _DERIVE_PY.is_file(), f"Arquivo não encontrado: {_DERIVE_PY}"
assert _ACCOUNTS_PY.is_file(), f"Arquivo não encontrado: {_ACCOUNTS_PY}"

# Carrega broker/derive.py
spec_d = importlib.util.spec_from_file_location("broker_derive", _DERIVE_PY)
assert spec_d and spec_d.loader
derive_mod = importlib.util.module_from_spec(spec_d)
spec_d.loader.exec_module(derive_mod)

# Carrega broker/accounts.py
spec_a = importlib.util.spec_from_file_location("broker_accounts", _ACCOUNTS_PY)
assert spec_a and spec_a.loader
accounts_mod = importlib.util.module_from_spec(spec_a)
spec_a.loader.exec_module(accounts_mod)


# ============================================================================
# 1. Vetores Dourados (Golden Vectors) - algoritmo compartilhado com o gateway
# ============================================================================

def test_golden_vectors_virtual_gateway():
    """Valida derivação de virtual_node_num e virtual_gateway_id com vetores dourados."""
    # Constantes calculadas exatamente com a especificação:
    # n = (int.from_bytes(sha256(b"rastro-vgw:" + b.encode()).digest()[:4], "big") | 0xE0000000) & 0xFFFFFFFE
    # virtual_node_num = n; virtual gateway id = "!%08x" % n
    golden = [
        ("b1", 3825633914, "!e4068a7a"),
        ("b2", 4264915960, "!fe3573f8"),
        ("univaja-3", 4093112930, "!f3f7f262"),
        ("barco-alpha", 4209551180, "!fae8a74c"),
        ("b10", 4186399432, "!f98762c8"),
    ]

    for boat, exp_num, exp_gw_id in golden:
        num, gw_id = derive_mod.derive_virtual_node(boat)
        assert num == exp_num, f"Falha no virtual_node_num para barco {boat}"
        assert gw_id == exp_gw_id, f"Falha no gateway_id para barco {boat}"
        # Propriedades invariantes do algoritmo:
        assert (num & 0xE0000000) == 0xE0000000, "Bits mais significativos devem ser 0xE0000000"
        assert (num & 1) == 0, "O bit menos significativo deve ser 0 (número par)"
        assert gw_id == f"!{num:08x}"


def test_golden_vectors_node_password():
    """Valida derivação de senhas de nós com HMAC-SHA256 e Base64 urlsafe."""
    # Algoritmo:
    # base64.urlsafe_b64encode(hmac.new(secret.encode(), b"node:" + u.encode(), sha256).digest()).decode().rstrip("=")[:32]
    # Vetores idênticos aos de test_native_derive.py (H1):
    h1_golden = [
        ("teste-secreto-h1", "!a0000001", "eIugm-YbMQVV6CCvwqSrIkRH4rJlVq2y"),
        ("teste-secreto-h1", "!a0000002", "SgArIutL4UOvk5TWmbDhdexP-M0RuiMj"),
    ]
    for segredo, usuario, esperado in h1_golden:
        pw = derive_mod.derive_node_password(segredo, usuario)
        assert pw == esperado
        assert len(pw) == 32

    secret1 = "chave-secreta-para-testes-com-mais-de-24-caracteres"
    golden1 = [
        ("!a0000001", "3l2iFDbzOJ5tq9bnS_4zk8YQ6DK9dnG_"),
        ("!a0000002", "ai82R-XDp37IaEsOWHxOQB3UcZtTZOEq"),
        ("!b1234567", "yJoscdxrQWqpaxKPiA7g0SKvwni9WETc"),
    ]
    for user, exp_pw in golden1:
        pw = derive_mod.derive_node_password(secret1, user)
        assert pw == exp_pw
        assert len(pw) == 32
        assert ":" not in pw
        assert "\n" not in pw

    secret2 = "secret-super-secreta-para-testes-24ch"
    golden2 = [
        ("!a0000001", "l12GH5waJ2ndMTDbAxRuPDn1W12utbXx"),
        ("!a0000002", "KyZwh9fBvwzEPge5tkmWhZPed5bKdDWs"),
    ]
    for user, exp_pw in golden2:
        pw = derive_mod.derive_node_password(secret2, user)
        assert pw == exp_pw
        assert len(pw) == 32


# ============================================================================
# 2. Formato RASTRO_NATIVE_NODES e extração de barcos
# ============================================================================

def test_parse_native_nodes_e_barcos_distintos():
    """Valida parsing de nós nativos e preservação de ordem de barcos distintos."""
    nodes_str = "!a0000001=b1,!a0000002=b2,!a0000003=b1,!a0000004=b3"
    pairs = derive_mod.parse_native_nodes(nodes_str)
    assert pairs == [
        ("!a0000001", "b1"),
        ("!a0000002", "b2"),
        ("!a0000003", "b1"),
        ("!a0000004", "b3"),
    ]

    boats = derive_mod.extract_distinct_boats(pairs)
    assert boats == ["b1", "b2", "b3"], "Ordem de primeira aparição deve ser preservada"


def test_parse_native_nodes_tolera_vazios():
    """Valida que entradas vazias ou com espaços retornam lista vazia."""
    assert derive_mod.parse_native_nodes("") == []
    assert derive_mod.parse_native_nodes(None) == []
    assert derive_mod.parse_native_nodes("   ") == []
    assert derive_mod.parse_native_nodes(" , , ") == []


@pytest.mark.parametrize(
    "bad_str",
    [
        "!a0000001",           # Sem '='
        "!a0000001=b1=extra",  # Múltiplos '='
        "=b1",                 # Usuário vazio
        "!a0000001=",          # Barco vazio
        "!a0000001=b1,!a0000001=b2",  # Nó duplicado
        "user with space=b1",  # Caracteres inválidos
    ],
)
def test_parse_native_nodes_invalido_rejeitado(bad_str):
    with pytest.raises(ValueError):
        derive_mod.parse_native_nodes(bad_str)


# ============================================================================
# 3. Construção de contas a partir do ambiente e geração de passwd / ACL
# ============================================================================

def test_build_accounts_from_env_e_geracao_arquivos():
    """Gera dicionário de contas a partir do ambiente e valida arquivos passwd e aclfile."""
    env = {
        "RASTRO_NATIVE_NODES": "!a0000001=b1,!a0000002=b2",
        "RASTRO_NATIVE_SECRET": "chave-secreta-para-testes-com-mais-de-24-caracteres",
        "RASTRO_MQTT_PASSWORD_INGEST": "password-ingest-min24chars",
        "RASTRO_MQTT_PASSWORD_OUTBOX": "password-outbox-min24chars",
        "RASTRO_MQTT_PASSWORD_GATEWAY": "password-gateway-min24chars",
        "RASTRO_NATIVE_ROOT": "univaja/mesh",
        "RASTRO_MQTT_TOPIC_PREFIX": "rastro",
    }

    accounts = derive_mod.build_accounts_from_env(env)
    assert accounts["root"] == "univaja/mesh"
    assert accounts["ingest"]["password"] == "password-ingest-min24chars"
    assert accounts["outbox"]["password"] == "password-outbox-min24chars"
    assert accounts["gateway"]["password"] == "password-gateway-min24chars"
    assert accounts["gateway"]["prefix"] == "rastro"

    # Virtual gateways derivados
    assert len(accounts["virtual_gateways"]) == 2
    assert accounts["virtual_gateways"][0] == {"boat": "b1", "gateway_id": "!e4068a7a"}
    assert accounts["virtual_gateways"][1] == {"boat": "b2", "gateway_id": "!fe3573f8"}

    # Nós com senhas derivadas
    assert len(accounts["nodes"]) == 2
    assert accounts["nodes"][0] == {
        "user": "!a0000001",
        "password": "3l2iFDbzOJ5tq9bnS_4zk8YQ6DK9dnG_",
        "boat": "b1",
    }
    assert accounts["nodes"][1] == {
        "user": "!a0000002",
        "password": "ai82R-XDp37IaEsOWHxOQB3UcZtTZOEq",
        "boat": "b2",
    }

    # Valida passwd
    passwd = accounts_mod.generate_passwd(accounts)
    assert "ingest:password-ingest-min24chars\n" in passwd
    assert "outbox:password-outbox-min24chars\n" in passwd
    assert "gateway:password-gateway-min24chars\n" in passwd
    assert "!a0000001:3l2iFDbzOJ5tq9bnS_4zk8YQ6DK9dnG_\n" in passwd
    assert "!a0000002:ai82R-XDp37IaEsOWHxOQB3UcZtTZOEq\n" in passwd

    # Valida ACL
    acl = accounts_mod.generate_acl(accounts)
    # Regra crítica: ingest deve ler TANTO o nativo quanto o legado
    assert "user ingest\n" in acl
    assert "topic read univaja/mesh/2/e/#\n" in acl
    assert "topic read rastro/positions/#\n" in acl
    assert "topic read rastro/telemetry/#\n" in acl
    assert "topic read rastro/status/#\n" in acl
    assert "topic write" not in acl.split("user ingest\n")[1].split("user outbox\n")[0]

    # Outbox escreve apenas nos gateways virtuais
    assert "user outbox\n" in acl
    assert "topic write univaja/mesh/2/e/EVU/!e4068a7a\n" in acl
    assert "topic write univaja/mesh/2/e/EVU/!fe3573f8\n" in acl

    # Gateway legado
    assert "user gateway\n" in acl
    assert "topic write rastro/positions/#\n" in acl

    # Nós: escrita em seu tópico e leitura no vgw do barco correspondente
    assert "user !a0000001\n" in acl
    assert "topic write univaja/mesh/2/e/+/!a0000001\n" in acl
    assert "topic read univaja/mesh/2/e/EVU/!e4068a7a\n" in acl
    # Isolamento entre barcos
    node1_block = acl.split("user !a0000001\n")[1].split("user !a0000002\n")[0]
    assert "!fe3573f8" not in node1_block

    assert "user !a0000002\n" in acl
    assert "topic write univaja/mesh/2/e/+/!a0000002\n" in acl
    assert "topic read univaja/mesh/2/e/EVU/!fe3573f8\n" in acl


def test_build_accounts_sem_gateway_mantem_leitura_legada_no_ingest():
    """Mesmo sem senha de gateway definida, o ingest lê os tópicos legados."""
    env = {
        "RASTRO_NATIVE_NODES": "!a0000001=b1",
        "RASTRO_NATIVE_SECRET": "chave-secreta-para-testes-com-mais-de-24-caracteres",
        "RASTRO_MQTT_PASSWORD_INGEST": "password-ingest-min24chars",
        "RASTRO_MQTT_PASSWORD_OUTBOX": "password-outbox-min24chars",
    }
    accounts = derive_mod.build_accounts_from_env(env)
    assert "gateway" not in accounts
    acl = accounts_mod.generate_acl(accounts)
    assert "topic read univaja/mesh/2/e/#\n" in acl
    assert "topic read rastro/positions/#\n" in acl
    assert "user gateway" not in acl


# ============================================================================
# 4. Rejeição de segredos e credenciais inválidas (sem expor segredos no erro)
# ============================================================================

def test_rejeicao_segredo_invalido():
    base = {
        "RASTRO_NATIVE_NODES": "!a0000001=b1",
        "RASTRO_NATIVE_SECRET": "chave-secreta-para-testes-com-mais-de-24-caracteres",
        "RASTRO_MQTT_PASSWORD_INGEST": "password-ingest-min24chars",
        "RASTRO_MQTT_PASSWORD_OUTBOX": "password-outbox-min24chars",
    }

    # Segredo muito curto (< 24)
    with pytest.raises(ValueError, match="mínimo 24 caracteres") as exc:
        derive_mod.build_accounts_from_env({**base, "RASTRO_NATIVE_SECRET": "curto"})
    assert "curto" not in str(exc.value)

    # Segredo ausente
    with pytest.raises(ValueError, match="RASTRO_NATIVE_SECRET não definida"):
        derive_mod.build_accounts_from_env({**base, "RASTRO_NATIVE_SECRET": ""})

    # Outbox password ausente ou curta
    with pytest.raises(ValueError, match="RASTRO_MQTT_PASSWORD_OUTBOX não definida"):
        derive_mod.build_accounts_from_env({**base, "RASTRO_MQTT_PASSWORD_OUTBOX": ""})
    with pytest.raises(ValueError, match="mínimo 24 caracteres"):
        derive_mod.build_accounts_from_env({**base, "RASTRO_MQTT_PASSWORD_OUTBOX": "curta"})

    # Ingest password ausente ou curta
    with pytest.raises(ValueError, match="RASTRO_MQTT_PASSWORD_INGEST não definida"):
        derive_mod.build_accounts_from_env({**base, "RASTRO_MQTT_PASSWORD_INGEST": ""})
    with pytest.raises(ValueError, match="mínimo 24 caracteres"):
        derive_mod.build_accounts_from_env({**base, "RASTRO_MQTT_PASSWORD_INGEST": "curta"})


# ============================================================================
# 5. Utilitário CLI scripts/rastro_node_credentials.py
# ============================================================================

def test_cli_rastro_node_credentials():
    secret = "chave-secreta-para-testes-com-mais-de-24-caracteres"

    # Teste --node único
    res = subprocess.run(
        [
            sys.executable,
            str(_CREDENTIALS_PY),
            "--secret",
            secret,
            "--node",
            "!a0000001",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "!a0000001 3l2iFDbzOJ5tq9bnS_4zk8YQ6DK9dnG_" in res.stdout
    assert "AVISO:" in res.stderr
    assert "secretas" in res.stderr

    # Teste --nodes múltiplos
    res2 = subprocess.run(
        [
            sys.executable,
            str(_CREDENTIALS_PY),
            "--secret",
            secret,
            "--nodes",
            "!a0000001=b1,!a0000002=b2",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "!a0000001 3l2iFDbzOJ5tq9bnS_4zk8YQ6DK9dnG_" in res2.stdout
    assert "!a0000002 ai82R-XDp37IaEsOWHxOQB3UcZtTZOEq" in res2.stdout

    # Teste via variável de ambiente RASTRO_NATIVE_SECRET
    env = {**os.environ, "RASTRO_NATIVE_SECRET": secret}
    res3 = subprocess.run(
        [
            sys.executable,
            str(_CREDENTIALS_PY),
            "--node",
            "!a0000001",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "!a0000001 3l2iFDbzOJ5tq9bnS_4zk8YQ6DK9dnG_" in res3.stdout

    # Rejeição de segredo curto
    res_bad = subprocess.run(
        [
            sys.executable,
            str(_CREDENTIALS_PY),
            "--secret",
            "curto",
            "--node",
            "!a0000001",
        ],
        capture_output=True,
        text=True,
    )
    assert res_bad.returncode != 0
    assert "ERRO:" in res_bad.stderr


# ============================================================================
# 6. Testes de Execução do entrypoint.sh (Dry-Run)
# ============================================================================

@pytest.fixture
def fake_mosquitto_env(tmp_path):
    """Cria ambiente isolado com mock do mosquitto_passwd e diretórios temporários."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    mock_mp = bin_dir / "mosquitto_passwd"
    # mock do mosquitto_passwd -U: não faz nada além de retornar 0
    mock_mp.write_text("#!/bin/sh\nexit 0\n")
    mock_mp.chmod(0o755)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    public_dir = tmp_path / "public"
    public_dir.mkdir()
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()

    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "RASTRO_BROKER_DRY_RUN": "1",
        "RASTRO_DATA_DIR": str(data_dir),
        "RASTRO_PUBLIC_DIR": str(public_dir),
        "RASTRO_SECRETS_DIR": str(secrets_dir),
        "RASTRO_TPL_DIR": str(_BROKER_DIR),
        "RASTRO_BROKER_SANS": "srv-captain--broker,broker.local",
    }
    return env


def test_entrypoint_native_env_dry_run(fake_mosquitto_env):
    """Valida dry-run do entrypoint.sh no modo nativo RASTRO_NATIVE_NODES."""
    secret = "chave-secreta-para-testes-com-mais-de-24-caracteres"
    env = {
        **fake_mosquitto_env,
        "RASTRO_NATIVE_NODES": "!a0000001=b1,!a0000002=b2",
        "RASTRO_NATIVE_SECRET": secret,
        "RASTRO_MQTT_PASSWORD_INGEST": "password-ingest-min24chars",
        "RASTRO_MQTT_PASSWORD_OUTBOX": "password-outbox-min24chars",
        "RASTRO_MQTT_PASSWORD_GATEWAY": "password-gateway-min24chars",
    }

    res = subprocess.run(
        ["/bin/sh", str(_ENTRYPOINT_SH)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"entrypoint falhou: stderr={res.stderr}"
    assert "OK: configuração gerada" in res.stdout
    # Garante que segredos NUNCA foram logados
    assert secret not in res.stdout
    assert secret not in res.stderr
    assert "password-ingest-min24chars" not in res.stdout
    assert "password-outbox-min24chars" not in res.stdout


def test_entrypoint_native_env_rejeita_segredo_invalido(fake_mosquitto_env):
    """Valida que segredo inválido é rejeitado pelo entrypoint.sh sem vazar o valor."""
    env = {
        **fake_mosquitto_env,
        "RASTRO_NATIVE_NODES": "!a0000001=b1",
        "RASTRO_NATIVE_SECRET": "segredo-curto",
        "RASTRO_MQTT_PASSWORD_INGEST": "password-ingest-min24chars",
        "RASTRO_MQTT_PASSWORD_OUTBOX": "password-outbox-min24chars",
    }
    res = subprocess.run(
        ["/bin/sh", str(_ENTRYPOINT_SH)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0
    assert "ERRO: RASTRO_NATIVE_SECRET muito curta" in res.stderr
    assert "segredo-curto" not in res.stderr


def test_entrypoint_legacy_mode_inalterado(fake_mosquitto_env):
    """Sem RASTRO_NATIVE_NODES, entrypoint opera em modo legado sem alterações."""
    env = {
        **fake_mosquitto_env,
        "RASTRO_MQTT_PASSWORD_GATEWAY": "password-gateway-min24chars",
        "RASTRO_MQTT_PASSWORD_INGEST": "password-ingest-min24chars",
    }
    res = subprocess.run(
        ["/bin/sh", str(_ENTRYPOINT_SH)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"entrypoint legado falhou: stderr={res.stderr}"
    assert "OK: configuração gerada" in res.stdout
