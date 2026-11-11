"""Verifica confidencialidade e vínculo dos tokens cifrados do backend."""

import pytest

pytest.importorskip("cryptography")

from web.errors import AuthenticationError  # noqa: E402
from web.persistent_crypto import decrypt_session, encrypt_session  # noqa: E402

SECRET = "segredo-exclusivamente-ficticio-de-criptografia-2026"
BINDING = "projeto:organizacao:usuario:dispositivo"
PAYLOAD = {"access_token": "access-ficticio-nao-autentica", "refresh_token": "refresh-ficticio-nao-autentica",
           "expires_at": 123456.0, "user_id": "00000000-0000-4000-8000-000000000001"}


def test_sessao_cifrada_nao_revela_tokens_e_reconstroi_exatamente():
    cifra = encrypt_session(PAYLOAD, SECRET, BINDING)
    assert PAYLOAD["access_token"] not in cifra and PAYLOAD["refresh_token"] not in cifra
    assert decrypt_session(cifra, SECRET, BINDING) == PAYLOAD
    assert encrypt_session(PAYLOAD, SECRET, BINDING) != cifra


@pytest.mark.parametrize("tipo", ["alteracao", "chave", "usuario"])
def test_adulteracao_chave_errada_ou_outro_usuario_nao_decifram(tipo):
    cifra = encrypt_session(PAYLOAD, SECRET, BINDING)
    if tipo == "alteracao":
        cifra = ("A" if cifra[0] != "A" else "B") + cifra[1:]
    with pytest.raises(AuthenticationError):
        decrypt_session(cifra, SECRET + "x" if tipo == "chave" else SECRET,
                        BINDING + ":outro" if tipo == "usuario" else BINDING)
