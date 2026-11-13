"""Monta a leitura GestãoClick a partir de configuração protegida do servidor.

Não há rotas ou métodos recebidos da interface. A tela escolhe somente um
período civil; contrato, campos, situações e filtros vêm dos Secrets revisados.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from integracoes.gestaoclick import (
    ContratoGestaoClick,
    ContratoInvalido,
    GestaoClickClient,
    GestaoClickConfig,
    MapaDocumentoGestaoClick,
    MapaLinhaGestaoClick,
    TransporteConsulta,
    normalizar_documentos,
    normalizar_linhas,
    parse_json_seguro,
)

_REQUIRED_FIELDS = frozenset({
    "document_id", "line_id", "product_id", "sku", "sale_date", "status", "quantity",
    "returned_quantity", "store_id",
})
_OPTIONAL_FIELDS = frozenset({"product_name", "category", "supplier", "source_updated_at"})
_STATUS = frozenset({"potential", "separating", "completed", "cancelled", "returned", "unknown"})
_FORBIDDEN_FILTERS = frozenset({"pagina", "limite", "usuario_id", "loja_id"})


def _json(values: Mapping[str, Any], name: str, *, optional: bool = False) -> dict:
    """Lê apenas objetos JSON limitados, com mensagens que não exibem seu conteúdo."""
    raw = values.get(name, "{}" if optional else None)
    if not isinstance(raw, str):
        raise ContratoInvalido(f"Configure {name} como JSON protegido do servidor.")
    try:
        return parse_json_seguro(raw)
    except ContratoInvalido:
        raise ContratoInvalido(f"O JSON protegido {name} é inválido.") from None


def _path(value: Any) -> tuple[str, ...]:
    """Converte um caminho declarativo, recusando expressões e percursos profundos."""
    if (not isinstance(value, list) or not 1 <= len(value) <= 6 or
            any(not isinstance(part, str) or not 1 <= len(part) <= 120 or
                any(ord(char) < 32 for char in part) for part in value)):
        raise ContratoInvalido("O mapa de campos deve conter caminhos em listas de texto.")
    return tuple(value)


def _fields(value: Any) -> dict[str, tuple[str, ...]]:
    """Admite somente colunas analíticas e impede inclusão de dados pessoais arbitrários."""
    if not isinstance(value, dict) or set(value) - (_REQUIRED_FIELDS | _OPTIONAL_FIELDS):
        raise ContratoInvalido("Campo fora da allowlist de análise de vendas.")
    return {key: _path(path) for key, path in value.items()}


def _day(value: Any) -> date:
    """Normaliza data civil ISO sem aceitar datetime ou interpretação ambígua."""
    if type(value) is date:
        return value
    if not isinstance(value, str):
        raise ContratoInvalido("Informe o período com datas civis válidas.")
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise ContratoInvalido("Informe o período em AAAA-MM-DD.") from None
    if result.isoformat() != value:
        raise ContratoInvalido("Informe o período em AAAA-MM-DD.")
    return result


@dataclass(frozen=True, repr=False)
class GestaoClickReader:
    """Lê um período autorizado, validando cobertura antes de entregar linhas ao sync."""

    config: GestaoClickConfig
    _contrato: ContratoGestaoClick
    _mapa: MapaDocumentoGestaoClick | MapaLinhaGestaoClick
    _period_filters: Mapping[str, str]
    _fixed_filters: Mapping[str, str]
    _transport: TransporteConsulta | None = None

    def read(self, start: date | str, end: date | str) -> list[dict]:
        """Consulta vendas GET e normaliza o resultado completo, sem gravar no banco.

        A comparação de cada data impede aceitar um filtro temporal ignorado
        silenciosamente pela origem. A contagem ainda requer reconciliação com
        a conta/loja autorizada; uma lista vazia não demonstra sua abrangência.
        """
        first, last = _day(start), _day(end)
        today = datetime.now(ZoneInfo("America/Fortaleza")).date()
        if first > last or last > today or (last - first).days > 3660:
            raise ContratoInvalido("Período inválido, futuro ou maior que dez anos.")
        filters = {**self._fixed_filters,
                   self._period_filters["start"]: first.isoformat(),
                   self._period_filters["end"]: last.isoformat()}
        client = GestaoClickClient(self.config, self._contrato, self._transport)
        response = client.consultar("vendas", filters)
        normalizer = normalizar_documentos if isinstance(self._mapa, MapaDocumentoGestaoClick) else normalizar_linhas
        rows = normalizer(response.registros, self._mapa, self.config.source_id, self.config.store_id)
        if any(not first.isoformat() <= row["sale_date"] <= last.isoformat() for row in rows):
            raise ContratoInvalido("A origem retornou vendas fora do período; confira os filtros no contrato.")
        return rows


def build_reader(
    values: Mapping[str, Any], transport: TransporteConsulta | None = None,
) -> GestaoClickReader:
    """Valida Secrets e monta leitor fechado, sem qualquer requisição de rede.

    O OpenAPI ou contrato declarativo, os mapas de campos/status e os nomes de
    filtros precisam ser conferidos pelo administrador. Nenhum vem da navegação
    ou de uploads da aplicação. A primeira leitura continua exigindo Master e
    autorização explícita no coordenador de sincronização.
    """
    if not isinstance(values, Mapping):
        raise ContratoInvalido("Configuração protegida do servidor inválida.")
    config = GestaoClickConfig.from_mapping(values)
    config.validar()
    contract_names = {"GESTAOCLICK_OPENAPI_JSON", "GESTAOCLICK_READ_CONTRACT_JSON"}
    configured_contracts = contract_names & values.keys()
    if len(configured_contracts) != 1:
        raise ContratoInvalido("Configure exatamente um contrato: OpenAPI ou contrato de leitura JSON.")
    contract_name = configured_contracts.pop()
    spec = _json(values, contract_name)
    builder = (ContratoGestaoClick.from_openapi if contract_name == "GESTAOCLICK_OPENAPI_JSON"
               else ContratoGestaoClick.from_read_contract)
    contrato = builder(spec, config.base_url, ("vendas",))
    field_map = _json(values, "GESTAOCLICK_FIELD_MAP_JSON")
    statuses = _json(values, "GESTAOCLICK_STATUS_MAP_JSON")
    if (not statuses or any(not isinstance(value, str) for value in statuses.values()) or
            set(statuses.values()) - _STATUS or
            any(not isinstance(key, str) or not 1 <= len(key) <= 120 for key in statuses)):
        raise ContratoInvalido("O mapa de status da conta é inválido.")
    mode = field_map.get("mode")
    if mode == "documents":
        if set(field_map) != {"mode", "lines_path", "document_fields", "line_fields"}:
            raise ContratoInvalido("Mapa de documentos incompleto ou com opção não permitida.")
        document_fields = _fields(field_map["document_fields"])
        line_fields = _fields(field_map["line_fields"])
        if document_fields.keys() & line_fields.keys():
            raise ContratoInvalido("Campos de documento e linha não podem se sobrepor.")
        keys = document_fields.keys() | line_fields.keys()
        mapa: MapaDocumentoGestaoClick | MapaLinhaGestaoClick = MapaDocumentoGestaoClick(
            _path(field_map["lines_path"]), document_fields, line_fields, statuses,
        )
    elif mode == "lines":
        if set(field_map) != {"mode", "fields"}:
            raise ContratoInvalido("Mapa de linhas incompleto ou com opção não permitida.")
        fields = _fields(field_map["fields"])
        keys = fields.keys()
        mapa = MapaLinhaGestaoClick(fields, statuses)
    else:
        raise ContratoInvalido("O mapa de campos deve definir mode documents ou lines.")
    if not _REQUIRED_FIELDS.issubset(keys):
        raise ContratoInvalido("O mapa não contém todos os campos obrigatórios da análise.")
    period = _json(values, "GESTAOCLICK_PERIOD_FILTERS_JSON")
    endpoint_filters = contrato.recursos["vendas"].filtros
    if (set(period) != {"start", "end"} or
            any(not isinstance(value, str) for value in period.values()) or
            len(set(period.values())) != 2 or set(period.values()) - endpoint_filters or
            set(period.values()) & _FORBIDDEN_FILTERS):
        raise ContratoInvalido("Filtros temporais não confirmados no GET do contrato.")
    fixed = _json(values, "GESTAOCLICK_FIXED_FILTERS_JSON", optional=True)
    if (set(fixed) - endpoint_filters or set(fixed) & (_FORBIDDEN_FILTERS | set(period.values())) or
            any(not isinstance(value, str) or not value or len(value) > 512 or
                any(ord(char) < 32 for char in value) for value in fixed.values())):
        raise ContratoInvalido("Filtros fixos não autorizados pelo contrato da conta.")
    return GestaoClickReader(config, contrato, mapa, period, fixed, transport)
