"""Transforma somente resultados da auditoria em observação histórica (não operacional).

As vendas e o catálogo já foram consultados via GET autorizado e conciliados.
Esta projeção NÃO fabrica line_id, source_updated_at nem devolução e não fornece
dados para reposição ou para NF-e. Somente quantitativos agregados entram no RPC.
"""
from __future__ import annotations

import re
from decimal import Decimal
from uuid import UUID

from analytics.sales_audit import ConciliacaoVendas
from integracoes.gestaoclick import ContratoInvalido
from integracoes.sales_audit import AuditoriaVendas


def construir_observacao(
    auditoria: AuditoriaVendas,
    resumo: ConciliacaoVendas,
    source_id: str,
) -> dict:
    """Exige a conciliação íntegra e quantidade UN exata antes de gravar histórico."""
    if (not isinstance(auditoria, AuditoriaVendas) or
            not isinstance(resumo, ConciliacaoVendas)):
        raise ContratoInvalido("A observação exige uma auditoria integral conciliada.")
    try:
        fonte = str(UUID(source_id))
    except (TypeError, ValueError, AttributeError):
        raise ContratoInvalido("A fonte analítica não foi autorizada.") from None
    if (not isinstance(auditoria.loja_solicitada, str) or
            not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", auditoria.loja_solicitada) or
            auditoria.total_informado != resumo.total_documentos or
            auditoria.total_informado != len(auditoria.documentos) or
            not 0 <= (auditoria.fim - auditoria.inicio).days <= 6 or
            auditoria.itens != sum(len(doc.itens) for doc in auditoria.documentos) or
            resumo.documentos_fechados != resumo.documentos_separacao + resumo.documentos_concretizados or
            resumo.total_documentos != resumo.documentos_fechados + resumo.documentos_nao_classificados or
            resumo.itens_fechados > auditoria.itens or
            resumo.linhas_unidade_nao_confirmada + resumo.linhas_unidade_nao_suportada >
                resumo.itens_fechados):
        raise ContratoInvalido("Os totais da observação e da auditoria divergem.")
    linhas = []
    vistos = set()
    total = Decimal(0)
    for ident, quantidade in sorted(resumo.agrupamento, key=lambda linha: int(linha[0])):
        if (not isinstance(ident, str) or not re.fullmatch(r"[0-9]{1,30}", ident) or
                int(ident) < 1 or ident in vistos or
                not isinstance(quantidade, Decimal) or not quantidade.is_finite() or
                not Decimal(0) < quantidade <= Decimal("1000000000") or
                max(0, -quantidade.as_tuple().exponent) > 6):
            raise ContratoInvalido("A agregação contém produto ou quantidade inválidos.")
        vistos.add(ident)
        total += quantidade
        linhas.append({"product_id": ident, "observed_un": format(quantidade, "f")})
    if total != resumo.unidades_fechadas_un:
        raise ContratoInvalido("As quantidades do agrupamento não conferem com o total.")
    if len(linhas) > 1000:
        raise ContratoInvalido("A observação excede mil produtos.")
    return {
        "p_source_id": fonte,
        "p_store_id": auditoria.loja_solicitada,
        "p_period_start": auditoria.inicio.isoformat(),
        "p_period_end": auditoria.fim.isoformat(),
        "p_documents": auditoria.total_informado,
        "p_separating": resumo.documentos_separacao,
        "p_completed": resumo.documentos_concretizados,
        "p_other": resumo.documentos_nao_classificados,
        "p_items": auditoria.itens,
        "p_unknown_unit": resumo.linhas_unidade_nao_confirmada,
        "p_unsupported_unit": resumo.linhas_unidade_nao_suportada,
        "p_rows": linhas,
        "p_confirmed": True,
    }
