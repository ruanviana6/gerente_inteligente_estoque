"""Auditoria integral (somente leitura) de vendas nativas do GestãoClick.

Não inventa ID de linha, SKU, devolução nem timestamp de alteração. Conferir
todas as páginas NÃO é evidência de cobertura histórica ou de pedidos excluídos.
"""
from __future__ import annotations

import hmac
import time
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlencode

from integracoes.account_setup import (
    ContaGestaoClick,
    PaginacaoSituacoesInvalida,
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
from integracoes.native_preview import (
    DocumentoPreviaVenda,
    _documento,
    _hoje,
    _normalizar_cursor_numerico,
)
from sincronizacao.service import RPCAuth
from web.errors import AuthorizationError

MAX_DIAS_CONSULTA = 7
MAX_DOCUMENTOS = 1000
MAX_PAGINAS = 10
MAX_ITENS = 20000
LIMITE = 100


class VendaIntegralInvalida(ContratoInvalido):
    """Diagnóstico fechado, sem expor dados comerciais nem o corpo da API."""

    def __init__(self, motivo: str):
        permitidos = {
            "META", "CONTAGEM", "PAGINA", "CURSOR", "LIMITE",
            "TOTAL_ALTERADO", "DUPLICIDADE", "EXCESSO", "DOCUMENTO",
        }
        codigo = motivo if motivo in permitidos else "META"
        super().__init__(f"A consulta integral de vendas não pôde ser reconciliada ({codigo}). "
                         "Nenhuma venda foi importada.")
        self.diagnostico = {"motivo": codigo}


@dataclass(frozen=True, repr=False)
class AuditoriaVendas:
    loja_solicitada: str
    inicio: date
    fim: date
    documentos: tuple[DocumentoPreviaVenda, ...] = field(repr=False)
    total_informado: int = 0
    paginas: int = 0
    itens: int = 0
    imported: bool = field(default=False, init=False)
    source_coverage_verified: bool = field(default=False, init=False)
    stable_line_id_verified: bool = field(default=False, init=False)
    source_updated_at_verified: bool = field(default=False, init=False)


def consultar_vendas_integrais(
    auth: RPCAuth,
    carregar_configuracao,
    conta: ContaGestaoClick,
    store_id: str,
    inicio: date,
    fim: date,
    *,
    autorizado: bool,
    transporte: TransporteConsulta | None = None,
) -> AuditoriaVendas:
    """Consulta até 10 páginas; retorna tudo ou falha sem persistir resultados.

    O próximo GET é sempre gerado localmente e usa a mesma loja, o mesmo tipo
    e o mesmo período. A URL de continuação retornada nunca é seguida.
    """
    with auth.operation():
        stamp = _stamp(auth)
        if autorizado is not True:
            raise AuthorizationError("Confirme a autorização de leitura integral de vendas.")
        _validar_conta(auth, conta)
        if not isinstance(store_id, str) or store_id not in {x["id"] for x in conta.lojas}:
            raise AuthorizationError("Selecione uma loja do catálogo autorizado.")
        if (type(inicio) is not date or type(fim) is not date or
                inicio > fim or (fim - inicio).days >= MAX_DIAS_CONSULTA or fim > _hoje()):
            raise ContratoInvalido("Escolha até sete dias inclusivos, sem datas futuras.")
        config = _configuracao(auth, carregar_configuracao, stamp, True)
        if not hmac.compare_digest(conta._config_stamp, _vinculo_configuracao(config)):
            raise AuthorizationError("A configuração protegida mudou. Consulte as lojas novamente.")
        base = config.validar_conexao()
        transport = transporte or TransporteHTTPS()

        def revalidar() -> None:
            _validar_conta(auth, conta)
            if _configuracao(auth, carregar_configuracao, stamp, True) != config:
                raise AuthorizationError("O acesso mudou durante a consulta de vendas.")

        documentos: list[DocumentoPreviaVenda] = []
        vistos: set[str] = set()
        total_esperado = limite_efetivo = None
        paginas_consultadas = itens = 0
        limite_solicitado = LIMITE
        situacoes = {x["id"] for x in conta.situacoes}
        for pagina in range(1, MAX_PAGINAS + 1):
            if pagina > 1:
                time.sleep(config.interval_seconds)
            revalidar()
            url = base + "/vendas?" + urlencode({
                "data_inicio": inicio.isoformat(), "data_fim": fim.isoformat(),
                "loja_id": store_id, "tipo": "produto",
                "pagina": pagina, "limite": limite_solicitado,
            })
            try:
                resposta = transport.get(url, {
                    "access-token": config.access_token,
                    "secret-access-token": config.secret_access_token,
                    "Accept": "application/json",
                }, min(config.timeout_seconds, 15))
            except Exception:
                revalidar()
                raise GestaoClickError("A API não respondeu; nenhuma venda foi importada.") from None
            revalidar()
            if type(resposta.status) is not int or resposta.status != 200:
                if resposta.status in (401, 403):
                    raise GestaoClickError("A API recusou permissão de leitura de vendas.")
                if resposta.status == 429:
                    raise GestaoClickError("Limite de consultas atingido. Tente depois.")
                raise GestaoClickError("A API recusou uma página. Nenhuma venda foi importada.")
            payload = parse_json_seguro(resposta.body)
            rows = payload.get("data")
            if (type(payload.get("code")) is not int or payload["code"] != 200 or
                    payload.get("status") != "success" or
                    not isinstance(rows, list) or len(rows) > LIMITE):
                raise VendaIntegralInvalida("META")
            meta = _normalizar_cursor_numerico(payload.get("meta"))
            try:
                total, efetivo, paginas = _conferir_pagina_situacoes(meta, len(rows), pagina)
            except PaginacaoSituacoesInvalida:
                raise VendaIntegralInvalida("CONTAGEM") from None
            if total > MAX_DOCUMENTOS or paginas > MAX_PAGINAS:
                raise VendaIntegralInvalida("EXCESSO")
            if total_esperado is None:
                total_esperado, limite_efetivo = total, efetivo
            elif total != total_esperado or efetivo != limite_efetivo:
                raise VendaIntegralInvalida("TOTAL_ALTERADO")
            # Conferir TODOS os documentos antes de devolver qualquer parcial.
            for row in rows:
                doc = _documento(row, inicio, fim, situacoes)
                if doc.id in vistos:
                    raise VendaIntegralInvalida("DUPLICIDADE")
                vistos.add(doc.id)
                documentos.append(doc)
                itens += len(doc.itens)
                if itens > MAX_ITENS:
                    raise VendaIntegralInvalida("EXCESSO")
            paginas_consultadas = pagina
            if len(documentos) == total_esperado:
                break
            if pagina >= MAX_PAGINAS:
                raise VendaIntegralInvalida("EXCESSO")
            limite_solicitado = efetivo
        revalidar()
        if total_esperado is None or len(documentos) != total_esperado:
            raise VendaIntegralInvalida("CONTAGEM")
        return AuditoriaVendas(
            store_id, inicio, fim, tuple(documentos), total_esperado,
            paginas_consultadas, itens,
        )
