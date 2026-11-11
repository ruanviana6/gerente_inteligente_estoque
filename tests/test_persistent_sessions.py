"""Testa F5/reabertura, rotação, revogação e isolamento sem endpoints reais."""

import json
from collections import Counter
from dataclasses import replace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from test_web_auth import ORG, PASSWORD, USER_A, FakeAuthTransport

pytest.importorskip("cryptography")

from web.api import HTTPResponse, SupabaseAPI  # noqa: E402
from web.auth import AuthService  # noqa: E402
from web.errors import AuthenticationError, AuthorizationError, ServiceUnavailable  # noqa: E402
from web.persistent_sessions import PersistentSessions, split_ticket  # noqa: E402
from web.settings import PROJECT_URL, Settings  # noqa: E402

BACKEND_SECRET = "segredo-ficticio-sessao-persistente-2026"


class PersistentTransport(FakeAuthTransport):
    """Simula apenas transporte e armazenamento; AuthService e criptografia são reais."""

    def __init__(self):
        super().__init__()
        self.rows = {}
        self.counts = Counter()
        self.fail_claim = False
        for context in self.contexts.values():
            context["capabilities"] = {"persistent_login": True}

    def __call__(self, method, url, headers, payload, timeout):
        path = urlsplit(url).path
        name = path.rsplit("/", 1)[-1]
        self.counts[name] += 1
        if not name.startswith("persistent_"):
            return super().__call__(method, url, headers, payload, timeout)
        if self.fail_claim and name == "persistent_claim":
            raise ServiceUnavailable("Falha fictícia de rede.")
        data = json.loads(payload)
        if data.get("p_backend_secret") != BACKEND_SECRET:
            return HTTPResponse(403, b"{}")
        selector = data["p_selector"]
        user = self.tokens.get(headers.get("Authorization", "").removeprefix("Bearer "))
        if name == "persistent_register":
            self.rows[selector] = {"claim": str(uuid4()), "encrypted_session": data["p_encrypted_session"],
                "organization_id": ORG, "user_id": user, "revoked": False,
                "validator_hash": data["p_validator_hash"], "expires_at": self.now + 30*86400}
            return HTTPResponse(200, b'{"saved":true}')
        row = self.rows.get(selector)
        if row is None or row["revoked"] or row["expires_at"] <= self.now:
            return HTTPResponse(403, b"{}")
        if name in {"persistent_claim", "persistent_forget"}:
            import hashlib
            if row["validator_hash"] != hashlib.sha256(data["p_validator"].encode()).hexdigest():
                return HTTPResponse(403, b"{}")
        if name == "persistent_claim":
            if not self.contexts[row["user_id"]]["profile"]["active"]:
                return HTTPResponse(403, b"{}")
            result = {k: row[k] for k in ("claim", "encrypted_session", "organization_id", "user_id")}
            return HTTPResponse(200, json.dumps(result).encode())
        if name == "persistent_finish":
            if user != row["user_id"] or row["claim"] != data["p_claim"]:
                return HTTPResponse(403, b"{}")
            if data["p_encrypted_session"]:
                row["encrypted_session"] = data["p_encrypted_session"]
        elif name == "persistent_release":
            row["revoked"] = data["p_revoke"]
        elif name == "persistent_forget":
            row["revoked"] = True
        else:
            raise AssertionError("Endpoint fora do contrato de sessão.")
        return HTTPResponse(200, b'{"saved":true}')


def novo_auth(transport):
    settings = Settings(PROJECT_URL, "sb_publishable_exclusivamente_ficticia", backend_secret=BACKEND_SECRET)
    return AuthService(SupabaseAPI(settings, transport), lambda: transport.now)


def test_nova_conexao_restaura_login_sem_senha_ou_novo_password_grant():
    backend = PersistentTransport()
    original = novo_auth(backend)
    original.login("operador@example.test", PASSWORD)
    ticket = original.remember_browser()
    selector, _ = split_ticket(ticket)
    assert "access" not in ticket and "refresh" not in ticket and USER_A not in ticket
    assert "access-" not in backend.rows[selector]["encrypted_session"]
    inicio = len(backend.calls)
    retomada = novo_auth(backend)
    assert retomada.restore_browser(ticket).profile.user_id == USER_A
    assert not any(c["path"].endswith("grant_type=password") for c in backend.calls[inicio:])
    assert retomada.session.refresh_token == original.session.refresh_token


def test_rotacao_e_compartilhada_entre_abas_sem_reutilizar_token_antigo():
    backend = PersistentTransport()
    primeira = novo_auth(backend)
    primeira.login("operador@example.test", PASSWORD)
    ticket = primeira.remember_browser()
    backend.now += 3590
    segunda = novo_auth(backend)
    segunda.restore_browser(ticket)
    renovacoes = sum(c["path"].endswith("grant_type=refresh_token") for c in backend.calls)
    assert renovacoes == 1
    primeira.context()
    assert sum(c["path"].endswith("grant_type=refresh_token") for c in backend.calls) == renovacoes
    assert primeira.session.access_token == segunda.session.access_token


@pytest.mark.parametrize("motivo", ["vencido", "desativado", "forjado", "sair"])
def test_retomada_nega_cookie_invalido_e_nao_cria_sessao(motivo):
    backend = PersistentTransport()
    auth = novo_auth(backend)
    auth.login("operador@example.test", PASSWORD)
    ticket = auth.remember_browser()
    selector, _ = split_ticket(ticket)
    if motivo == "vencido":
        backend.now += 31*86400
    elif motivo == "desativado":
        backend.contexts[USER_A]["profile"]["active"] = False
    elif motivo == "forjado":
        ticket = ticket[:33] + "A"*43
    else:
        auth.logout()
        assert backend.rows[selector]["revoked"]
    nova = novo_auth(backend)
    with pytest.raises((AuthenticationError, AuthorizationError)):
        nova.restore_browser(ticket)
    assert nova.session is None


def test_falha_transitoria_na_renovacao_nao_apaga_acesso_lembrado():
    backend = PersistentTransport()
    auth = novo_auth(backend)
    auth.login("operador@example.test", PASSWORD)
    ticket = auth.remember_browser()
    backend.now += 3590
    backend.fail_claim = True
    with pytest.raises(ServiceUnavailable):
        auth.context()
    assert auth.session is not None
    backend.fail_claim = False
    assert novo_auth(backend).restore_browser(ticket).profile.user_id == USER_A


def test_sessao_sem_jwt_nao_chama_registro_e_backend_nao_aceita_outros_rpcs():
    backend = PersistentTransport()
    auth = novo_auth(backend)
    with pytest.raises(AuthenticationError):
        auth.api.session_rpc("persistent_register", {})
    with pytest.raises(AuthorizationError):
        auth.api.session_rpc("admin_set_user", {})
    assert not backend.rows


def test_payload_de_outro_usuario_e_recusado_apos_auth():
    backend = PersistentTransport()
    auth = novo_auth(backend)
    contexto = auth.login("operador@example.test", PASSWORD)
    sessao = replace(auth.session, user_id="00000000-0000-0000-0000-000000000002")
    ticket = PersistentSessions(auth.api, lambda: backend.now).register(sessao, contexto)
    with pytest.raises(AuthenticationError):
        novo_auth(backend).restore_browser(ticket)
