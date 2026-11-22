"""Foto real autenticada da integração para a tela inicial.

Sem rede com o GestãoClick, sem importação nem cálculo de reposição. Usa
somente dados já validados e salvos no Supabase desta organização.
"""
from __future__ import annotations

from uuid import UUID

from analytics.observational_review import conferir_observacao
from integracoes.gestaoclick import ContratoInvalido
from web.errors import AuthorizationError
from web.sales_service import access_stamp


def selecionar_fonte_real(fontes: object) -> tuple[str, str] | None:
    """Escolhe somente uma fonte GestãoClick ativa e uma loja; nunca DEMO."""
    if not isinstance(fontes, list) or len(fontes) > 100:
        raise ContratoInvalido("Fontes reais não verificáveis.")
    candidatos = []
    for fonte in fontes:
        if not isinstance(fonte, dict):
            raise ContratoInvalido("Fonte analítica inválida.")
        nome, ativo, lojas = fonte.get("name"), fonte.get("active"), fonte.get("stores")
        if not isinstance(nome, str) or type(ativo) is not bool or not isinstance(lojas, list):
            raise ContratoInvalido("Fonte analítica incompleta.")
        if not ativo or not nome.casefold().startswith("gestãoclick:"):
            continue
        if len(lojas) != 1 or not isinstance(lojas[0], str) or not lojas[0].isdigit():
            continue
        try:
            fonte_id = str(UUID(fonte["id"]))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ContratoInvalido("Identificador da fonte real inválido.") from None
        candidatos.append((fonte_id, lojas[0]))
    return candidatos[0] if len(candidatos) == 1 else None


def consultar_status_real(auth):
    """Resumo seguro e não operacional, revalidando a sessão após RPC."""
    with auth.operation():
        acesso = auth.require("integrations.manage")
        if acesso.profile.role != "Master":
            raise AuthorizationError("Painel real disponível somente para Master.")
        stamp = access_stamp(acesso, auth.generation)
        fontes = auth.rpc("integration_sources", {}, "trends.view")
        selecionado = selecionar_fonte_real(fontes)
        if selecionado is None:
            return None
        source_id, store_id = selecionado
        bruto = auth.rpc(
            "demand_observation_review",
            {"p_source_id": source_id, "p_store_id": store_id},
            "integrations.manage",
        )
        if stamp != access_stamp(auth.require("integrations.manage"), auth.generation):
            raise AuthorizationError("A autorização mudou durante a consulta.")
        return conferir_observacao(bruto, store_id)
