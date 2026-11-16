"""Consulta catálogos mínimos da conta e vincula uma loja à fonte interna.

Os objetos deste módulo pertencem ao estado da sessão no servidor. Nenhum
catálogo recebido do navegador, nome de empresa ou credencial autoriza uma loja.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from integracoes.diagnostico import _stamp
from integracoes.gestaoclick import (
    ContratoInvalido,
    GestaoClickConfig,
    GestaoClickError,
    TransporteConsulta,
    TransporteHTTPS,
    parse_json_seguro,
)
from sincronizacao.service import RPCAuth
from web.errors import AppError, AuthorizationError

CONTA_TTL_SECONDS = 300
_LIMITE = 100
_MAX_PAGINAS = 3
_CHAVE_ASSINATURA = secrets.token_bytes(32)
_CAMPOS_PAGINACAO = ("total_registros", "total_da_pagina", "total_registros_pagina",
                     "total_paginas", "pagina_atual", "limite_por_pagina", "proxima_pagina", "proxima_url")


class PaginacaoSituacoesInvalida(ContratoInvalido):
    """Expõe apenas o motivo fixo e tipos de metadados, nunca seu conteúdo.

    O diagnóstico permite identificar diferenças de contrato sem pedir resposta
    bruta da API, que poderia conter dados comerciais ou credenciais.
    """

    def __init__(self, motivo: str, meta: Any):
        """Limita códigos e nomes de campo ao vocabulário interno conhecido."""
        motivos = {"SC_META", "SC_CONTAGEM", "SC_LIMITE", "SC_PAGINA", "SC_TOTAL_PAGINAS",
                   "SC_CURSOR", "SC_CONTINUACAO", "SC_MUDANCA"}
        codigo = motivo if motivo in motivos else "SC_META"
        super().__init__(f"A paginação das situações não pôde ser comprovada ({codigo}). "
                         "Nenhum catálogo parcial foi utilizado.")
        tipos = {int: "inteiro", str: "texto", bool: "booleano", float: "decimal",
                 dict: "objeto", list: "lista", type(None): "nulo"}
        self.diagnostico = {"codigo": codigo, "tipo_meta": tipos.get(type(meta), "outro"),
                            "campos": {campo: (tipos.get(type(meta[campo]), "outro")
                                               if isinstance(meta, dict) and campo in meta else "ausente")
                                       for campo in _CAMPOS_PAGINACAO}}


def _conferir_pagina_situacoes(meta: Any, recebidos: int, pagina: int) -> tuple[int, int, int]:
    """Reconcilia os dois formatos documentados sem inferir fim por página curta.

    A API pode aplicar um limite menor que o solicitado. O limite efetivo define
    o tamanho de cada página e precisa permanecer estável durante a consulta.
    As contagens continuam inteiras; aliases conflitantes são recusados.
    """
    if not isinstance(meta, dict) or "proxima_url" not in meta:
        raise PaginacaoSituacoesInvalida("SC_META", meta)
    campos = ["total_registros", "pagina_atual", "limite_por_pagina"]
    contadores = [nome for nome in ("total_da_pagina", "total_registros_pagina") if nome in meta]
    if not contadores or any(type(meta.get(nome)) is not int or meta[nome] < 0
                            for nome in campos + contadores):
        raise PaginacaoSituacoesInvalida("SC_CONTAGEM", meta)
    total, limite = meta["total_registros"], meta["limite_por_pagina"]
    if not 1 <= limite <= _LIMITE:
        raise PaginacaoSituacoesInvalida("SC_LIMITE", meta)
    paginas = (total + limite - 1) // limite
    if meta["pagina_atual"] != pagina or pagina > max(1, paginas):
        raise PaginacaoSituacoesInvalida("SC_PAGINA", meta)
    if "total_paginas" in meta:
        declaradas = meta["total_paginas"]
        esperadas = {paginas} if total else {0, 1}
        if type(declaradas) is not int or declaradas not in esperadas:
            raise PaginacaoSituacoesInvalida("SC_TOTAL_PAGINAS", meta)
    if (any(meta[nome] != recebidos for nome in contadores) or
            recebidos != min(limite, max(0, total - (pagina - 1) * limite))):
        raise PaginacaoSituacoesInvalida("SC_CONTAGEM", meta)
    cursor = str(pagina + 1) if pagina < paginas else None
    if "proxima_pagina" in meta and meta["proxima_pagina"] != cursor:
        raise PaginacaoSituacoesInvalida("SC_CURSOR", meta)
    if (pagina >= paginas and meta["proxima_url"] is not None or
            pagina < paginas and not isinstance(meta["proxima_url"], str)):
        raise PaginacaoSituacoesInvalida("SC_CONTINUACAO", meta)
    return total, limite, paginas


@dataclass(frozen=True, repr=False)
class ContaGestaoClick:
    """Guarda somente catálogos permitidos e vínculo assinado de curta duração.

    Guarde o objeto inteiro no session_state do servidor e execute validar_conta
    antes de exibir os catálogos em cada rerun. As propriedades devolvem cópias;
    alterar a lista exibida não altera a autorização de registro de uma fonte.
    """

    organization_id: str
    _access_stamp: tuple
    _criada_em: float
    _expira_em: float
    _lojas: tuple[tuple[str, str], ...]
    _situacoes: tuple[tuple[str, str], ...]
    _config_stamp: bytes
    _assinatura: bytes = b""

    @property
    def lojas(self) -> list[dict[str, str]]:
        """Entrega somente identificadores e nomes, sem referências mutáveis internas."""
        return [{"id": identificador, "nome": nome} for identificador, nome in self._lojas]

    @property
    def situacoes(self) -> list[dict[str, str]]:
        """Entrega a lista completa consultada, sem atributos financeiros ou pessoais."""
        return [{"id": identificador, "nome": nome} for identificador, nome in self._situacoes]


def _vinculo_configuracao(config: GestaoClickConfig) -> bytes:
    """Vincula a consulta à conta sem guardar tokens no catálogo da sessão.

    A HMAC usa a chave efêmera do processo. Fonte, loja e mapas não entram no
    vínculo, pois podem ser configurados depois da conferência da mesma conta.
    """
    body = json.dumps((config.validar_conexao(), config.access_token, config.secret_access_token),
                      ensure_ascii=True, separators=(",", ":")).encode()
    return hmac.digest(_CHAVE_ASSINATURA, b"gestaoclick-account-binding\0" + body, "sha256")


def _assinatura(conta: ContaGestaoClick) -> bytes:
    """Assina metadados mínimos com chave efêmera que nunca sai do processo servidor."""
    body = json.dumps((conta.organization_id, conta._access_stamp, conta._criada_em,
                       conta._expira_em, conta._lojas, conta._situacoes, conta._config_stamp.hex()),
                      ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
    return hmac.digest(_CHAVE_ASSINATURA, body, "sha256")


def _validar_conta(auth: RPCAuth, conta: ContaGestaoClick) -> None:
    """Confere integridade, prazo e identidade, sem carregar Secrets ou consultar o ERP."""
    atual = _stamp(auth)
    try:
        valida = (type(conta) is ContaGestaoClick and
                  type(conta._lojas) is tuple and type(conta._situacoes) is tuple and
                  all(type(row) is tuple and len(row) == 2 and
                      all(type(value) is str for value in row)
                      for row in conta._lojas + conta._situacoes) and
                  type(conta._access_stamp) is tuple and len(conta._access_stamp) == 5 and
                  type(conta._access_stamp[3]) is tuple and
                  type(conta._config_stamp) is bytes and len(conta._config_stamp) == 32 and
                  type(conta._assinatura) is bytes and
                  hmac.compare_digest(conta._assinatura, _assinatura(conta)) and
                  all(type(value) in (int, float) and math.isfinite(value)
                      for value in (conta._criada_em, conta._expira_em)) and
                  # Repete a soma da emissão: subtrair floats pode arredondar o prazo de novo.
                  conta._expira_em == conta._criada_em + CONTA_TTL_SECONDS and
                  conta._criada_em <= time.monotonic() and
                  conta._access_stamp == atual and conta.organization_id == atual[1])
    except (AttributeError, TypeError, ValueError, OverflowError):
        valida = False
    if not valida:
        raise AuthorizationError("A consulta da conta expirou ou o acesso mudou. Consulte a conta novamente.")
    if time.monotonic() >= conta._expira_em:
        raise ContratoInvalido("A consulta da conta expirou. Consulte a conta novamente para continuar.")


def validar_conta(auth: RPCAuth, conta: ContaGestaoClick) -> ContaGestaoClick:
    """Revalida o catálogo guardado no servidor antes de exibi-lo ou reutilizá-lo."""
    with auth.operation():
        _validar_conta(auth, conta)
        return conta


def _configuracao(
    auth: RPCAuth,
    carregar_configuracao: Callable[[], Mapping[str, Any]],
    stamp: tuple,
    autorizado: bool,
) -> GestaoClickConfig:
    """Revalida consentimento, Master e organização protegida em cada fronteira HTTP."""
    if autorizado is not True:
        raise AuthorizationError("Confirme a consulta real das lojas e situações da conta.")
    if _stamp(auth) != stamp:
        raise AuthorizationError("O acesso mudou durante a consulta da conta.")
    try:
        values = carregar_configuracao()
    except Exception:
        raise ContratoInvalido("Não foi possível carregar a configuração protegida do servidor.") from None
    if not isinstance(values, Mapping):
        raise ContratoInvalido("Configuração protegida do servidor inválida.")
    try:
        organization = str(UUID(values.get("GESTAOCLICK_ORGANIZATION_ID", "")))
    except (ValueError, TypeError, AttributeError):
        raise ContratoInvalido("Configure a organização autorizada nos Secrets antes de consultar a conta.") from None
    if organization != stamp[1] or _stamp(auth) != stamp:
        raise AuthorizationError("Esta conexão não está autorizada para sua organização.")
    config = GestaoClickConfig.from_mapping(values)
    config.validar_conexao()
    return config


def _catalogo(rows: Any) -> tuple[tuple[str, str], ...]:
    """Aceita somente linhas íntegras e IDs únicos, descartando todos os outros campos."""
    if not isinstance(rows, list) or len(rows) > _LIMITE:
        raise ContratoInvalido("O catálogo da conta está inválido ou excedeu o limite da consulta.")
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ContratoInvalido("O catálogo da conta contém um registro inválido.")
        identificador, nome = row.get("id"), row.get("nome")
        if (not isinstance(identificador, str) or
                not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identificador) or
                identificador in seen or not isinstance(nome, str) or not nome.strip() or
                len(nome) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in nome)):
            raise ContratoInvalido("O catálogo da conta contém identificadores ou nomes inválidos.")
        seen.add(identificador)
        result.append((identificador, nome))
    return tuple(result)


def _validar_proxima_url(value: Any, base: str, pagina: int, limites: tuple[int, ...]) -> int:
    """Extrai só o limite comprovado; o próximo endereço continua construído localmente."""
    if not isinstance(value, str) or not value or len(value) > 1024 or any(c.isspace() for c in value):
        raise ContratoInvalido("A continuação do catálogo de situações é inválida.")
    try:
        parsed = urllib.parse.urlsplit(value)
        expected = urllib.parse.urlsplit(base + "/situacoes_vendas")
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        params = dict(pairs)
        if (parsed.scheme != expected.scheme or parsed.netloc != expected.netloc or
                parsed.path != expected.path or parsed.fragment or
                len(pairs) != 2 or set(params) != {"pagina", "limite"} or
                params["pagina"] != str(pagina) or params["limite"] not in {str(n) for n in limites}):
            raise ValueError
        return int(params["limite"])
    except ValueError:
        raise ContratoInvalido("A continuação do catálogo de situações está fora da consulta autorizada.") from None


def consultar_conta(
    auth: RPCAuth,
    carregar_configuracao: Callable[[], Mapping[str, Any]],
    *,
    autorizado: bool,
    transporte: TransporteConsulta | None = None,
) -> ContaGestaoClick:
    """Consulta lojas e todas as situações em até quatro GETs, sem importar vendas.

    Lojas não têm paginação no contrato público. Situações exigem metadados
    consistentes em até três páginas de até cem registros; qualquer lacuna aborta
    toda a consulta. As URLs são construídas localmente e não há retentativas.
    """
    with auth.operation():
        stamp = _stamp(auth)
        config = _configuracao(auth, carregar_configuracao, stamp, autorizado)
        base = config.validar_conexao()
        transport = transporte or TransporteHTTPS()
        requests = 0

        def revalidar() -> None:
            """Impede continuação ou entrega após troca de acesso, configuração ou organização."""
            atual = _configuracao(auth, carregar_configuracao, stamp, autorizado)
            if atual != config:
                raise AuthorizationError("A configuração protegida mudou. Consulte a conta novamente.")

        def consultar(path: str) -> dict:
            """Faz somente GET fixo, com autorização antes e depois, inclusive nas falhas."""
            nonlocal requests
            if requests >= 1 + _MAX_PAGINAS:
                raise ContratoInvalido("O catálogo excedeu o limite de consultas autorizado.")
            if requests:
                time.sleep(config.interval_seconds)
            revalidar()
            requests += 1
            try:
                response = transport.get(base + path, {
                    "access-token": config.access_token,
                    "secret-access-token": config.secret_access_token,
                    "Accept": "application/json",
                }, min(config.timeout_seconds, 15))
            except (TimeoutError, OSError, GestaoClickError):
                revalidar()
                raise GestaoClickError("A consulta não respondeu; tente novamente mais tarde.") from None
            revalidar()
            if type(response.status) is not int:
                raise GestaoClickError("O GestãoClick retornou um estado de consulta inválido.")
            if response.status in {401, 403}:
                raise GestaoClickError("O GestãoClick recusou as credenciais ou as permissões de consulta.")
            if response.status == 429:
                raise GestaoClickError("Limite do GestãoClick atingido. Aguarde antes de consultar novamente.")
            if response.status != 200:
                raise GestaoClickError("Consulta recusada pelo GestãoClick; nenhum redirecionamento foi seguido.")
            payload = parse_json_seguro(response.body)
            if (type(payload.get("code")) is not int or payload["code"] != 200 or
                    payload.get("status") != "success"):
                raise ContratoInvalido("A resposta não corresponde ao catálogo esperado da conta.")
            return payload

        lojas_payload = consultar("/lojas")
        if lojas_payload.get("meta") is not None or lojas_payload.get("links") is not None:
            raise ContratoInvalido("A resposta de lojas diverge do catálogo sem paginação documentado.")
        lojas = _catalogo(lojas_payload.get("data"))
        situacoes: tuple[tuple[str, str], ...] = ()
        total = limite_efetivo = None
        limite_solicitado = _LIMITE
        for pagina in range(1, _MAX_PAGINAS + 1):
            payload = consultar(f"/situacoes_vendas?pagina={pagina}&limite={limite_solicitado}")
            rows = _catalogo(payload.get("data"))
            meta = payload.get("meta")
            total_pagina, limite, paginas = _conferir_pagina_situacoes(meta, len(rows), pagina)
            if total is None:
                total, limite_efetivo = total_pagina, limite
            if paginas > _MAX_PAGINAS:
                raise ContratoInvalido("As situações excedem o limite de três páginas; a consulta foi descartada.")
            if (total_pagina != total or limite != limite_efetivo or
                    {row[0] for row in situacoes} & {row[0] for row in rows}):
                raise PaginacaoSituacoesInvalida("SC_MUDANCA", meta)
            situacoes += rows
            if len(situacoes) == total:
                break
            if pagina == _MAX_PAGINAS:
                raise ContratoInvalido("Não foi possível comprovar todas as situações no limite autorizado.")
            limite_solicitado = _validar_proxima_url(meta["proxima_url"], base, pagina + 1,
                                                    (limite_solicitado, limite))
        revalidar()
        criada_em = time.monotonic()
        conta = ContaGestaoClick(stamp[1], stamp, criada_em, criada_em + CONTA_TTL_SECONDS,
                                lojas, situacoes, _vinculo_configuracao(config))
        return replace(conta, _assinatura=_assinatura(conta))


def registrar_fonte(auth: RPCAuth, conta: ContaGestaoClick, store_id: str) -> str:
    """Registra somente a loja do catálogo autorizado usando a RPC interna existente."""
    with auth.operation():
        _validar_conta(auth, conta)
        if not isinstance(store_id, str) or store_id not in {row[0] for row in conta._lojas}:
            raise AuthorizationError("Selecione uma loja da consulta autorizada desta conta.")
        payload = {"p_key": "gestaoclick-" + hashlib.sha256(store_id.encode()).hexdigest(),
                   "p_name": "GestãoClick: loja " + store_id, "p_stores": [store_id]}
        try:
            result = auth.rpc("integration_register_source", payload, "integrations.manage")
        except AppError:
            _validar_conta(auth, conta)
            raise GestaoClickError("Não foi possível registrar a fonte interna. Tente novamente.") from None
        _validar_conta(auth, conta)
        try:
            if not isinstance(result, dict) or not isinstance(result.get("id"), str):
                raise ValueError
            source_id = str(UUID(result["id"]))
        except (ValueError, TypeError, AttributeError):
            raise GestaoClickError("O registro da fonte não retornou um identificador válido.") from None
        return source_id
