"""Consulta analítica autorizada, sem consultar o ERP durante a navegação."""
from __future__ import annotations

import math
from datetime import date, timedelta
from uuid import UUID

from analytics.sales import analisar_vendas
from web.errors import AuthorizationError, ServiceUnavailable

MAX_ANALYSIS_DAYS = 1826


def _source_list(value):
    """Projeta metadados conhecidos, recusando respostas remotas sem contrato."""
    if not isinstance(value, list) or len(value) > 100:
        raise ServiceUnavailable("A consulta de fontes retornou uma estrutura inválida.")
    result = []
    seen = set()
    for source in value:
        try:
            if not isinstance(source, dict):
                raise ValueError
            source_id = str(UUID(source["id"]))
            name, stores, active = source["name"], source["stores"], source["active"]
            if (source_id in seen or not isinstance(name, str) or not 1 <= len(name.strip()) <= 120
                    or not isinstance(stores, list) or len(stores) > 30 or type(active) is not bool):
                raise ValueError
            if any(not isinstance(store, str) or not 1 <= len(store.strip()) <= 120 for store in stores):
                raise ValueError
            if len(set(stores)) != len(stores):
                raise ValueError
            seen.add(source_id)
            last = source.get("last_sync")
            if last is not None:
                if not isinstance(last, dict):
                    raise ValueError
                finished = last.get("finished_at")
                count = last.get("rows", 0)
                duration = last.get("duration_seconds")
                if (not isinstance(finished, str) or len(finished) > 100 or type(count) is not int or count < 0
                        or (duration is not None and (isinstance(duration, bool)
                            or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0))):
                    raise ValueError
                last = {"finished_at": finished, "rows": count, "duration_seconds": duration}
            result.append({"id": source_id, "name": name, "active": active, "stores": list(stores),
                           "last_sync": last})
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ServiceUnavailable("A consulta de fontes retornou uma estrutura inválida.") from None
    return result


def _sales_payload(value, source_id, store_id):
    """Recusa dados fora do escopo e não aceita cobertura concedida por valor truthy."""
    if (not isinstance(value, dict) or set(value) - {"rows", "metadata", "source_id", "store_id"}
            or not isinstance(value.get("rows"), list) or len(value["rows"]) > 20_000
            or not isinstance(value.get("metadata"), dict)):
        raise ServiceUnavailable("A consulta de vendas retornou uma estrutura inválida.")
    if value.get("source_id") != source_id or value.get("store_id") != store_id:
        raise AuthorizationError("A resposta não corresponde à fonte e à loja autorizadas.")
    for row in value["rows"]:
        if not isinstance(row, dict):
            raise ServiceUnavailable("A consulta de vendas retornou uma estrutura inválida.")
        if row.get("source_id") != source_id or row.get("store_id") != store_id:
            raise AuthorizationError("A resposta contém dados fora do escopo autorizado.")
    meta = value["metadata"]
    if type(meta.get("coverage_verified", False)) is not bool:
        raise ServiceUnavailable("A declaração de abrangência da fonte é inválida.")
    if type(meta.get("period_end_coverage_verified", False)) is not bool:
        raise ServiceUnavailable("A declaração de abrangência da fonte é inválida.")
    return value


def access_stamp(context, generation):
    """Vincula resultados à identidade, organização, permissões e vida da sessão."""
    p = context.profile
    return (p.user_id, p.organization_id, p.role, tuple(sorted(context.permissions)), generation)


class SalesService:
    """Usa o JWT individual e revalida acesso também após os cálculos."""

    def __init__(self, auth):
        """Recebe o serviço da sessão; não mantém dados ou autorização em cache."""
        self.auth = auth

    def sources(self):
        """Obtém somente fontes autorizadas na organização ativa."""
        with self.auth.operation():
            before = access_stamp(self.auth.require("trends.view"), self.auth.generation)
            result = _source_list(self.auth.rpc("integration_sources", {}, "trends.view"))
            if before != access_stamp(self.auth.require("trends.view"), self.auth.generation):
                raise AuthorizationError("O acesso mudou durante a consulta.")
            return result

    def analyze(self, source_id, store_id, start, end, *, product_id=None, category=None):
        """Inclui período anterior equivalente, sem consolidar fontes ou lojas."""
        if (type(start) is not date or type(end) is not date
                or (end - start).days not in range(MAX_ANALYSIS_DAYS)):
            raise ValueError("Escolha um período ordenado de até 1826 dias.")
        try:
            source_id = str(UUID(source_id))
        except (TypeError, ValueError, AttributeError):
            raise ValueError("Selecione uma fonte autorizada válida.") from None
        if not isinstance(store_id, str) or not 1 <= len(store_id.strip()) <= 120:
            raise ValueError("Selecione uma loja autorizada válida.")
        with self.auth.operation():
            before = access_stamp(self.auth.require("trends.view"), self.auth.generation)
            previous_start = start - timedelta(days=(end - start).days + 1)
            payload = _sales_payload(self.auth.rpc("sales_payload", {
                "p_source_id": source_id, "p_store_id": store_id,
                "p_start": previous_start.isoformat(), "p_end": end.isoformat(),
            }, "trends.view"), source_id, store_id)
            meta = payload.get("metadata", {})
            cobertura = (meta.get("coverage_verified") is True
                         or meta.get("period_end_coverage_verified") is True)
            try:
                result = analisar_vendas(payload["rows"], start, end,
                    coverage_start=meta.get("coverage_start") if cobertura else None,
                    coverage_end=meta.get("coverage_end") if cobertura else None,
                    source_id=source_id, store_id=store_id, product_id=product_id, category=category)
            except (TypeError, ValueError, KeyError):
                raise ServiceUnavailable("Os dados de vendas não atendem ao contrato analítico. Confira a origem.") from None
            if before != access_stamp(self.auth.require("trends.view"), self.auth.generation):
                raise AuthorizationError("O acesso mudou durante a análise.")
            return result
