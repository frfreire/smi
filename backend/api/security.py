"""Autenticação: hash de senha (PBKDF2) e tokens JWT (RFC 7519)."""
import hashlib
import hmac
import os
import time

import jwt

ITERACOES = 100_000


def hash_senha(senha: str) -> str:
    sal = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal, ITERACOES)
    return f"{sal.hex()}${h.hex()}"


def verificar_senha(senha: str, armazenado: str) -> bool:
    try:
        sal_hex, h_hex = armazenado.split("$")
    except ValueError:
        return False
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(sal_hex), ITERACOES)
    return hmac.compare_digest(h.hex(), h_hex)


def emitir_token(segredo: str, usuario_id: int, papel: str, validade_s: int = 1800) -> str:
    agora = int(time.time())
    return jwt.encode(
        {"sub": str(usuario_id), "papel": papel, "iat": agora, "exp": agora + validade_s},
        segredo,
        algorithm="HS256",
    )


def decodificar_token(segredo: str, token: str) -> dict:
    """Levanta jwt.ExpiredSignatureError ou jwt.InvalidTokenError."""
    return jwt.decode(token, segredo, algorithms=["HS256"])
