"""Prévia de catálogo/estoque da loja via GET, sem persistência ou escrita no ERP.

Usa a mesma conta Master conferida para as vendas. Os IDs e códigos do catálogo
não são automaticamente vinculados aos IDs de itens de vendas; requerem
reconciliação antes de qualquer importação ou cálculo comercial.
"""

from __future__ import annotations

import hmac
import math
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from integracoes.account_setup import ContaGestaoClick, _configuracao, _validar_conta, _vinculo_configuracao
from integracoes.diagnostico import _stamp
from integracoes.gestaoclick import (
    ContratoInvalido,
    GestaoClickError,
    TransporteConsulta,
    TransporteHTTPS,
    parse_json_seguro,
)
from sincronizacao.service import RPCAuth
from web.errors import AuthorizationError

MAX_PRODUTOS_EXIBIDOS = 5
MAX_PRODUTOS_RECEBIDOS = 100
MAX_VARIACOES_RECEBIDAS = 100


@dataclass(frozen=True, repr=False)
class VariacaoCatalogo:
    """ID de linha do estoque na loja, não assumido como ID de item vendido."""

    id: str | None
    codigo: str
    estoque: Decimal | None
    variacao_api_id: str | None


@dataclass(frozen=True, repr=False)
class ProdutoCatalogo:
    """Amostra do catálogo com campos operacionais mínimos, sem preços ou clientes."""

    id: str
    codigo_interno: str
    estoque: Decimal | None
    variacoes: tuple[VariacaoCatalogo, ...]


@dataclass(frozen=True, repr=False)
class PreviaCatalogo:
    """Uma página de consulta; não comprova cobertura de loja ou movimentações."""

    loja_solicitada: str
    produtos: tuple[ProdutoCatalogo, ...]
    recebidos: int
    total_informado: int | None
    imported: bool = field(default=False, init=False)
    coverage_confirmed: bool = field(default=False, init=False)


class IdentificadorCatalogoInvalido(ContratoInvalido):
    """Diagnóstico tipado sem transmitir identificadores nem conteúdo do ERP."""

    def __init__(self, campo: str, tipo: str, motivo: str):
        # Os argumentos são códigos fixos da aplicação, nunca valores da API.
        super().__init__("O catálogo retornou um identificador inválido. Nenhum saldo foi importado.")
        self.diagnostico = {"campo": campo, "tipo": tipo, "motivo": motivo}


def _tipo_id(value: Any, presente: bool) -> str:
    """Classifica somente a espécie do campo, sem incluir o valor recebido."""
    if not presente:
        return "ausente"
    if value is None:
        return "nulo"
    if type(value) is bool:
        return "booleano"
    if type(value) is int:
        return "inteiro"
    if isinstance(value, str):
        return "texto"
    if type(value) is float:
        return "decimal"
    if isinstance(value, Mapping):
        return "objeto"
    if isinstance(value, list):
        return "lista"
    return "outro"


def _id(value: Any, *, campo: str, presente: bool) -> str:
    """Confere IDs sem aceitar suposições sobre a estrutura da conta real."""
    tipo = _tipo_id(value, presente)
    if tipo == "ausente":
        raise IdentificadorCatalogoInvalido(campo, tipo, "CAMPO_AUSENTE")
    if tipo == "nulo":
        raise IdentificadorCatalogoInvalido(campo, tipo, "CAMPO_NULO")
    if type(value) is int:
        raw = str(value)
    elif isinstance(value, str):
        raw = value
    else:
        raise IdentificadorCatalogoInvalido(campo, tipo, "TIPO_INCOMPATIVEL")
    if not raw:
        raise IdentificadorCatalogoInvalido(campo, tipo, "TEXTO_VAZIO")
    if len(raw) > 30:
        raise IdentificadorCatalogoInvalido(campo, tipo, "COMPRIMENTO")
    if not re.fullmatch(r"[0-9]+", raw):
        raise IdentificadorCatalogoInvalido(campo, tipo, "NAO_NUMERICO")
    if int(raw) <= 0:
        raise IdentificadorCatalogoInvalido(campo, tipo, "NAO_POSITIVO")
    return raw


def _codigo(value: Any, *, opcional: bool = False) -> str | None:
    """Preserva códigos operacionais sem reproduzir controles ou conteúdo HTML."""
    if opcional and value is None:
        return None
    if type(value) is int:
        value = str(value)
    if (not isinstance(value, str) or (not value.strip() and not opcional) or
            len(value) > 100 or any(
                unicodedata.category(ch).startswith("C") or ch in "<>" for ch in value
            )):
        raise ContratoInvalido("O catálogo retornou um código inválido.")
    return value.strip() or None


def _estoque(value: Any) -> Decimal | None:
    """Preserva quantidades decimais, inclusive negativas, sem converter unidades."""
    if value is None:
        return None
    if type(value) is int:
        raw = str(value)
    elif type(value) is float and math.isfinite(value):
        # A prévia pode receber números decimais no JSON. Apenas representa:
        # nunca arredonda e jamais persiste esse valor como saldo validado.
        raw = str(value)
    elif isinstance(value, str):
        raw = value
    else:
        raise ContratoInvalido("O catálogo retornou uma quantidade de estoque inválida.")
    if not re.fullmatch(r"-?[0-9]{1,10}(?:\.[0-9]{1,6})?", raw):
        raise ContratoInvalido("O catálogo retornou uma quantidade de estoque inválida.")
    result = Decimal(raw)
    if abs(result) > Decimal("1000000000"):
        raise ContratoInvalido("O catálogo retornou uma quantidade de estoque inválida.")
    return result


def _projetar(body: dict) -> tuple[tuple[ProdutoCatalogo, ...], int, int | None]:
    """Confere a página inteira antes de mostrar os cinco primeiros produtos."""
    rows = body.get("data")
    if (type(body.get("code")) is not int or body["code"] != 200
            or body.get("status") != "success"
            or not isinstance(rows, list) or len(rows) > MAX_PRODUTOS_RECEBIDOS):
        raise ContratoInvalido("A prévia do catálogo não corresponde ao retorno documentado.")
    meta = body.get("meta")
    total = None
    if meta is not None:
        if not isinstance(meta, dict):
            raise ContratoInvalido("Metadados inválidos na prévia do catálogo.")
        if "pagina_atual" in meta and (type(meta["pagina_atual"]) is not int
                                      or meta["pagina_atual"] != 1):
            raise ContratoInvalido("A prévia do catálogo não corresponde à primeira página.")
        if "total_registros" in meta:
            n = meta["total_registros"]
            if type(n) is not int or n < len(rows):
                raise ContratoInvalido("A contagem da prévia do catálogo é inconsistente.")
            total = n
    products = []
    seen = set()
    variation_count = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ContratoInvalido("A prévia do catálogo contém um produto inválido.")
        id_prod = _id(row.get("id"), campo="produto.id", presente="id" in row)
        if id_prod in seen:
            raise ContratoInvalido("A prévia do catálogo contém produto repetido.")
        seen.add(id_prod)
        variations = row.get("variacoes", [])
        if not isinstance(variations, list):
            raise ContratoInvalido("As variações do produto estão em formato inválido.")
        variation_count += len(variations)
        if variation_count > MAX_VARIACOES_RECEBIDAS:
            raise ContratoInvalido("A prévia excede o limite de variações permitido.")
        parsed = []
        ids = set()
        for variation in variations:
            if not isinstance(variation, dict):
                raise ContratoInvalido("A prévia contém uma variação inválida.")
            # Um retorno sem ID contraria o contrato documentado do ERP.
            # A prévia pode continuar, mas não atribui saldo a uma linha sem identidade.
            # ID nulo ou inválido continua sendo um erro e nunca gera um ID sintético.
            vid = (_id(variation["id"], campo="variacao.id", presente=True)
                   if "id" in variation else None)
            if vid is not None:
                if vid in ids:
                    raise ContratoInvalido("A prévia contém variação repetida no produto.")
                ids.add(vid)
            codigo = _codigo(variation.get("codigo"))
            estoque = _estoque(variation.get("estoque"))
            parsed.append(VariacaoCatalogo(
                id=vid, codigo=codigo,
                estoque=estoque if vid is not None else None,
                variacao_api_id=_codigo(variation.get("variacao_api_id"), opcional=True),
            ))
        if len(products) < MAX_PRODUTOS_EXIBIDOS:
            products.append(ProdutoCatalogo(
                id=id_prod, codigo_interno=_codigo(row.get("codigo_interno")),
                estoque=_estoque(row.get("estoque")), variacoes=tuple(parsed),
            ))
    return tuple(products), len(rows), total


def consultar_previa_catalogo(
    auth: RPCAuth,
    carregar_configuracao: Callable[[], Mapping[str, Any]],
    conta: ContaGestaoClick,
    store_id: str,
    *,
    autorizado: bool,
    transporte: TransporteConsulta | None = None,
) -> PreviaCatalogo:
    """Um GET protegido de até cinco produtos; sem salvar estoque ou alterar KPI."""
    with auth.operation():
        stamp = _stamp(auth)
        if autorizado is not True:
            raise AuthorizationError("Confirme a consulta da amostra real do catálogo.")
        _validar_conta(auth, conta)
        if not isinstance(store_id, str) or store_id not in {x["id"] for x in conta.lojas}:
            raise AuthorizationError("Selecione uma loja autorizada para consultar o catálogo.")
        config = _configuracao(auth, carregar_configuracao, stamp, True)
        if not hmac.compare_digest(conta._config_stamp, _vinculo_configuracao(config)):
            raise AuthorizationError("A configuração da conta mudou. Consulte as lojas novamente.")

        def revalidar():
            _validar_conta(auth, conta)
            if _configuracao(auth, carregar_configuracao, stamp, True) != config:
                raise AuthorizationError("A configuração protegida mudou durante a consulta.")

        revalidar()
        query = urlencode({"loja_id": store_id, "pagina": 1, "limite": MAX_PRODUTOS_EXIBIDOS})
        url = config.validar_conexao() + "/produtos?" + query
        try:
            try:
                response = (transporte or TransporteHTTPS()).get(url, {
                    "access-token": config.access_token,
                    "secret-access-token": config.secret_access_token,
                    "Accept": "application/json",
                }, min(config.timeout_seconds, 15))
            except Exception:
                raise GestaoClickError("O catálogo não respondeu. Nenhum estoque foi importado.") from None
            revalidar()
            if type(response.status) is not int:
                raise GestaoClickError("O catálogo retornou estado inválido.")
            if response.status in {401, 403}:
                raise GestaoClickError("O GestãoClick recusou as permissões de consultar produtos.")
            if response.status == 429:
                raise GestaoClickError("Limite de consultas do GestãoClick atingido.")
            if response.status != 200:
                raise GestaoClickError("O GestãoClick recusou a consulta ao catálogo.")
            products, received, total = _projetar(parse_json_seguro(response.body))
            return PreviaCatalogo(store_id, products, received, total)
        finally:
            revalidar()
