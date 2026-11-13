"""Verifica a conexão por uma consulta limitada, sem importar dados do ERP."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from integracoes.gestaoclick import (
    ContratoInvalido,
    GestaoClickConfig,
    GestaoClickError,
    TransporteConsulta,
    TransporteHTTPS,
    parse_json_seguro,
)
from sincronizacao.service import RPCAuth
from web.errors import AuthorizationError
from web.sales_service import access_stamp


def _stamp(auth: RPCAuth) -> tuple:
    """Confere Master ativo e permissão no backend, vinculando a identidade à sessão."""
    context = auth.require("integrations.manage")
    if context.profile.role != "Master" or not context.profile.active:
        raise AuthorizationError("Somente o Master ativo pode verificar a conexão.")
    return access_stamp(context, auth.generation)


def diagnosticar_conexao(
    auth: RPCAuth,
    carregar_configuracao: Callable[[], Mapping[str, Any]],
    *,
    autorizado: bool,
    transporte: TransporteConsulta | None = None,
) -> dict[str, Any]:
    """Faz um único GET de lojas após consentimento e autorização da organização.

    O vínculo protegido da organização evita que um Master de outra organização
    use as credenciais globais do servidor. A resposta comercial é descartada;
    o diagnóstico devolve apenas contagem e estado técnico. Não segue páginas,
    não repete chamadas e não comprova abrangência, saldo ou contrato de vendas.
    """
    with auth.operation():
        before = _stamp(auth)
        if autorizado is not True:
            raise AuthorizationError("Confirme a consulta real para verificar a conexão.")
        values = carregar_configuracao()
        if not isinstance(values, Mapping):
            raise ContratoInvalido("Configuração protegida do servidor inválida.")
        context = auth.require("integrations.manage")
        try:
            organization = str(UUID(values.get("GESTAOCLICK_ORGANIZATION_ID", "")))
        except (ValueError, TypeError, AttributeError):
            raise ContratoInvalido(
                "Configure GESTAOCLICK_ORGANIZATION_ID nos Secrets para vincular a conexão."
            ) from None
        if organization != context.profile.organization_id or before != _stamp(auth):
            raise AuthorizationError("Esta conexão não está autorizada para sua organização.")
        config = GestaoClickConfig.from_mapping(values)
        base = config.validar_conexao()
        # O caminho é fixo e documentado; nenhuma URL ou método vem do navegador.
        url = base + "/lojas?pagina=1&limite=1"
        try:
            response = (transporte or TransporteHTTPS()).get(url, {
                "access-token": config.access_token,
                "secret-access-token": config.secret_access_token,
                "Accept": "application/json",
            }, min(config.timeout_seconds, 15))
        except (TimeoutError, OSError):
            raise GestaoClickError("A consulta não respondeu; tente novamente mais tarde.") from None
        if before != _stamp(auth):
            raise AuthorizationError("O acesso mudou durante a verificação da conexão.")
        if response.status in {401, 403}:
            raise GestaoClickError("O GestãoClick recusou as credenciais ou as permissões de consulta.")
        if response.status == 429:
            raise GestaoClickError("Limite do GestãoClick atingido. Aguarde antes de verificar novamente.")
        if response.status != 200:
            raise GestaoClickError("Verificação recusada pelo GestãoClick; nenhum redirecionamento foi seguido.")
        payload = parse_json_seguro(response.body)
        rows = payload.get("data")
        if (type(payload.get("code")) is not int or payload["code"] != 200 or
                payload.get("status") != "success" or not isinstance(rows, list) or
                len(rows) > 100 or any(not isinstance(row, dict) for row in rows)):
            raise ContratoInvalido("A resposta não corresponde à consulta de lojas esperada.")
        return {"authenticated": True, "resource": "lojas", "records_received": len(rows),
                "coverage_confirmed": False, "imported": False}
