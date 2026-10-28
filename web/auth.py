"""Autenticação Supabase com sessão por navegador e autorização renovada por operação."""

import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from web.api import SupabaseAPI
from web.errors import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    RateLimited,
    ServiceUnavailable,
)

PERMISSIONS = frozenset({
    "dashboard.view", "inventory.view", "replenishment.view", "replenishment.export",
    "finance.view", "trends.view", "inventory.import", "parameters.edit", "decisions.write",
    "audit.view", "users.manage", "permissions.manage", "integrations.manage", "reports.view",
    "reports.export", "reports.receive", "reports.manage",
})
ROLES = ("Master", "Administrador", "Operador", "Visualizador")
MASTER_ONLY_PERMISSIONS = frozenset({
    "users.manage", "permissions.manage", "reports.manage", "integrations.manage",
})


def _uuid(value: object) -> str:
    """Normaliza um identificador imutável sem aceitar identidade arbitrária."""
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise AuthenticationError("A resposta de identidade é inválida. Entre novamente.") from None


def _email(value: str) -> str:
    """Valida tamanho e formato básico; a titularidade continua no Supabase Auth."""
    if not isinstance(value, str):
        raise AppError("Informe um e-mail válido.")
    value = value.strip()
    if len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise AppError("Informe um e-mail válido.")
    return value


def validate_password(value: str) -> None:
    """Exige senha longa e variada nas operações de criação e troca de senha."""
    if not isinstance(value, str) or not 12 <= len(value) <= 128:
        raise AppError("A senha deve ter de 12 a 128 caracteres, letras, números e símbolos.")
    if not all((any(c.isalpha() for c in value), any(c.isdigit() for c in value),
                any(not c.isalnum() and not c.isspace() for c in value))):
        raise AppError("A senha deve ter de 12 a 128 caracteres, letras, números e símbolos.")


@dataclass(frozen=True)
class Session:
    """Guarda tokens apenas no servidor, sem inclusão em repr ou URL do navegador."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float
    user_id: str


@dataclass(frozen=True)
class Profile:
    """Descreve o perfil protegido retornado pelo banco, nunca por user_metadata."""

    user_id: str
    organization_id: str
    display_name: str
    role: str
    active: bool


@dataclass(frozen=True)
class AccessContext:
    """Representa uma verificação pontual; operações não reutilizam permissões antigas."""

    profile: Profile
    permissions: frozenset[str]

    def can(self, permission: str) -> bool:
        """Exige concessão explícita; nome do perfil sozinho não libera funcionalidades."""
        return self.profile.active and permission in self.permissions


class AuthService:
    """Coordena autenticação de uma única sessão Streamlit, sem estado global."""

    def __init__(self, api: SupabaseAPI, clock: Callable[[], float] = time.time) -> None:
        """Injeta relógio e transporte para verificar expiração sem conexões reais."""
        self.api = api
        self._clock = clock
        self._session: Session | None = None
        self._failed_logins = 0
        self._blocked_until = 0.0

    @property
    def session(self) -> Session | None:
        """Disponibiliza somente a sessão desta instância, sem expor senha."""
        return self._session

    def _read_session(self, response: object) -> Session:
        """Aceita sessão somente após validar estrutura e identidade da resposta Auth."""
        try:
            if not isinstance(response, dict) or not isinstance(response["user"], dict):
                raise ValueError
            access = response["access_token"]
            refresh = response["refresh_token"]
            if not all(isinstance(value, str) and value and len(value) <= 16384 for value in (access, refresh)):
                raise ValueError
            lifetime = response.get("expires_at")
            if lifetime is None:
                lifetime = self._clock() + float(response["expires_in"])
            if isinstance(lifetime, bool):
                raise ValueError
            expires = float(lifetime)
            if not math.isfinite(expires) or expires <= self._clock():
                raise ValueError
            if response["user"].get("is_anonymous") is True:
                raise ValueError
            return Session(access, refresh, expires, _uuid(response["user"]["id"]))
        except (KeyError, ValueError, TypeError, OverflowError):
            raise AuthenticationError("A sessão retornada é inválida. Entre novamente.") from None

    def _current_session(self) -> Session:
        """Renova o token antes da expiração e recusa troca inesperada de identidade."""
        session = self._session
        if session is None:
            raise AuthenticationError("Entre para continuar.")
        if session.expires_at <= self._clock() + 60:
            try:
                response = self.api.auth("POST", "token?grant_type=refresh_token", {
                    "refresh_token": session.refresh_token,
                })
                refreshed = self._read_session(response)
                if refreshed.user_id != session.user_id:
                    raise AuthenticationError("A identidade da sessão mudou. Entre novamente.")
                self._session = refreshed
                session = refreshed
            except AppError:
                self._session = None
                raise
        return session

    def login(self, email: str, password: str) -> AccessContext:
        """Autentica e exige perfil ativo antes de disponibilizar qualquer dado interno."""
        self._session = None
        if self._clock() < self._blocked_until:
            raise RateLimited("Muitas tentativas nesta sessão. Aguarde um minuto.")
        email = _email(email)
        if not isinstance(password, str) or not password or len(password) > 128:
            raise AuthenticationError("Confira o e-mail e a senha.")
        try:
            response = self.api.auth("POST", "token?grant_type=password", {"email": email, "password": password})
            self._session = self._read_session(response)
            context = self.context()
        except (AuthenticationError, AuthorizationError):
            self._session = None
            self._failed_logins += 1
            if self._failed_logins >= 5:
                self._blocked_until = self._clock() + 60
                self._failed_logins = 0
            raise AuthenticationError("Acesso não autorizado. Confira suas credenciais ou consulte o Master.") from None
        except AppError:
            self._session = None
            raise
        self._failed_logins = 0
        return context

    def context(self) -> AccessContext:
        """Revalida usuário e perfil no servidor, incluindo bloqueios e revogações recentes."""
        try:
            session = self._current_session()
            user = self.api.auth("GET", "user", access_token=session.access_token)
            if (not isinstance(user, dict) or _uuid(user.get("id")) != session.user_id
                    or user.get("is_anonymous") is True):
                raise AuthenticationError("Sessão inválida. Entre novamente.")
            result = self.api.rpc("app_context", {}, session.access_token)
            if not isinstance(result, dict) or not isinstance(result.get("profile"), dict):
                raise AuthorizationError("A conta não possui acesso ativo ao aplicativo.")
            profile = result["profile"]
            permissions = result.get("permissions")
            if (profile.get("active") is not True or _uuid(profile.get("user_id")) != session.user_id
                    or profile.get("role") not in ROLES or not isinstance(permissions, list)
                    or not all(isinstance(p, str) and p in PERMISSIONS for p in permissions)):
                raise AuthorizationError("A conta não possui acesso ativo ao aplicativo.")
            return AccessContext(Profile(
                user_id=session.user_id, organization_id=_uuid(profile.get("organization_id")),
                display_name=str(profile.get("display_name", "Usuário")), role=profile["role"], active=True,
            ), frozenset(permissions))
        except (AuthenticationError, AuthorizationError):
            self._session = None
            raise

    def require(self, permission: str) -> AccessContext:
        """Exige uma permissão vigente antes de cada operação protegida."""
        context = self.context()
        if not context.can(permission):
            raise AuthorizationError("Seu usuário não tem permissão para esta funcionalidade.")
        return context

    def rpc(self, name: str, payload: dict | None, permission: str) -> Any:
        """Verifica autorização no backend e envia JWT; o banco a verifica novamente."""
        self.require(permission)
        session = self._current_session()
        try:
            return self.api.rpc(name, payload or {}, session.access_token)
        except (AuthenticationError, AuthorizationError):
            # Uma revogação entre a consulta de contexto e a operação não reutiliza sessão local.
            self._session = None
            raise

    def logout(self) -> None:
        """Revoga sessões no Auth e sempre remove os tokens locais, inclusive em falha de rede."""
        try:
            if self._session is not None:
                session = self._current_session()
                self.api.auth("POST", "logout?scope=global", access_token=session.access_token)
        except AppError:
            raise ServiceUnavailable("Sessão local encerrada; não foi possível confirmar a revogação remota.") from None
        finally:
            self._session = None

    def request_password_reset(self, email: str) -> None:
        """Solicita código de recuperação somente após habilitação explícita do remetente."""
        if not self.api.settings.auth_email_enabled:
            raise ConfigurationError("Recuperação por e-mail aguarda configuração e autorização do proprietário.")
        self.api.auth("POST", "recover", {"email": _email(email)})

    def recover_password(self, email: str, otp: str, password: str) -> None:
        """Confirma código digitado, troca senha e encerra a sessão sem tokens na URL."""
        validate_password(password)
        if not isinstance(otp, str) or not re.fullmatch(r"\d{6,10}", otp):
            raise AuthenticationError("Informe o código de recuperação recebido.")
        self._session = None
        try:
            response = self.api.auth("POST", "verify", {"type": "recovery", "email": _email(email), "token": otp})
            self._session = self._read_session(response)
            self.change_password(password)
        finally:
            self._session = None

    def change_password(self, password: str) -> None:
        """Exige identidade ativa, altera senha no Auth e solicita novo login."""
        validate_password(password)
        self.context()
        session = self._current_session()
        self.api.auth("PUT", "user", {"password": password}, session.access_token)
        self.logout()

    def create_user(
        self, email: str, password: str, display_name: str, role: str,
        permissions: Mapping[str, bool] | None = None,
    ) -> str:
        """Cria uma conta autorizada sem e-mail automático e concede perfil via RPC auditada."""
        email = _email(email)
        validate_password(password)
        if role not in ROLES[1:]:
            raise AuthorizationError("A criação de Master não está disponível na aplicação.")
        if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 120:
            raise AppError("Informe um nome de exibição com até 120 caracteres.")
        overrides = dict(permissions or {})
        # O banco recusa essas chaves mesmo quando o valor é false. Validar antes
        # do Admin API evita criar uma identidade que não poderá receber o perfil.
        if any(key not in PERMISSIONS or key in MASTER_ONLY_PERMISSIONS or type(value) is not bool
               for key, value in overrides.items()):
            raise AppError("Uma permissão informada é inválida ou exclusiva do Master.")
        context = self.require("users.manage")
        if context.profile.role != "Master" or not context.can("permissions.manage"):
            raise AuthorizationError("Somente o Master pode cadastrar e autorizar contas.")
        result = self.api._create_auth_user(email, password)
        if not isinstance(result, dict):
            raise ServiceUnavailable("Não foi possível confirmar a criação da conta.")
        # O Admin API pode retornar o usuário diretamente ou dentro de user.
        user = result.get("user", result)
        user_id = _uuid(user.get("id") if isinstance(user, dict) else None)
        self.rpc("admin_set_user", {
            "p_user_id": user_id, "p_display_name": display_name.strip(), "p_role": role,
            "p_active": True, "p_permissions": overrides,
        }, "permissions.manage")
        return user_id
