"""Confere uma amostra nativa de vendas, sem persistir nem calcular indicadores.

O contrato fechado usa o GET /vendas documentado em api.gestaoclick.com/docs.
A resposta é projetada no servidor: clientes, valores, observações e credenciais
nunca integram o resultado. Identidades e devoluções ausentes continuam ausentes.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from integracoes.account_setup import (
    ContaGestaoClick,
    _conferir_pagina_situacoes,
    _configuracao,
    _validar_conta,
    _vinculo_configuracao,
)
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

VERSAO_CONTRATO = "gestaoclick-vendas-previa-20261116.1"
MAX_DOCUMENTOS_EXIBIDOS = 5
MAX_DOCUMENTOS_RECEBIDOS = 100
MAX_ITENS_RECEBIDOS = 100
MAX_DIAS_PREVIA = 31
_TIPOS = frozenset({"produto", "vendas_balcao"})


@dataclass(frozen=True, repr=False)
class ItemPreviaVenda:
    """Preserva produto e variação sem transformar IDs em SKU ou inventar linhas."""

    produto_id: str
    variacao_id: str | None
    quantidade: Decimal
    source_line_id: None = field(default=None, init=False)
    sku: None = field(default=None, init=False)
    returned_quantity: None = field(default=None, init=False)


@dataclass(frozen=True, repr=False)
class DocumentoPreviaVenda:
    """Representa somente os campos comerciais mínimos conferidos da amostra."""

    id: str
    codigo: str
    data: date
    situacao_id: str
    itens: tuple[ItemPreviaVenda, ...]
    source_updated_at: None = field(default=None, init=False)


@dataclass(frozen=True, repr=False)
class PreviaVendasNativas:
    """Entrega uma amostra efêmera; total declarado não comprova abrangência.

    loja_solicitada é a proveniência do filtro enviado, não uma identificação
    retornada em cada venda. Nenhum resultado desta classe pode ser consolidado
    como linha canônica pelo sincronizador atual.
    """

    loja_solicitada: str
    inicio: date
    fim: date
    tipo: str
    documentos: tuple[DocumentoPreviaVenda, ...]
    total_informado: int
    documentos_recebidos: int
    amostra_parcial: bool = field(default=True, init=False)
    versao_contrato: str = field(default=VERSAO_CONTRATO, init=False)
    coverage_confirmed: bool = field(default=False, init=False)
    imported: bool = field(default=False, init=False)


def _hoje() -> date:
    """Usa o dia civil de Fortaleza para bloquear períodos futuros de consulta."""
    return datetime.now(ZoneInfo("America/Fortaleza")).date()


def _identificador(value: Any) -> str:
    """Aceita o identificador textual da venda sem caracteres de controle ou URL."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ContratoInvalido("A amostra contém um identificador obrigatório inválido.")
    return value


def _id_produto(value: Any, *, variacao: bool = False) -> str | None:
    """Normaliza IDs numéricos sem confundir variação zero com ausência.

    O contrato atual declara inteiros; a forma decimal textual preserva a
    compatibilidade das respostas legadas. Booleanos e frações são recusados.
    """
    if variacao and (value is None or value == ""):
        return None
    if type(value) is int:
        texto = str(value)
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,30}", value):
        texto = value
    else:
        raise ContratoInvalido("A amostra contém um identificador de produto ou variação inválido.")
    if len(texto) > 30 or not texto.isdecimal() or int(texto) < (0 if variacao else 1):
        raise ContratoInvalido("A amostra contém um identificador de produto ou variação inválido.")
    return texto


def _quantidade(value: Any) -> Decimal:
    """Confere decimal textual finito sem arredondar nem aceitar vírgulas/expoentes."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,6})?", value):
        raise ContratoInvalido("A amostra contém uma quantidade inválida ou fora do limite.")
    numero = Decimal(value)
    if numero > Decimal("1000000000"):
        raise ContratoInvalido("A amostra contém uma quantidade inválida ou fora do limite.")
    return numero


def _documento(row: Any, inicio: date, fim: date, situacoes: set[str]) -> DocumentoPreviaVenda:
    """Projeta uma venda conferindo período, situação e todos os seus itens."""
    if not isinstance(row, dict):
        raise ContratoInvalido("A amostra contém um documento de venda inválido.")
    identificador = _identificador(row.get("id"))
    codigo = _identificador(row.get("codigo"))
    situacao = _identificador(row.get("situacao_id"))
    if situacao not in situacoes:
        raise ContratoInvalido("A venda usa uma situação não conferida. Consulte as situações novamente.")
    data = row.get("data")
    if not isinstance(data, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", data):
        raise ContratoInvalido("A amostra contém uma data de venda inválida.")
    try:
        dia = date.fromisoformat(data)
    except ValueError:
        raise ContratoInvalido("A amostra contém uma data de venda inválida.") from None
    if not inicio <= dia <= fim:
        raise ContratoInvalido("A amostra retornou uma venda fora do período solicitado.")
    produtos = row.get("produtos")
    if not isinstance(produtos, list) or not produtos or len(produtos) > MAX_ITENS_RECEBIDOS:
        raise ContratoInvalido("Os itens da amostra estão ausentes ou excedem o limite da prévia.")
    itens = []
    for wrapper in produtos:
        produto = wrapper.get("produto") if isinstance(wrapper, dict) else None
        if not isinstance(produto, dict):
            raise ContratoInvalido("A amostra contém um item de produto inválido.")
        itens.append(ItemPreviaVenda(
            produto_id=_id_produto(produto.get("produto_id")),
            variacao_id=_id_produto(produto.get("variacao_id"), variacao=True),
            quantidade=_quantidade(produto.get("quantidade")),
        ))
    return DocumentoPreviaVenda(identificador, codigo, dia, situacao, tuple(itens))


def _projetar(payload: dict, inicio: date, fim: date, conta: ContaGestaoClick) -> tuple[
    tuple[DocumentoPreviaVenda, ...], int, int
]:
    """Valida a página inteira antes de entregar até cinco documentos, sem PII."""
    rows = payload.get("data")
    if (type(payload.get("code")) is not int or payload["code"] != 200 or
            payload.get("status") != "success" or not isinstance(rows, list) or
            len(rows) > MAX_DOCUMENTOS_RECEBIDOS):
        raise ContratoInvalido("A resposta não corresponde à amostra de vendas documentada.")
    try:
        # Situações e vendas têm os mesmos contadores documentados. Reutilizar
        # a validação numérica não reutiliza rotas nem segue URLs da resposta.
        total, _, _ = _conferir_pagina_situacoes(payload.get("meta"), len(rows), 1)
    except ContratoInvalido:
        raise ContratoInvalido("A paginação da amostra de vendas não pôde ser comprovada.") from None
    documentos = []
    vistos = set()
    total_itens = 0
    situacoes = {row["id"] for row in conta.situacoes}
    for row in rows:
        documento = _documento(row, inicio, fim, situacoes)
        if documento.id in vistos:
            raise ContratoInvalido("A amostra contém um documento de venda repetido.")
        vistos.add(documento.id)
        total_itens += len(documento.itens)
        if total_itens > MAX_ITENS_RECEBIDOS:
            raise ContratoInvalido("A amostra excedeu cem itens. Escolha um período menor.")
        if len(documentos) < MAX_DOCUMENTOS_EXIBIDOS:
            documentos.append(documento)
    return tuple(documentos), total, len(rows)


def consultar_previa_vendas(
    auth: RPCAuth,
    carregar_configuracao: Callable[[], Mapping[str, Any]],
    conta: ContaGestaoClick,
    store_id: str,
    inicio: date,
    fim: date,
    tipo: str = "produto",
    *,
    autorizado: bool,
    transporte: TransporteConsulta | None = None,
) -> PreviaVendasNativas:
    """Consulta uma página sob consentimento Master, sem importar nem gravar.

    Autorizações, catálogo assinado e configuração são conferidos antes e após
    a operação, inclusive em respostas inválidas ou falhas. O único GET usa
    parâmetros fechados e nunca segue a continuação fornecida pelo servidor.
    """
    with auth.operation():
        stamp = _stamp(auth)
        if autorizado is not True:
            raise AuthorizationError("Confirme a consulta da amostra real de vendas.")
        _validar_conta(auth, conta)
        if not isinstance(store_id, str) or store_id not in {row["id"] for row in conta.lojas}:
            raise AuthorizationError("Selecione uma loja da conferência autorizada da conta.")
        if not isinstance(tipo, str) or tipo not in _TIPOS:
            raise ContratoInvalido("Escolha vendas de produtos ou vendas de balcão para a amostra.")
        if (type(inicio) is not date or type(fim) is not date or
                inicio > fim or (fim - inicio).days >= MAX_DIAS_PREVIA or fim > _hoje()):
            raise ContratoInvalido("Escolha até 31 dias inclusivos, sem datas futuras, para a amostra.")
        config = _configuracao(auth, carregar_configuracao, stamp, True)
        if not hmac.compare_digest(conta._config_stamp, _vinculo_configuracao(config)):
            raise AuthorizationError("As credenciais ou o servidor mudaram. Confira a conta novamente.")

        def revalidar() -> None:
            """Recusa entrega após revogação, expiração, troca de conta ou de Secrets."""
            _validar_conta(auth, conta)
            atual = _configuracao(auth, carregar_configuracao, stamp, True)
            if atual != config:
                raise AuthorizationError("A configuração protegida mudou. Confira a conta novamente.")

        revalidar()
        query = urlencode({"data_inicio": inicio.isoformat(), "data_fim": fim.isoformat(),
                           "loja_id": store_id, "tipo": tipo, "pagina": 1,
                           "limite": MAX_DOCUMENTOS_EXIBIDOS})
        url = config.validar_conexao() + "/vendas?" + query
        try:
            try:
                response = (transporte or TransporteHTTPS()).get(url, {
                    "access-token": config.access_token,
                    "secret-access-token": config.secret_access_token,
                    "Accept": "application/json",
                }, min(config.timeout_seconds, 15))
            except Exception:
                raise GestaoClickError("A consulta da amostra não respondeu. Tente novamente mais tarde.") from None
            revalidar()
            if type(response.status) is not int:
                raise GestaoClickError("O GestãoClick retornou um estado de consulta inválido.")
            if response.status in {401, 403}:
                raise GestaoClickError("O GestãoClick recusou as credenciais ou as permissões de consulta.")
            if response.status == 429:
                raise GestaoClickError("Limite do GestãoClick atingido. Aguarde antes de consultar novamente.")
            if response.status != 200:
                raise GestaoClickError("Consulta recusada pelo GestãoClick; nenhum redirecionamento foi seguido.")
            documentos, total, recebidos = _projetar(parse_json_seguro(response.body), inicio, fim, conta)
            return PreviaVendasNativas(store_id, inicio, fim, tipo, documentos, total, recebidos)
        finally:
            revalidar()
