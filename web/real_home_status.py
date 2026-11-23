"""Leituras reais por loja, sem consolidar automaticamente lojas distintas.

As consultas não acionam o GestãoClick. Uma loja só pode ser usada se a
fonte ativa vier da RPC de integração autorizada na sessão corrente.
"""
from __future__ import annotations

from uuid import UUID

from analytics.observational_review import conferir_observacao
from integracoes.gestaoclick import ContratoInvalido
from web.errors import AuthorizationError
from web.sales_service import access_stamp


def listar_fontes_reais(fontes: object) -> tuple[tuple[str, str], ...]:
    """Enumera fontes GestãoClick de loja única e rejeita mapeamentos ambíguos."""
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
        if len(lojas) != 1 or not isinstance(lojas[0], str) or not lojas[0].isascii() or not lojas[0].isdigit():
            continue
        try:
            fonte_id = str(UUID(fonte["id"]))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ContratoInvalido("Identificador da fonte real inválido.") from None
        candidatos.append((fonte_id, lojas[0]))
    if (len(candidatos) != len(set(candidatos))
            or len(candidatos) != len({loja for _, loja in candidatos})):
        raise ContratoInvalido("Existem fontes duplicadas para a mesma loja real.")
    return tuple(sorted(candidatos, key=lambda par: (int(par[1]), par[0])))


def selecionar_fonte_real(fontes: object, loja_id: str | None = None) -> tuple[str, str] | None:
    """Uma loja selecionada precisa estar autorizada; sem seleção, exige unicidade."""
    candidatas = listar_fontes_reais(fontes)
    if loja_id is None:
        return candidatas[0] if len(candidatas) == 1 else None
    if not isinstance(loja_id, str) or not loja_id.isascii() or not loja_id.isdigit():
        return None
    encontradas = [par for par in candidatas if par[1] == loja_id]
    return encontradas[0] if len(encontradas) == 1 else None


def consultar_fontes_reais(auth) -> tuple[tuple[str, str], ...]:
    """Obtém lojas reais da RPC revalidando Master e geração da sessão."""
    with auth.operation():
        acesso = auth.require("integrations.manage")
        if acesso.profile.role != "Master":
            raise AuthorizationError("Fontes reais disponíveis somente para Master.")
        stamp = access_stamp(acesso, auth.generation)
        fontes = listar_fontes_reais(auth.rpc("integration_sources", {}, "trends.view"))
        if stamp != access_stamp(auth.require("integrations.manage"), auth.generation):
            raise AuthorizationError("A autorização mudou durante a seleção de loja.")
        return fontes


def consultar_status_real(auth, loja_id: str | None = None):
    """Retorna somente a observação validada da loja autorizada nesta sessão."""
    with auth.operation():
        acesso = auth.require("integrations.manage")
        if acesso.profile.role != "Master":
            raise AuthorizationError("Painel real disponível somente para Master.")
        stamp = access_stamp(acesso, auth.generation)
        fonte = selecionar_fonte_real(auth.rpc("integration_sources", {}, "trends.view"), loja_id)
        if fonte is None:
            return None
        source_id, store_id = fonte
        bruto = auth.rpc(
            "demand_observation_review",
            {"p_source_id": source_id, "p_store_id": store_id},
            "integrations.manage",
        )
        if stamp != access_stamp(auth.require("integrations.manage"), auth.generation):
            raise AuthorizationError("A autorização mudou durante a consulta.")
        return conferir_observacao(bruto, store_id)
