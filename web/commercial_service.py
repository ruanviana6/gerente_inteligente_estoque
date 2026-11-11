"""Fronteira de autorização dos resultados comerciais previamente calculados."""

from __future__ import annotations

from web.errors import AuthorizationError, ServiceUnavailable
from web.sales_service import access_stamp


def commercial_rpc(auth, name: str, payload: dict, permission: str, *, master: bool = False):
    """Revalida identidade, sessão e concessões antes e depois de qualquer RPC."""
    with auth.operation():
        contexto = auth.require(permission)
        if master and contexto.profile.role != "Master":
            raise AuthorizationError("Operação exclusiva do Master.")
        before = access_stamp(contexto, auth.generation)
        result = auth.rpc(name, payload, permission)
        if before != access_stamp(auth.require(permission), auth.generation):
            raise AuthorizationError("O acesso mudou durante a consulta comercial.")
        if not isinstance(result, (dict, list)):
            raise ServiceUnavailable("Resposta comercial indisponível.")
        return result
