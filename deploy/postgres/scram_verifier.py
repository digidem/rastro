#!/usr/bin/env python3
"""Lê UMA senha do stdin e imprime o verificador SCRAM-SHA-256 do PostgreSQL.

A senha nunca passa por argv nem aparece em log: o servidor recebe só o
verificador (``SCRAM-SHA-256$<iter>:<salt>$<StoredKey>:<ServerKey>``), no mesmo
formato que o próprio PostgreSQL grava em ``pg_authid.rolpassword``.

Uso: printf '%s' "$SENHA" | python3 scram_verifier.py
"""
import base64
import hashlib
import hmac
import secrets
import sys

ITERATIONS = 4096  # padrão do PostgreSQL (scram_iterations)


def verifier(password: str, salt: bytes | None = None, iterations: int = ITERATIONS) -> str:
    salt = salt or secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def main() -> int:
    pw = sys.stdin.read()
    if pw.endswith("\n"):
        pw = pw[:-1]
    if not pw.isascii():
        print("ERRO: use só caracteres ASCII na senha (sem SASLprep aqui)", file=sys.stderr)
        return 1
    if len(pw) < 24:
        print("ERRO: senha com menos de 24 caracteres", file=sys.stderr)
        return 1
    print(verifier(pw))
    return 0


if __name__ == "__main__":
    sys.exit(main())
