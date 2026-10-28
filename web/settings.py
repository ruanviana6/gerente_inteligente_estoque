"""Configuração protegida da aplicação, vinculada ao projeto Supabase aprovado."""

import base64
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from web.errors import ConfigurationError

PROJECT_REF = "csiqpzoudruzppfbpqqx"
PROJECT_URL = f"https://{PROJECT_REF}.supabase.co"


def _jwt_role(value: str) -> str | None:
    """Inspeciona apenas o tipo da chave; isso não valida identidade ou assinatura."""
    try:
        parts = value.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return decoded.get("role") if isinstance(decoded, dict) else None
    except (ValueError, TypeError, UnicodeError):
        return None


def _valid_key(value: str, administrative: bool = False) -> bool:
    """Recusa chave de tipo errado e caracteres que não pertencem a um header."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{10,8192}", value):
        return False
    prefix = "sb_secret_" if administrative else "sb_publishable_"
    role = "service_role" if administrative else "anon"
    return value.startswith(prefix) or _jwt_role(value) == role


def _boolean(value: object) -> bool:
    """Aceita booleano ou texto explícito; uma string 'false' nunca habilita envio."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower().strip() in {"true", "false"}:
        return value.lower().strip() == "true"
    raise ConfigurationError("AUTH_EMAIL_ENABLED deve ser true ou false.")


@dataclass(frozen=True)
class Settings:
    """Mantém segredos fora do repr e permite somente o projeto já autorizado."""

    supabase_url: str
    publishable_key: str = field(repr=False)
    service_role_key: str | None = field(default=None, repr=False)
    auth_email_enabled: bool = False
    timeout_seconds: int = 20
    backend_secret: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Valida origem HTTPS e separação de chaves antes de qualquer conexão."""
        try:
            url = urlsplit(self.supabase_url)
            valid_url = (
                url.scheme == "https" and url.hostname == f"{PROJECT_REF}.supabase.co"
                and url.port is None and not url.username and not url.password
                and url.path in {"", "/"} and not url.query and not url.fragment
            )
        except (TypeError, ValueError):
            valid_url = False
        if not valid_url:
            raise ConfigurationError("Configure a URL HTTPS do projeto Supabase aprovado.")
        if not _valid_key(self.publishable_key):
            raise ConfigurationError("Use uma chave publishable ou anon nas consultas comuns.")
        if self.service_role_key is not None and not _valid_key(self.service_role_key, administrative=True):
            raise ConfigurationError("A chave administrativa tem formato ou tipo inválido.")
        if type(self.auth_email_enabled) is not bool:
            raise ConfigurationError("A autorização de e-mail deve ser booleana.")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 60:
            raise ConfigurationError("O tempo limite HTTP deve ficar entre 1 e 60 segundos.")
        if self.backend_secret is not None and (
            not isinstance(self.backend_secret, str) or not 32 <= len(self.backend_secret) <= 512
        ):
            raise ConfigurationError("O segredo dedicado do backend deve ter entre 32 e 512 caracteres.")
        object.__setattr__(self, "supabase_url", PROJECT_URL)

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "Settings":
        """Lê Streamlit Secrets ou ambiente sem imprimir nenhuma configuração."""
        return cls(
            supabase_url=str(values.get("SUPABASE_URL", "")),
            publishable_key=str(values.get("SUPABASE_PUBLISHABLE_KEY") or values.get("SUPABASE_ANON_KEY", "")),
            service_role_key=str(values["SUPABASE_SERVICE_ROLE_KEY"]) if values.get("SUPABASE_SERVICE_ROLE_KEY") else None,
            auth_email_enabled=_boolean(values.get("AUTH_EMAIL_ENABLED", False)),
            backend_secret=values.get("SUPABASE_BACKEND_SECRET"),
        )
