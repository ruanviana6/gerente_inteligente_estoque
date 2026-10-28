"""Cliente HTTP mínimo para Supabase Auth e RPC, sem acesso ao ERP."""

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from web.errors import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    RateLimited,
    ServiceUnavailable,
)
from web.settings import Settings

MAX_RESPONSE_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True, repr=False)
class HTTPResponse:
    """Transporta status e bytes; repr não revela uma resposta que contenha tokens."""

    status: int
    body: bytes


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Impede que um redirecionamento encaminhe headers secretos para outra origem."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Recusa redirecionamento em vez de repetir credenciais automaticamente."""
        return None


def http_transport(
    method: str, url: str, headers: Mapping[str, str], payload: bytes | None, timeout: int,
) -> HTTPResponse:
    """Executa HTTPS com TLS padrão, proxy do ambiente, limite de leitura e timeout."""
    request = urllib.request.Request(url, data=payload, headers=dict(headers), method=method)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ServiceUnavailable("A resposta excedeu o limite permitido. Reduza o período consultado.")
            return HTTPResponse(response.status, body)
    except urllib.error.HTTPError as exc:
        # O corpo pode conter informações internas. Só seu status é necessário.
        status = exc.code
        exc.close()
        return HTTPResponse(status, b"")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ServiceUnavailable("Não foi possível acessar o Supabase. Tente novamente.") from None


Transport = Callable[[str, str, Mapping[str, str], bytes | None, int], HTTPResponse]


def _reject_constant(value: str) -> None:
    """JSON da API não admite NaN nem infinito, mesmo que o parser padrão aceite."""
    raise ValueError("Número inválido no JSON.")


class SupabaseAPI:
    """Isola transporte e chaves; consultas normais sempre usam a identidade do usuário."""

    def __init__(self, settings: Settings, transport: Transport | None = None) -> None:
        """Cria um cliente sem sessão compartilhada nem cache de resultados."""
        self.settings = settings
        self._transport = transport or http_transport

    def _request(
        self, method: str, path: str, payload: dict | None = None,
        access_token: str | None = None, *, administrative: bool = False,
    ) -> Any:
        """Serializa JSON estrito e converte falhas externas em mensagens seguras."""
        key = self.settings.service_role_key if administrative else self.settings.publishable_key
        if key is None:
            raise ConfigurationError("A criação de contas exige a configuração administrativa protegida.")
        headers = {"apikey": key, "Content-Type": "application/json", "Accept": "application/json"}
        bearer = key if administrative else access_token
        if bearer:
            if not isinstance(bearer, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,16384}", bearer):
                raise AuthenticationError("Sessão inválida. Entre novamente.")
            headers["Authorization"] = f"Bearer {bearer}"
        try:
            data = json.dumps(payload, allow_nan=False).encode("utf-8") if payload is not None else None
        except (ValueError, TypeError, OverflowError):
            raise AppError("Os dados enviados contêm um valor inválido.") from None
        try:
            response = self._transport(method, self.settings.supabase_url + path, headers, data, self.settings.timeout_seconds)
        except (TimeoutError, OSError):
            raise ServiceUnavailable("Não foi possível acessar o Supabase. Tente novamente.") from None
        if response.status == 429:
            raise RateLimited("Muitas tentativas. Aguarde alguns minutos antes de tentar novamente.")
        if response.status in {401, 403}:
            error = AuthenticationError if response.status == 401 else AuthorizationError
            raise error("Acesso negado ou sessão encerrada. Entre novamente ou consulte o Master.")
        if 400 <= response.status < 500:
            if path.startswith("/auth/v1/") and not administrative:
                raise AuthenticationError("Não foi possível autenticar ou atualizar a conta. Confira os dados.")
            raise AppError("A operação foi recusada. Confira os dados e as permissões.")
        if not 200 <= response.status < 300:
            raise ServiceUnavailable("O serviço está indisponível. Tente novamente mais tarde.")
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise ServiceUnavailable("A resposta excedeu o limite permitido.")
        if not response.body:
            return None
        try:
            return json.loads(response.body, parse_constant=_reject_constant)
        except (ValueError, UnicodeError):
            raise ServiceUnavailable("O serviço retornou uma resposta inválida.") from None

    def auth(
        self, method: str, path: str, payload: dict | None = None, access_token: str | None = None,
    ) -> Any:
        """Permite somente os endpoints de autenticação utilizados pela aplicação."""
        allowed = {
            ("POST", "token?grant_type=password"), ("POST", "token?grant_type=refresh_token"),
            ("GET", "user"), ("PUT", "user"), ("POST", "logout?scope=global"),
            ("POST", "recover"), ("POST", "verify"),
        }
        if (method, path) not in allowed:
            raise AuthorizationError("Endpoint de autenticação não permitido.")
        return self._request(method, "/auth/v1/" + path, payload, access_token)

    def rpc(self, name: str, payload: dict | None, access_token: str) -> Any:
        """Chama uma função publicada com JWT do usuário; RLS continua aplicada."""
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name):
            raise AppError("Nome de operação inválido.")
        if not access_token:
            raise AuthenticationError("Entre para continuar.")
        if payload is not None and not isinstance(payload, dict):
            raise AppError("Os parâmetros da operação devem formar um objeto.")
        parameters = dict(payload or {})
        # Somente a configuração do servidor pode identificar este backend. O
        # segredo não concede permissões; o banco também exige identidade ativa.
        parameters.pop("p_backend_secret", None)
        if name == "load_inventory":
            parameters["p_backend_secret"] = self.settings.backend_secret
        return self._request("POST", "/rest/v1/rpc/" + name, parameters, access_token)

    def _create_auth_user(self, email: str, password: str) -> dict:
        """Usa a chave administrativa somente neste endpoint, após autorização no AuthService."""
        return self._request(
            "POST", "/auth/v1/admin/users",
            {"email": email, "password": password, "email_confirm": True}, administrative=True,
        )

    def job_rpc(self, name: str, payload: dict) -> Any:
        """Limita o worker sem sessão às três funções protegidas pelo segredo dedicado."""
        if name not in {"report_job_claim", "report_job_authorize", "report_job_finish"}:
            raise AuthorizationError("Operação não permitida para o agendador.")
        secret = payload.get("p_job_secret") if isinstance(payload, dict) else None
        if not isinstance(secret, str) or len(secret) < 32:
            raise ConfigurationError("Configure o segredo dedicado do agendador em ambiente protegido.")
        # A credencial do job fica no corpo HTTPS, nunca na URL, nos logs ou no bearer.
        return self._request("POST", "/rest/v1/rpc/" + name, payload)
