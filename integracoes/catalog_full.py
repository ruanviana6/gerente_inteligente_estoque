"""Leitura integral e reconciliada do catálogo GestãoClick, sem gravações no ERP.

A amostra por loja é insuficiente para declarar cobertura. Este leitor valida
todas as páginas, metadados, cursor, duplicidades, escopo e identidade Master.
Não importa saldos, não deduz produtos ausentes e não altera o motor de estoque.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from integracoes.account_setup import ContaGestaoClick, _configuracao, _validar_conta, _vinculo_configuracao
from integracoes.catalog_preview import ProdutoCatalogo, _projetar
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

MAX_PAGINAS_CATALOGO = 10
LIMITE_PAGINA = 100
MAX_ITENS_CATALOGO = MAX_PAGINAS_CATALOGO * LIMITE_PAGINA


class PaginacaoCatalogoInvalida(ContratoInvalido):
    """Recusa cobertura incerta sem mostrar links, contagens da conta ou tokens."""

    def __init__(self, motivo: str):
        permitidos = {
            "META", "CONTAGEM", "PAGINA", "LIMITE", "CURSOR",
            "URL", "ALTERACAO", "DUPLICIDADE", "EXCESSO",
            "CURSOR_TIPO", "CURSOR_PAGINA", "CURSOR_URL_AUSENTE",
            "CURSOR_URL_INDEVIDA",
            "URL_ORIGEM", "URL_CAMINHO", "URL_PARAMETROS", "URL_LOJA",
            "URL_PAGINA", "URL_LIMITE",
        }
        codigo = motivo if motivo in permitidos else "META"
        super().__init__(f"O catálogo completo não pôde ser reconciliado ({codigo}). "
                         "Nenhum saldo foi importado.")
        self.diagnostico = {"motivo": codigo}


@dataclass(frozen=True, repr=False)
class CatalogoCompleto:
    loja_solicitada: str
    produtos: tuple[ProdutoCatalogo, ...]
    total_informado: int
    paginas: int
    soma_estoque: Decimal | None
    sem_estoque: int
    sem_codigo: int
    codigos_repetidos: int
    variacoes_sem_id: int
    variacoes_sem_codigo: int
    checksum: str = field(repr=False)
    continuacoes_sem_loja: int = 0

    def linhas_snapshot(self) -> list[dict]:
        """Representação mínima para cópia analítica, sem preços, cliente ou vendas."""
        if self.sem_estoque:
            raise ContratoInvalido("A consulta possui saldos ausentes; não é possível armazenar o lote.")
        return [
            {
                "id": produto.id,
                "codigo_interno": produto.codigo_interno,
                "estoque": format(produto.estoque, "f"),
                "variacoes_sem_id": sum(v.id is None for v in produto.variacoes),
                "variacoes_sem_codigo": sum(v.codigo is None for v in produto.variacoes),
            }
            for produto in sorted(self.produtos, key=lambda p: (int(p.id), p.id))
        ]


def _conferir_meta(meta: Any, *, pagina: int, recebidos: int) -> tuple[int, int, int]:
    """Aceita os aliases documentados, mas nunca calcula fim por página curta."""
    if not isinstance(meta, dict) or "proxima_url" not in meta:
        raise PaginacaoCatalogoInvalida("META")
    campos = ("total_registros", "pagina_atual", "limite_por_pagina")
    aliases = tuple(k for k in ("total_da_pagina", "total_registros_pagina") if k in meta)
    if not aliases or any(type(meta.get(k)) is not int or meta[k] < 0
                          for k in (*campos, *aliases)):
        raise PaginacaoCatalogoInvalida("CONTAGEM")
    total, limite = meta["total_registros"], meta["limite_por_pagina"]
    paginas = (total + limite - 1) // limite if limite else 0
    if not 1 <= limite <= LIMITE_PAGINA:
        raise PaginacaoCatalogoInvalida("LIMITE")
    if total > MAX_ITENS_CATALOGO or paginas > MAX_PAGINAS_CATALOGO:
        raise PaginacaoCatalogoInvalida("EXCESSO")
    if meta["pagina_atual"] != pagina or pagina > max(paginas, 1):
        raise PaginacaoCatalogoInvalida("PAGINA")
    if (any(meta[key] != recebidos for key in aliases) or
            recebidos != min(limite, max(0, total - (pagina - 1) * limite))):
        raise PaginacaoCatalogoInvalida("CONTAGEM")
    if "total_paginas" in meta:
        esperados = {paginas} if total else {0, 1}
        if type(meta["total_paginas"]) is not int or meta["total_paginas"] not in esperados:
            raise PaginacaoCatalogoInvalida("META")
    # O ERP pode serializar o número de continuação como inteiro ou texto.
    # Aceitar ambos não implica confiar na página: o valor precisa ser exato.
    if "proxima_pagina" in meta:
        cursor = meta["proxima_pagina"]
        if pagina < paginas:
            esperado = pagina + 1
            if type(cursor) is int:
                coincide = cursor == esperado
            elif isinstance(cursor, str):
                coincide = cursor == str(esperado)
            else:
                raise PaginacaoCatalogoInvalida("CURSOR_TIPO")
            if not coincide:
                raise PaginacaoCatalogoInvalida("CURSOR_PAGINA")
        elif cursor is not None:
            raise PaginacaoCatalogoInvalida("CURSOR_PAGINA")
    if pagina < paginas:
        if not isinstance(meta["proxima_url"], str) or not meta["proxima_url"]:
            raise PaginacaoCatalogoInvalida("CURSOR_URL_AUSENTE")
    elif meta["proxima_url"] is not None:
        raise PaginacaoCatalogoInvalida("CURSOR_URL_INDEVIDA")
    return total, limite, paginas


def _conferir_proxima_url(
    url: Any, *, base: str, loja: str, pagina: int, limite_solicitado: int, limite_efetivo: int,
) -> tuple[int, bool]:
    """Valida o endereço sem segui-lo; o próximo GET é sempre montado localmente.

    Aceita URL absoluta da mesma origem, caminho relativo exato, ou apenas a
    query da continuação. A ausência do filtro de loja gera alerta, mas a
    requisição seguinte mantém sempre loja_id explícito e autorizado.
    """
    if not isinstance(url, str) or not 0 < len(url) <= 2048 or any(c.isspace() for c in url):
        raise PaginacaoCatalogoInvalida("URL")
    try:
        target = urlsplit(url)
        expected = urlsplit(base + "/produtos")
        if target.fragment or target.username is not None or target.password is not None:
            raise PaginacaoCatalogoInvalida("URL_ORIGEM")
        if target.scheme or target.netloc:
            if (target.scheme != expected.scheme or target.netloc != expected.netloc):
                raise PaginacaoCatalogoInvalida("URL_ORIGEM")
            if target.path != expected.path:
                raise PaginacaoCatalogoInvalida("URL_CAMINHO")
        elif target.path not in ("", expected.path, "produtos"):
            raise PaginacaoCatalogoInvalida("URL_CAMINHO")
        pairs = parse_qsl(
            target.query, keep_blank_values=True, strict_parsing=True, max_num_fields=4,
        )
        params = dict(pairs)
        if (len(params) != len(pairs) or len(params) not in (2, 3)
                or not {"pagina", "limite"}.issubset(params)
                or set(params) - {"pagina", "limite", "loja_id", "loja"}
                or ("loja_id" in params and "loja" in params)):
            raise PaginacaoCatalogoInvalida("URL_PARAMETROS")
        loja_vinda = params.get("loja_id", params.get("loja"))
        if loja_vinda is not None and loja_vinda != loja:
            raise PaginacaoCatalogoInvalida("URL_LOJA")
        if params["pagina"] != str(pagina):
            raise PaginacaoCatalogoInvalida("URL_PAGINA")
        limites = {str(limite_solicitado), str(limite_efetivo)}
        if params["limite"] not in limites:
            raise PaginacaoCatalogoInvalida("URL_LIMITE")
        return int(params["limite"]), loja_vinda is None
    except ValueError:
        raise PaginacaoCatalogoInvalida("URL_PARAMETROS") from None


def consultar_catalogo_completo(
    auth: RPCAuth,
    carregar_configuracao,
    conta: ContaGestaoClick,
    store_id: str,
    *,
    autorizado: bool,
    transporte: TransporteConsulta | None = None,
) -> CatalogoCompleto:
    """Realiza até dez GETs de leitura. Falha inteira em qualquer página inconsistente."""
    with auth.operation():
        stamp = _stamp(auth)
        if autorizado is not True:
            raise AuthorizationError("Autorize explicitamente a leitura completa do catálogo.")
        _validar_conta(auth, conta)
        if not isinstance(store_id, str) or store_id not in {x["id"] for x in conta.lojas}:
            raise AuthorizationError("Selecione uma loja autorizada para consultar o catálogo.")
        config = _configuracao(auth, carregar_configuracao, stamp, True)
        if not hmac.compare_digest(conta._config_stamp, _vinculo_configuracao(config)):
            raise AuthorizationError("A configuração da conta mudou. Consulte as lojas novamente.")
        base = config.validar_conexao()
        transport = transporte or TransporteHTTPS()

        def revalidar():
            _validar_conta(auth, conta)
            if _configuracao(auth, carregar_configuracao, stamp, True) != config:
                raise AuthorizationError("A configuração protegida mudou durante a consulta.")

        total_esperado = limite_efetivo = None
        produtos: list[ProdutoCatalogo] = []
        vistos: set[str] = set()
        limite_solicitado = LIMITE_PAGINA
        paginas_consultadas = 0
        continuacoes_sem_loja = 0
        for pagina in range(1, MAX_PAGINAS_CATALOGO + 1):
            if pagina > 1:
                time.sleep(config.interval_seconds)
            revalidar()
            url = base + "/produtos?" + urlencode({
                "loja_id": store_id, "pagina": pagina, "limite": limite_solicitado,
            })
            try:
                response = transport.get(url, {
                    "access-token": config.access_token,
                    "secret-access-token": config.secret_access_token,
                    "Accept": "application/json",
                }, min(config.timeout_seconds, 15))
            except Exception:
                revalidar()
                raise GestaoClickError("O catálogo não respondeu; nenhum lote foi preparado.") from None
            revalidar()
            if type(response.status) is not int or response.status != 200:
                if response.status in (401, 403):
                    raise GestaoClickError("A chave não possui permissão para ler o catálogo.")
                if response.status == 429:
                    raise GestaoClickError("O GestãoClick limitou as consultas. Retome depois.")
                raise GestaoClickError("A API recusou uma página do catálogo; nenhum lote foi preparado.")
            payload = parse_json_seguro(response.body)
            lote, recebidos, _ = _projetar(payload, pagina=pagina, max_exibidos=LIMITE_PAGINA)
            total, limite, paginas = _conferir_meta(payload.get("meta"), pagina=pagina,
                                                    recebidos=recebidos)
            if total_esperado is None:
                total_esperado, limite_efetivo = total, limite
            elif total != total_esperado or limite != limite_efetivo:
                raise PaginacaoCatalogoInvalida("ALTERACAO")
            ids = {x.id for x in lote}
            if len(ids) != len(lote) or vistos.intersection(ids):
                raise PaginacaoCatalogoInvalida("DUPLICIDADE")
            vistos.update(ids)
            produtos.extend(lote)
            paginas_consultadas = pagina
            if len(produtos) == total_esperado:
                break
            if pagina >= MAX_PAGINAS_CATALOGO:
                raise PaginacaoCatalogoInvalida("EXCESSO")
            limite_solicitado, sem_loja = _conferir_proxima_url(
                payload["meta"]["proxima_url"], base=base, loja=store_id, pagina=pagina + 1,
                limite_solicitado=limite_solicitado, limite_efetivo=limite,
            )
            if sem_loja:
                continuacoes_sem_loja += 1
        revalidar()
        if total_esperado is None or len(produtos) != total_esperado:
            raise PaginacaoCatalogoInvalida("CONTAGEM")
        sem_estoque = sum(p.estoque is None for p in produtos)
        soma = sum((p.estoque for p in produtos if p.estoque is not None), Decimal(0))
        codigos = [p.codigo_interno for p in produtos if p.codigo_interno is not None]
        repetidos = len(codigos) - len(set(codigos))
        compact = [{
            "id": p.id, "codigo": p.codigo_interno,
            "estoque": format(p.estoque, "f") if p.estoque is not None else None,
        } for p in sorted(produtos, key=lambda p: (int(p.id), p.id))]
        checksum = hashlib.sha256(
            json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return CatalogoCompleto(
            store_id, tuple(produtos), total_esperado, paginas_consultadas,
            soma if not sem_estoque else None, sem_estoque,
            sum(p.codigo_interno is None for p in produtos), repetidos,
            sum(v.id is None for p in produtos for v in p.variacoes),
            sum(v.codigo is None for p in produtos for v in p.variacoes), checksum,
            continuacoes_sem_loja,
        )
