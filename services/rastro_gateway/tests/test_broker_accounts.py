"""Testes unitários de geração de contas e ACL do broker (WP-C).

Importa accounts.py a partir do diretório broker/ via importlib conforme especificado.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import pytest

# Carrega broker/accounts.py via importlib
_BROKER_DIR = Path(__file__).resolve().parents[3] / "broker"
_ACCOUNTS_PY = _BROKER_DIR / "accounts.py"

assert _ACCOUNTS_PY.is_file(), f"Arquivo não encontrado: {_ACCOUNTS_PY}"
spec = importlib.util.spec_from_file_location("broker_accounts", _ACCOUNTS_PY)
assert spec and spec.loader
accounts_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(accounts_mod)


def test_carrega_example_json_valido():
    """Valida que broker/accounts.example.json é sintaticamente correto e passa na validação."""
    example_path = _BROKER_DIR / "accounts.example.json"
    assert example_path.is_file()

    data = accounts_mod.load_accounts(example_path)
    assert data["root"] == "univaja/mesh"
    assert "ingest" in data
    assert "outbox" in data
    assert len(data["nodes"]) == 6
    assert len(data["virtual_gateways"]) == 6


def test_generate_passwd_e_acl():
    """Confere conteúdo e regras de isolamento no passwd e no aclfile gerados."""
    sample = {
        "root": "univaja/mesh",
        "ingest": {"password": "password-ingest-min24chars"},
        "outbox": {"password": "password-outbox-min24chars"},
        "gateway": {"password": "password-gateway-min24chars", "prefix": "rastro"},
        "virtual_gateways": [
            {"boat": "barco-1", "gateway_id": "!f0000001"},
            {"boat": "barco-2", "gateway_id": "!f0000002"},
        ],
        "nodes": [
            {"user": "!a0000001", "password": "password-node1-min24chars", "boat": "barco-1"},
            {"user": "!a0000002", "password": "password-node2-min24chars", "boat": "barco-2"},
        ],
    }

    passwd = accounts_mod.generate_passwd(sample)
    assert "ingest:password-ingest-min24chars\n" in passwd
    assert "outbox:password-outbox-min24chars\n" in passwd
    assert "gateway:password-gateway-min24chars\n" in passwd
    assert "!a0000001:password-node1-min24chars\n" in passwd
    assert "!a0000002:password-node2-min24chars\n" in passwd

    acl = accounts_mod.generate_acl(sample)
    # Proibição explícita de %u
    assert "%u" not in acl

    # Ingest: leitura em univaja/mesh/2/e/# e sem regra de write
    assert "user ingest\n" in acl
    assert "topic read univaja/mesh/2/e/#\n" in acl
    assert "topic write univaja/mesh/2/e/#" not in acl

    # Outbox: escrita apenas nos gateways virtuais
    assert "user outbox\n" in acl
    assert "topic write univaja/mesh/2/e/EVU/!f0000001\n" in acl
    assert "topic write univaja/mesh/2/e/EVU/!f0000002\n" in acl
    assert "topic read" not in acl.split("user outbox\n")[1].split("user gateway\n")[0]

    # Gateway legado
    assert "user gateway\n" in acl
    assert "topic write rastro/positions/#\n" in acl

    # Isolamento de nó: nó 1 só escreve em seu tópico de nó e só lê vgw do seu barco
    assert "user !a0000001\n" in acl
    assert "topic write univaja/mesh/2/e/+/!a0000001\n" in acl
    assert "topic read univaja/mesh/2/e/EVU/!f0000001\n" in acl
    node1_block = acl.split("user !a0000001\n")[1].split("user !a0000002\n")[0]
    assert "topic read univaja/mesh/2/e/EVU/!f0000002" not in node1_block
    assert "EVU/+" not in node1_block
    assert "PKI/+" not in node1_block

    # Nó 2
    assert "user !a0000002\n" in acl
    assert "topic write univaja/mesh/2/e/+/!a0000002\n" in acl
    assert "topic read univaja/mesh/2/e/EVU/!f0000002\n" in acl
    node2_block = acl.split("user !a0000002\n")[1]
    assert "topic read univaja/mesh/2/e/EVU/!f0000001" not in node2_block
    assert "EVU/+" not in node2_block
    assert "PKI/+" not in node2_block


def test_validacao_rejeita_barco_inexistente(tmp_path):
    """Nó associado a barco sem gateway virtual dispara ValueError."""
    bad_data = {
        "root": "univaja/mesh",
        "ingest": {"password": "password-ingest-min24chars"},
        "outbox": {"password": "password-outbox-min24chars"},
        "virtual_gateways": [{"boat": "barco-1", "gateway_id": "!f0000001"}],
        "nodes": [
            {"user": "!a0000001", "password": "password-node1-min24chars", "boat": "barco-fantasma"}
        ],
    }
    f = tmp_path / "accounts.json"
    f.write_text(json.dumps(bad_data))

    with pytest.raises(ValueError, match="não possui gateway virtual configurado"):
        accounts_mod.load_accounts(f)


def test_validacao_rejeita_usuario_duplicado(tmp_path):
    """Usuários repetidos entre contas disparam ValueError."""
    bad_data = {
        "root": "univaja/mesh",
        "ingest": {"password": "password-ingest-min24chars"},
        "outbox": {"password": "password-outbox-min24chars"},
        "virtual_gateways": [{"boat": "b1", "gateway_id": "!f0000001"}],
        "nodes": [
            {"user": "ingest", "password": "password-node1-min24chars", "boat": "b1"}
        ],
    }
    f = tmp_path / "accounts.json"
    f.write_text(json.dumps(bad_data))

    with pytest.raises(ValueError, match="Usuário duplicado"):
        accounts_mod.load_accounts(f)


def test_validacao_rejeita_senha_com_dois_pontos(tmp_path):
    """Senhas com ':' (delimitador do passwd) são rejeitadas."""
    bad_data = {
        "root": "univaja/mesh",
        "ingest": {"password": "senha:com:delimitador-longa-ok"},
        "outbox": {"password": "password-outbox-min24chars"},
        "virtual_gateways": [],
        "nodes": [],
    }
    f = tmp_path / "accounts.json"
    f.write_text(json.dumps(bad_data))

    with pytest.raises(ValueError, match="não pode conter ':'"):
        accounts_mod.load_accounts(f)


def test_validacao_rejeita_root_com_wildcard(tmp_path):
    """Prefixo raiz com wildcard (+ ou #) é rejeitado."""
    bad_data = {
        "root": "univaja/+",
        "ingest": {"password": "password-ingest-min24chars"},
        "outbox": {"password": "password-outbox-min24chars"},
        "virtual_gateways": [],
        "nodes": [],
    }
    f = tmp_path / "accounts.json"
    f.write_text(json.dumps(bad_data))

    with pytest.raises(ValueError, match="caracteres inválidos"):
        accounts_mod.load_accounts(f)


def test_generate_broker_files_grava_com_modo_0600(tmp_path):
    """generate_broker_files escreve arquivos válidos com permissão restrita 0600."""
    example_path = _BROKER_DIR / "accounts.example.json"
    passwd_file = tmp_path / "subdir" / "passwd"
    acl_file = tmp_path / "subdir" / "aclfile"

    accounts_mod.generate_broker_files(example_path, passwd_file, acl_file)

    assert passwd_file.is_file()
    assert acl_file.is_file()

    # Verifica permissões restritas (apenas dono lê e escreve: 0600)
    passwd_mode = stat.S_IMODE(passwd_file.stat().st_mode)
    acl_mode = stat.S_IMODE(acl_file.stat().st_mode)
    assert passwd_mode == 0o600
    assert acl_mode == 0o600

    content_passwd = passwd_file.read_text(encoding="utf-8")
    content_acl = acl_file.read_text(encoding="utf-8")

    assert "ingest:" in content_passwd
    assert "user ingest" in content_acl


def test_main_cli_sucesso_e_erros(tmp_path, monkeypatch, capsys):
    """Execução da CLI com argumentos e variáveis de ambiente."""
    example_path = _BROKER_DIR / "accounts.example.json"
    pw_out = tmp_path / "passwd"
    acl_out = tmp_path / "aclfile"

    # Argumentos posicionais
    rc = accounts_mod.main([str(example_path), str(pw_out), str(acl_out)])
    assert rc == 0
    assert pw_out.exists() and acl_out.exists()

    # Sem argumentos, via variáveis de ambiente
    pw_out2 = tmp_path / "passwd2"
    acl_out2 = tmp_path / "aclfile2"
    monkeypatch.setenv("RASTRO_ACCOUNTS_FILE", str(example_path))
    monkeypatch.setenv("RASTRO_PASSWD_FILE", str(pw_out2))
    monkeypatch.setenv("RASTRO_ACL_FILE", str(acl_out2))

    rc = accounts_mod.main([])
    assert rc == 0
    assert pw_out2.exists() and acl_out2.exists()

    # Sem arquivo de contas definido -> erro
    monkeypatch.delenv("RASTRO_ACCOUNTS_FILE")
    rc = accounts_mod.main([])
    assert rc == 1
    err = capsys.readouterr().err
    assert "ERRO:" in err


def test_validacao_rejeita_senha_curta_e_prefixo_gateway_invalido(tmp_path):
    base = {
        "root": "univaja/mesh",
        "ingest": {"password": "password-ingest-min24chars"},
        "outbox": {"password": "password-outbox-min24chars"},
        "virtual_gateways": [],
        "nodes": [],
    }
    f = tmp_path / "accounts.json"
    f.write_text(json.dumps({**base, "ingest": {"password": "curta"}}))
    with pytest.raises(ValueError, match="muito curta"):
        accounts_mod.load_accounts(f)
    f.write_text(json.dumps({**base, "gateway": {"password": "password-gateway-min24chars", "prefix": "x/#"}}))
    with pytest.raises(ValueError, match="inválidos"):
        accounts_mod.load_accounts(f)


@pytest.mark.parametrize("reservado", ["ingest", "outbox", "gateway", "monitor"])
def test_validacao_rejeita_usuario_de_servico_reservado(reservado, tmp_path):
    """Usuário de nó com nome de serviço reservado (ingest/outbox/gateway/monitor) é rejeitado.

    A rejeição vale mesmo sem a conta de serviço correspondente no JSON — o nome
    colidiria com o passwd/ACL gerado (FIX-1).
    """
    base = {
        "root": "univaja/mesh",
        "ingest": {"password": "password-ingest-min24chars"},
        "outbox": {"password": "password-outbox-min24chars"},
        "virtual_gateways": [{"boat": "b1", "gateway_id": "!f0000001"}],
        "nodes": [
            {"user": reservado, "password": "password-node1-min24chars", "boat": "b1"}
        ],
    }
    f = tmp_path / "accounts.json"
    f.write_text(json.dumps(base))

    with pytest.raises(ValueError, match="duplicado ou reservado"):
        accounts_mod.load_accounts(f)


def test_validacao_rejeita_gateway_virtual_duplicado(tmp_path):
    """O mesmo gateway virtual id atribuído a dois barcos dispara ValueError.

    O gateway virtual publica no tópico EVU do barco: id repetido entre barcos
    cruzaria o downlink dos dois (FIX-1). Repetição no mesmo barco também é
    rejeitada (o banco exige gateway_id UNIQUE).
    """
    bad_data = {
        "root": "univaja/mesh",
        "ingest": {"password": "password-ingest-min24chars"},
        "outbox": {"password": "password-outbox-min24chars"},
        "virtual_gateways": [
            {"boat": "b1", "gateway_id": "!f0000001"},
            {"boat": "b2", "gateway_id": "!f0000001"},
        ],
        "nodes": [
            {"user": "!a0000001", "password": "password-node1-min24chars", "boat": "b1"},
            {"user": "!a0000002", "password": "password-node2-min24chars", "boat": "b2"},
        ],
    }
    f = tmp_path / "accounts.json"
    f.write_text(json.dumps(bad_data))

    with pytest.raises(ValueError, match="gateway virtual '!f0000001' duplicado"):
        accounts_mod.load_accounts(f)
