"""Coordena retomada e renovação de sessão sem expor tokens Supabase no navegador."""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import time
from dataclasses import asdict
from uuid import UUID, uuid4

from web.errors import AppError, AuthenticationError, AuthorizationError, ServiceUnavailable
from web.persistent_crypto import decrypt_session, encrypt_session
from web.settings import PROJECT_REF

COOKIE_NAME = "__Host-flatter_session"
COOKIE_DAYS = 30
TICKET_PATTERN = re.compile(r"[0-9a-f]{32}\.[A-Za-z0-9_-]{43}")


def split_ticket(ticket: object) -> tuple[str, str]:
    """Aceita somente identificador aleatório e validador opaco, nunca JWT ou URL."""
    if not isinstance(ticket, str) or not TICKET_PATTERN.fullmatch(ticket):
        raise AuthenticationError("O acesso lembrado não é válido.")
    selector, validator = ticket.split(".")
    return str(UUID(hex=selector)), validator


def _binding(selector, organization, user) -> str:
    """Impede aproveitar uma sessão cifrada em outro usuário ou organização."""
    return f"{PROJECT_REF}:{organization}:{user}:{selector}"


def _validate_payload(value):
    """Valida a sessão cifrada antes de utilizar qualquer campo em autenticação."""
    if not isinstance(value, dict) or set(value) != {"access_token", "refresh_token", "expires_at", "user_id"}:
        raise AuthenticationError("Sessão lembrada inválida.")
    if any(not isinstance(value[k], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,16384}", value[k])
           for k in ("access_token", "refresh_token")):
        raise AuthenticationError("Sessão lembrada inválida.")
    expiry = value["expires_at"]
    if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= 0:
        raise AuthenticationError("Sessão lembrada inválida.")
    try:
        if str(UUID(value["user_id"])) != value["user_id"]:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise AuthenticationError("Sessão lembrada inválida.") from None
    return value


class PersistentSessions:
    """Usa chave comum, segredo dedicado e JWT individual, sem service_role."""

    def __init__(self, api, clock=time.time):
        """Não conserva dados entre usuários nem escreve arquivos locais."""
        self.api, self.clock = api, clock

    def register(self, session, context) -> str:
        """Registra um navegador após login e primeiro acesso validados."""
        selector, validator = str(uuid4()), secrets.token_urlsafe(32)
        encrypted = encrypt_session(asdict(session), self.api.settings.backend_secret,
            _binding(selector, context.profile.organization_id, session.user_id))
        self.api.session_rpc("persistent_register", {"p_selector": selector,
            "p_validator_hash": hashlib.sha256(validator.encode()).hexdigest(), "p_encrypted_session": encrypted},
            session.access_token)
        return UUID(selector).hex + "." + validator

    def resume(self, ticket: str) -> dict:
        """Obtém a sessão mais recente e serializa refresh entre abas e processos."""
        selector, validator = split_ticket(ticket)
        lease = None
        for attempt in range(8):
            lease = self.api.session_rpc("persistent_claim", {"p_selector": selector, "p_validator": validator})
            if isinstance(lease, dict) and not lease.get("busy"):
                break
            if attempt < 7:
                time.sleep(0.2)
        if not isinstance(lease, dict) or lease.get("busy"):
            raise ServiceUnavailable("A sessão está sendo retomada em outra aba. Tente novamente em instantes.")
        try:
            if set(lease) != {"claim", "encrypted_session", "organization_id", "user_id"}:
                raise AuthenticationError("Retomada de sessão inválida.")
            payload = _validate_payload(decrypt_session(lease["encrypted_session"], self.api.settings.backend_secret,
                _binding(selector, lease["organization_id"], lease["user_id"])))
            if payload.get("user_id") != lease["user_id"]:
                raise AuthenticationError("A identidade lembrada mudou.")
            changed = False
            if payload.get("expires_at", 0) <= self.clock() + 60:
                response = self.api.auth("POST", "token?grant_type=refresh_token", {"refresh_token": payload["refresh_token"]})
                if (not isinstance(response, dict) or not isinstance(response.get("user"), dict)
                        or response["user"].get("id") != lease["user_id"] or response["user"].get("is_anonymous") is True):
                    raise AuthenticationError("A identidade da sessão mudou.")
                expiry = response.get("expires_at")
                if expiry is None:
                    lifetime = response.get("expires_in")
                    if isinstance(lifetime, bool) or not isinstance(lifetime, (int, float)):
                        raise AuthenticationError("A sessão renovada é inválida.")
                    expiry = self.clock() + lifetime
                payload = _validate_payload({"access_token": response.get("access_token"), "refresh_token": response.get("refresh_token"),
                           "expires_at": expiry, "user_id": lease["user_id"]})
                changed = True
            # O Auth é conferido mesmo quando o JWT ainda não venceu. O banco
            # repete a validação da sessão e do primeiro acesso no commit.
            user = self.api.auth("GET", "user", access_token=payload["access_token"])
            if not isinstance(user, dict) or user.get("id") != lease["user_id"] or user.get("is_anonymous") is True:
                raise AuthenticationError("A identidade lembrada não está ativa.")
            encrypted = encrypt_session(payload, self.api.settings.backend_secret,
                _binding(selector, lease["organization_id"], lease["user_id"])) if changed else None
            self.api.session_rpc("persistent_finish", {"p_selector": selector, "p_claim": lease["claim"],
                "p_encrypted_session": encrypted}, payload["access_token"])
            return payload
        except AppError as error:
            try:
                self.api.session_rpc("persistent_release", {"p_selector": selector, "p_claim": lease.get("claim"),
                    "p_revoke": isinstance(error, (AuthenticationError, AuthorizationError))})
            except AppError:
                pass
            raise

    def revoke(self, ticket, access_token=None):
        """Revoga o dispositivo antes do logout; a exclusão do cookie é feita pela UI."""
        selector, validator = split_ticket(ticket)
        self.api.session_rpc("persistent_forget", {"p_selector": selector, "p_validator": validator}, access_token)
