"""Diagnóstico local e vínculo do contrato aprovado, sem consultar o ERP."""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from typing import Any
from uuid import UUID

from integracoes.gestaoclick import ContratoInvalido, parse_json_seguro
from integracoes.runtime import build_reader

REQUIRED_CONFIGURATION = (
    "GESTAOCLICK_BASE_URL", "GESTAOCLICK_ACCESS_TOKEN", "GESTAOCLICK_SECRET_ACCESS_TOKEN",
    "GESTAOCLICK_ORGANIZATION_ID", "GESTAOCLICK_SOURCE_ID", "GESTAOCLICK_STORE_ID",
    "GESTAOCLICK_FIELD_MAP_JSON", "GESTAOCLICK_STATUS_MAP_JSON", "GESTAOCLICK_PERIOD_FILTERS_JSON",
)
CONTRACT_CONFIGURATION = ("GESTAOCLICK_OPENAPI_JSON", "GESTAOCLICK_READ_CONTRACT_JSON")


def missing_configuration(values: Mapping[str, Any]) -> list[str]:
    """Lista somente nomes de configurações ausentes; nunca retorna seus valores."""
    missing = [name for name in REQUIRED_CONFIGURATION if not values.get(name)]
    if not any(name in values for name in CONTRACT_CONFIGURATION):
        missing.append("GESTAOCLICK_READ_CONTRACT_JSON ou GESTAOCLICK_OPENAPI_JSON")
    return missing


def validated_scope(values: Mapping[str, Any], organization_id: str | None = None) -> tuple[str, str, str]:
    """Confere organização, fonte e loja antes de usar a credencial do servidor."""
    try:
        org = str(UUID(values.get("GESTAOCLICK_ORGANIZATION_ID", "")))
        source = str(UUID(values.get("GESTAOCLICK_SOURCE_ID", "")))
    except (ValueError, TypeError, AttributeError):
        raise ContratoInvalido("Configure os UUIDs válidos de organização e fonte nos Secrets.") from None
    if organization_id is not None and org != organization_id:
        raise ContratoInvalido("A configuração protegida não pertence à organização autenticada.")
    store = values.get("GESTAOCLICK_STORE_ID")
    if (not isinstance(store, str) or not 1 <= len(store.strip()) <= 120
            or any(ord(char) < 32 for char in store)):
        raise ContratoInvalido("Configure o identificador da loja conferido no GestãoClick.")
    return org, source, store


def contract_fingerprint(values: Mapping[str, Any]) -> str:
    """Vincula a aprovação a campos, filtros, loja e organização, sem incluir tokens.

    Trocar uma chave de autenticação não muda a semântica dos dados. Alterar o
    contrato, a loja ou o mapa muda o resumo e exige nova aprovação. A validação
    monta o leitor, mas não realiza qualquer requisição de rede.
    """
    reader = build_reader(values)
    org, source, store = validated_scope(values)
    document = {"version": 1, "base_url": reader.config.validar(),
                "organization_id": org, "source_id": source, "store_id": store}
    for name in (*CONTRACT_CONFIGURATION, "GESTAOCLICK_FIELD_MAP_JSON", "GESTAOCLICK_STATUS_MAP_JSON",
                 "GESTAOCLICK_PERIOD_FILTERS_JSON", "GESTAOCLICK_FIXED_FILTERS_JSON"):
        if name in values:
            document[name] = parse_json_seguro(values[name])
    # O filtro opcional ausente e o objeto vazio possuem a mesma semântica.
    document.setdefault("GESTAOCLICK_FIXED_FILTERS_JSON", {})
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return sha256(canonical.encode("utf-8")).hexdigest()
