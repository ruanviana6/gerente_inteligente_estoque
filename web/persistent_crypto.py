"""Protege a sessão Supabase que fica exclusivamente no backend e no banco privado."""

from __future__ import annotations

import base64
import hashlib
import json
import os

from web.errors import AuthenticationError, ConfigurationError


def _key(secret: str) -> bytes:
    """Deriva uma chave exclusiva desta finalidade a partir do segredo do backend."""
    if not isinstance(secret, str) or not 32 <= len(secret) <= 512:
        raise ConfigurationError("O acesso persistente exige o segredo protegido do backend.")
    return hashlib.sha256(b"flatter:persistent-session:v1\0" + secret.encode()).digest()


def encrypt_session(payload: dict, secret: str, binding: str) -> str:
    """Cifra com AES-GCM e vincula o conteúdo ao usuário, organização e dispositivo."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > 16_384:
        raise AuthenticationError("A sessão excede o limite de persistência.")
    nonce = os.urandom(12)
    return base64.urlsafe_b64encode(nonce + AESGCM(_key(secret)).encrypt(nonce, raw, binding.encode())).decode()


def decrypt_session(ciphertext: str, secret: str, binding: str) -> dict:
    """Recusa adulteração, chave errada, troca de usuário e estruturas inesperadas."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        if not isinstance(ciphertext, str) or not 40 <= len(ciphertext) <= 24_000:
            raise ValueError
        raw = base64.b64decode(ciphertext, altchars=b"-_", validate=True)
        plain = AESGCM(_key(secret)).decrypt(raw[:12], raw[12:], binding.encode())
        result = json.loads(plain)
        if (not isinstance(result, dict) or set(result) != {"access_token", "refresh_token", "expires_at", "user_id"}
                or len(plain) > 16_384):
            raise ValueError
        return result
    except (ValueError, TypeError, InvalidTag, UnicodeError):
        raise AuthenticationError("O acesso lembrado não é válido. Entre novamente.") from None
