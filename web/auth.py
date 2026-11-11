"""Autenticação Supabase com sessão por navegador e autorização renovada por operação."""

import math
import re
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from threading import RLock
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
from web.telemetry import measure

PERMISSIONS = frozenset({
    "dashboard.view", "inventory.view", "inventory.export", "replenishment.view", "replenishment.export",
    "finance.view", "trends.view", "inventory.import", "parameters.edit", "decisions.write",
    "audit.view", "users.manage", "permissions.manage", "integrations.manage", "reports.view",
    "reports.export", "reports.receive", "reports.manage",
    "commercial.view", "commercial.customer_detail", "commercial.manage",
    "fiscal.view", "fiscal.create_request", "fiscal.approve_pending", "fiscal.audit", "fiscal.manage",
})
ROLES = ("Master", "Administrador", "Operador", "Visualizador")
MASTER_ONLY_PERMISSIONS = frozenset({
    "users.manage", "permissions.manage", "reports.manage", "integrations.manage", "commercial.manage", "fiscal.manage",
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


def _serialized(method: Callable) -> Callable:
    """Serializa operações da mesma sessão, inclusive a renovação em downloads."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        """Evita que login, logout e refresh troquem identidade durante uma operação."""
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class AuthService:
    """Coordena autenticação de uma única sessão Streamlit, sem estado global."""

    def __init__(self, api: SupabaseAPI, clock: Callable[[], float] = time.time) -> None:
        """Injeta relógio e transporte para verificar expiração sem conexões reais."""
        self.api = api
        self._clock = clock
        self._session: Session | None = None
        self._password_change_required = False
        self._failed_logins = 0
        self._blocked_until = 0.0
        self._lock = RLock()
        self._generation = 0
        self._persistent_ticket: str | None = None
        self._persistent_supported = False
        # ContextVar pertence a esta instância e ao fluxo atual, nunca ao processo
        # inteiro. Só a validação Auth é reutilizada; permissões sempre vêm do banco.
        self._verified: ContextVar[tuple | None] = ContextVar("auth_verification", default=None)
        self._execution_active: ContextVar[bool] = ContextVar("auth_execution", default=False)

    @property
    def session(self) -> Session | None:
        """Disponibiliza somente a sessão desta instância, sem expor senha."""
        with self._lock:
            return self._session

    @property
    def generation(self) -> int:
        """Identifica a vida da sessão; novo login nunca reativa downloads antigos."""
        with self._lock:
            return self._generation

    def _invalidate_session(self) -> None:
        """Descarta a sessão e invalida callbacks vinculados à autenticação anterior."""
        self._session = None
        self._password_change_required = False
        self._persistent_ticket = None
        self._generation += 1
        self._verified.set(None)

    @contextmanager
    def execution(self) -> Iterator[None]:
        """Limita a reutilização Auth ao rerun, fragmento ou download atual.

        Escopos novos sempre validam Auth de novo. Não se guardam permissões,
        dados nem autorização entre execuções. O banco revalida auth.sessions.
        """
        marker = self._verified.set(None)
        active = self._execution_active.set(True)
        try:
            yield
        finally:
            self._verified.reset(marker)
            self._execution_active.reset(active)

    @contextmanager
    def operation(self) -> Iterator[None]:
        """Mantém a identidade estável até terminar uma operação com vários passos."""
        with self._lock:
            yield

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

    @_serialized
    def _current_session(self) -> Session:
        """Renova o token antes da expiração e recusa troca inesperada de identidade."""
        session = self._session
        if session is None:
            raise AuthenticationError("Entre para continuar.")
        if session.expires_at <= self._clock() + 60:
            try:
                if self._persistent_ticket:
                    from web.persistent_sessions import PersistentSessions

                    payload = PersistentSessions(self.api, self._clock).resume(self._persistent_ticket)
                    response = payload | {"user": {"id": payload["user_id"]}}
                else:
                    response = self.api.auth("POST", "token?grant_type=refresh_token", {
                        "refresh_token": session.refresh_token,
                    })
                refreshed = self._read_session(response)
                if refreshed.user_id != session.user_id:
                    raise AuthenticationError("A identidade da sessão mudou. Entre novamente.")
                self._session = refreshed
                session = refreshed
            except AppError as error:
                if not self._persistent_ticket or isinstance(error, (AuthenticationError, AuthorizationError)):
                    self._invalidate_session()
                raise
        return session

    @_serialized
    def login(self, email: str, password: str) -> AccessContext | None:
        """Autentica e exige perfil ativo antes de disponibilizar qualquer dado interno."""
        self._invalidate_session()
        if self._clock() < self._blocked_until:
            raise RateLimited("Muitas tentativas nesta sessão. Aguarde um minuto.")
        email = _email(email)
        if not isinstance(password, str) or not password or len(password) > 128:
            raise AuthenticationError("Confira o e-mail e a senha.")
        try:
            response = self.api.auth("POST", "token?grant_type=password", {"email": email, "password": password})
            self._session = self._read_session(response)
            gate = self.password_gate_status()
            self._password_change_required = gate["required"]
            context = None if gate["required"] else self.context()
        except (AuthenticationError, AuthorizationError):
            self._invalidate_session()
            self._failed_logins += 1
            if self._failed_logins >= 5:
                self._blocked_until = self._clock() + 60
                self._failed_logins = 0
            raise AuthenticationError("Acesso não autorizado. Confira suas credenciais ou consulte o Master.") from None
        except AppError:
            self._invalidate_session()
            raise
        self._failed_logins = 0
        return context

    @property
    def password_change_required(self) -> bool:
        """Informa se esta sessão está restrita à troca obrigatória de senha."""
        return self._password_change_required

    @_serialized
    def password_gate_status(self) -> dict[str, bool]:
        """Consulta a restrição no banco usando somente a identidade da sessão atual."""
        session = self._current_session()
        result = self.api.rpc("password_gate_status", {}, session.access_token)
        if not isinstance(result, dict) or any(type(result.get(k)) is not bool for k in ("required", "ready", "expired")):
            raise ServiceUnavailable("Não foi possível confirmar o estado do acesso.")
        return {k: result[k] for k in ("required", "ready", "expired")}

    @_serialized
    def finish_initial_password(self, password: str) -> None:
        """Troca a senha temporária no Auth e só libera a conta após confirmação no banco."""
        validate_password(password)
        gate = self.password_gate_status()
        if not gate["required"] or not gate["ready"] or gate["expired"]:
            raise AuthorizationError("A senha temporária expirou ou precisa ser refeita pelo Master.")
        session = self._current_session()
        self.api.auth("PUT", "user", {"password": password}, session.access_token)
        self.api.rpc("finish_initial_password", {}, session.access_token)
        self.logout()

    @_serialized
    def context(self) -> AccessContext:
        """Revalida usuário e perfil no servidor, incluindo bloqueios e revogações recentes."""
        try:
            session = self._current_session()
            stamp = (session, self._generation)
            if not self._execution_active.get() or self._verified.get() != stamp:
                with measure("auth_user"):
                    user = self.api.auth("GET", "user", access_token=session.access_token)
                if (not isinstance(user, dict) or _uuid(user.get("id")) != session.user_id
                        or user.get("is_anonymous") is True):
                    raise AuthenticationError("Sessão inválida. Entre novamente.")
                if self._execution_active.get():
                    self._verified.set(stamp)
            with measure("app_context"):
                result = self.api.rpc("app_context", {"p_supported_permissions": sorted(PERMISSIONS)}, session.access_token)
            if not isinstance(result, dict) or not isinstance(result.get("profile"), dict):
                raise AuthorizationError("A conta não possui acesso ativo ao aplicativo.")
            profile = result["profile"]
            capabilities = result.get("capabilities")
            self._persistent_supported = isinstance(capabilities, dict) and capabilities.get("persistent_login") is True
            permissions = result.get("permissions")
            if (profile.get("active") is not True or _uuid(profile.get("user_id")) != session.user_id
                    or profile.get("role") not in ROLES or not isinstance(permissions, list)
                    or not all(isinstance(p, str) and 1 <= len(p) <= 100 for p in permissions)):
                raise AuthorizationError("A conta não possui acesso ativo ao aplicativo.")
            return AccessContext(Profile(
                user_id=session.user_id, organization_id=_uuid(profile.get("organization_id")),
                display_name=str(profile.get("display_name", "Usuário")), role=profile["role"], active=True,
            # O banco pode evoluir antes do processo Streamlit. Permissões novas
            # são ignoradas pela versão antiga, sem bloquear o login ou concedê-las.
            ), frozenset(p for p in permissions if p in PERMISSIONS))
        except (AuthenticationError, AuthorizationError):
            self._invalidate_session()
            raise

    @property
    def persistent_supported(self) -> bool:
        """Só habilita lembrança depois de o banco confirmar o contrato instalado."""
        return self._persistent_supported and bool(self.api.settings.backend_secret)

    @_serialized
    def remember_browser(self) -> str:
        """Emite referência opaca após login completo; nunca persiste senha no navegador."""
        from web.persistent_sessions import PersistentSessions

        context = self.context()
        if not self.persistent_supported or self.password_change_required:
            raise ConfigurationError("A permanência do login ainda não está configurada.")
        ticket = PersistentSessions(self.api, self._clock).register(self._current_session(), context)
        self._persistent_ticket = ticket
        return ticket

    @_serialized
    def restore_browser(self, ticket: str) -> AccessContext:
        """Retoma no F5 ou reabertura e repete Auth, sessão, usuário e permissões."""
        from web.persistent_sessions import PersistentSessions

        self._invalidate_session()
        try:
            payload = PersistentSessions(self.api, self._clock).resume(ticket)
            self._session = self._read_session(payload | {"user": {"id": payload["user_id"]}})
            self._persistent_ticket = ticket
            return self.context()
        except AppError:
            self._invalidate_session()
            raise

    @_serialized
    def require(self, permission: str) -> AccessContext:
        """Exige uma permissão vigente antes de cada operação protegida."""
        context = self.context()
        if not context.can(permission):
            raise AuthorizationError("Seu usuário não tem permissão para esta funcionalidade.")
        return context

    @_serialized
    def rpc(self, name: str, payload: dict | None, permission: str) -> Any:
        """Verifica autorização no backend e envia JWT; o banco a verifica novamente."""
        return self.rpc_with_context(name, payload, permission)[0]

    @_serialized
    def rpc_with_context(
        self, name: str, payload: dict | None, permission: str,
    ) -> tuple[Any, AccessContext]:
        """Vincula a resposta ao contexto consultado sem repetir app_context.

        O contexto retornado identifica o escopo da consulta. Não substitui
        a revalidação de autorização após cálculos ou antes de entregar dados.
        """
        context = self.require(permission)
        session = self._current_session()
        try:
            return self.api.rpc(name, payload or {}, session.access_token), context
        except (AuthenticationError, AuthorizationError):
            # Uma revogação entre a consulta de contexto e a operação não reutiliza sessão local.
            self._invalidate_session()
            raise

    @_serialized
    def logout(self, scope: str = "global") -> None:
        """Revoga sessões no Auth e sempre remove os tokens locais, inclusive em falha de rede."""
        if scope not in {"local", "global"}:
            raise ValueError("Escopo de saída inválido.")
        try:
            if self._session is not None:
                session = self._current_session()
                if self._persistent_ticket:
                    from web.persistent_sessions import PersistentSessions

                    try:
                        PersistentSessions(self.api, self._clock).revoke(self._persistent_ticket, session.access_token)
                    except AppError:
                        # O logout confirmado pelo Auth remove auth.sessions e
                        # também invalida a retomada, mesmo se este RPC falhar.
                        pass
                self.api.auth("POST", f"logout?scope={scope}", access_token=session.access_token)
        except AppError:
            raise ServiceUnavailable("Sessão local encerrada; não foi possível confirmar a revogação remota.") from None
        finally:
            self._invalidate_session()

    def request_password_reset(self, email: str) -> None:
        """Solicita código de recuperação somente após habilitação explícita do remetente."""
        if not self.api.settings.auth_email_enabled:
            raise ConfigurationError("Recuperação por e-mail aguarda configuração e autorização do proprietário.")
        self.api.auth("POST", "recover", {"email": _email(email)})

    @_serialized
    def recover_password(self, email: str, otp: str, password: str) -> None:
        """Confirma código digitado, troca senha e encerra a sessão sem tokens na URL."""
        validate_password(password)
        if not isinstance(otp, str) or not re.fullmatch(r"\d{6,10}", otp):
            raise AuthenticationError("Informe o código de recuperação recebido.")
        self._invalidate_session()
        try:
            response = self.api.auth("POST", "verify", {"type": "recovery", "email": _email(email), "token": otp})
            self._session = self._read_session(response)
            gate = self.password_gate_status()
            if gate["required"]:
                self.finish_initial_password(password)
            else:
                self.change_password(password)
        finally:
            self._invalidate_session()

    @_serialized
    def change_password(self, password: str) -> None:
        """Exige identidade ativa, altera senha no Auth e solicita novo login."""
        validate_password(password)
        self.context()
        session = self._current_session()
        self.api.auth("PUT", "user", {"password": password}, session.access_token)
        self.logout()

    @_serialized
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
        self.rpc("admin_register_temporary_user", {
            "p_user_id": user_id, "p_display_name": display_name.strip(), "p_role": role,
            "p_permissions": overrides,
        }, "permissions.manage")
        return user_id

    @_serialized
    def reset_employee_password(self, user_id: str, temporary_password: str, master_password: str) -> None:
        """Reautentica o Master, bloqueia sessões antigas e prepara a troca do funcionário."""
        user_id = _uuid(user_id)
        validate_password(temporary_password)
        context = self.require("users.manage")
        if context.profile.role != "Master" or not context.can("permissions.manage"):
            raise AuthorizationError("Somente o Master pode redefinir acessos.")
        if not isinstance(master_password, str) or not master_password or len(master_password) > 128:
            raise AuthenticationError("Confirme sua senha para redefinir o acesso.")
        current = self._current_session()
        identity = self.api.auth("GET", "user", access_token=current.access_token)
        if not isinstance(identity, dict) or _uuid(identity.get("id")) != current.user_id:
            raise AuthenticationError("Não foi possível confirmar sua identidade.")
        email = _email(identity.get("email"))
        proof = self._read_session(self.api.auth("POST", "token?grant_type=password", {
            "email": email, "password": master_password,
        }))
        try:
            if proof.user_id != current.user_id:
                raise AuthenticationError("Não foi possível confirmar sua identidade.")
        finally:
            self.api.auth("POST", "logout?scope=local", access_token=proof.access_token)
        started = self.rpc("admin_begin_password_reset", {"p_user_id": user_id}, "users.manage")
        operation_id = _uuid(started.get("operation_id") if isinstance(started, dict) else None)
        self.require("users.manage")
        self.api._reset_auth_password(user_id, temporary_password)
        self.rpc("admin_finish_password_reset", {"p_user_id": user_id, "p_operation_id": operation_id}, "users.manage")
