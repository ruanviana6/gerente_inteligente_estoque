"""Conector GestãoClick de consulta: nenhum caminho permite escrita no ERP.

A especificação da conta é obrigatória para habilitar recursos. O cliente não
recebe rotas do navegador, não segue redirects e nunca infere cobertura de HTTP 200.
"""

from __future__ import annotations

import ipaddress
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


class GestaoClickError(Exception):
    """Expõe somente um diagnóstico seguro, sem resposta, URL ou credenciais."""


class IntegracaoDesabilitada(GestaoClickError):
    """Indica que as condições administrativas de ativação não foram satisfeitas."""


class ContratoInvalido(GestaoClickError):
    """Recusa contrato ambíguo ou resposta cuja integridade não foi demonstrada."""


# Recursos de consulta publicados nas fontes oficiais. Um recurso só é usado
# quando seu GET também existe no contrato conferido da conta autorizada.
RECURSOS_CONSULTA = frozenset({"produtos", "vendas", "lojas", "situacoes_vendas"})
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_RESPONSE_BYTES = 20 * 1024 * 1024


def _texto(value: Any, limite: int = 200) -> str:
    """Aceita identificadores textuais sem controles, preservando zeros à esquerda."""
    if not isinstance(value, str) or not value or len(value) > limite:
        raise ContratoInvalido("Identificador ou configuração textual inválida.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ContratoInvalido("Identificador ou configuração textual inválida.")
    return value


def _host_oficial(host: str) -> bool:
    """Confere domínio oficial com labels DNS completos, sem sufixos parecidos."""
    return (len(host) <= 253 and
            all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in host.split(".")) and
            any(host == suffix or host.endswith("." + suffix)
                for suffix in ("gestaoclick.com", "gestaoclick.com.br")))


def _base(value: Any) -> str:
    """Restringe HTTPS oficial; somente api.gestaoclick.com pode usar a raiz."""
    value = _texto(value, 512)
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ContratoInvalido("Endereço GestãoClick inválido.") from None
    host = parsed.hostname or ""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ContratoInvalido("O destino deve ser um domínio oficial conferido.")
    # Não basta startswith: usuario@host, sufixos falsos e portas são recusados.
    if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None or
            port not in (None, 443) or parsed.netloc.lower() not in {host, host + ":443"} or
            "?" in value or "#" in value or any(char.isspace() for char in value) or
            not (parsed.path.rstrip("/") in {"/api", "/v1"} or
                 host == "api.gestaoclick.com" and parsed.path in {"", "/"}) or
            not _host_oficial(host)):
        raise ContratoInvalido("Endereço GestãoClick fora dos destinos oficiais permitidos.")
    return f"https://{host}{parsed.path.rstrip('/')}"


@dataclass(frozen=True, repr=False)
class GestaoClickConfig:
    """Mantém tokens somente no servidor; ativação é explícita e desabilitada por padrão."""

    enabled: bool = False
    base_url: str = ""
    access_token: str = field(default="", repr=False)
    secret_access_token: str = field(default="", repr=False)
    source_id: str = ""
    store_id: str = ""
    timeout_seconds: float = 20
    interval_seconds: float = 1
    max_attempts: int = 3
    max_pages: int = 500
    max_records: int = 20000
    max_requests: int = 1000

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> GestaoClickConfig:
        """Lê o modelo de Secrets sem aceitar strings ambíguas como booleanos."""
        enabled = values.get("GESTAOCLICK_ENABLED", False)
        if not (type(enabled) is bool or isinstance(enabled, str) and enabled in {"true", "false"}):
            raise ContratoInvalido("GESTAOCLICK_ENABLED deve ser true ou false.")
        return cls(
            enabled=enabled is True or enabled == "true",
            base_url=values.get("GESTAOCLICK_BASE_URL", ""),
            access_token=values.get("GESTAOCLICK_ACCESS_TOKEN", ""),
            secret_access_token=values.get("GESTAOCLICK_SECRET_ACCESS_TOKEN", ""),
            source_id=values.get("GESTAOCLICK_SOURCE_ID", ""),
            store_id=values.get("GESTAOCLICK_STORE_ID", ""),
        )

    def validar_conexao(self) -> str:
        """Confere ativação, destino, credenciais e limites sem presumir loja ou fonte."""
        if self.enabled is not True:
            raise IntegracaoDesabilitada("Integração GestãoClick não habilitada.")
        base = _base(self.base_url)
        for value in (self.access_token, self.secret_access_token):
            _texto(value, 4096)
            if not value.isascii() or any(char.isspace() for char in value):
                raise ContratoInvalido("Credencial GestãoClick em formato inválido.")
        for value in (self.timeout_seconds, self.interval_seconds):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ContratoInvalido("Limites de rede inválidos.")
        if not 1 <= self.timeout_seconds <= 60 or not 0.1 <= self.interval_seconds <= 60:
            raise ContratoInvalido("Limites de rede inválidos.")
        for value, maximum in ((self.max_attempts, 5), (self.max_pages, 1000),
                               (self.max_records, 100000), (self.max_requests, 5000)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ContratoInvalido("Limites da consulta inválidos.")
        return base

    def validar(self) -> str:
        """Exige também o escopo da fonte antes de qualquer consulta analítica."""
        base = self.validar_conexao()
        _texto(self.source_id, 120)
        _texto(self.store_id, 120)
        return base


@dataclass(frozen=True)
class RecursoConsulta:
    """Guarda o GET validado e sua allowlist de filtros, sem aceitar rotas externas."""

    nome: str
    path: str
    filtros: frozenset[str]


@dataclass(frozen=True, repr=False)
class ContratoGestaoClick:
    """Vincula cada recurso permitido ao contrato de leitura conferido da conta."""

    base_url: str
    recursos: Mapping[str, RecursoConsulta]

    @classmethod
    def from_openapi(
        cls, documento: Mapping[str, Any], base_url: str,
        recursos: tuple[str, ...] = ("produtos", "vendas", "lojas"),
    ) -> ContratoGestaoClick:
        """Extrai somente GETs fechados; não importa métodos de escrita da especificação."""
        base = _base(base_url)
        if (not isinstance(documento, Mapping) or not isinstance(documento.get("openapi"), str) or
                not documento["openapi"].startswith("3.")):
            raise ContratoInvalido("É necessária a especificação OpenAPI 3 da conta.")
        servers = documento.get("servers", [])
        if (not isinstance(servers, list) or
                any(not isinstance(item, dict) or not isinstance(item.get("url"), str) for item in servers)):
            raise ContratoInvalido("Servidores inválidos no OpenAPI da conta.")
        official_servers = set()
        for server in servers:
            try:
                official_servers.add(_base(server["url"]))
            except ContratoInvalido:
                # Um OpenAPI pode listar outros ambientes; nenhum deles vira
                # destino autorizado sem passar pela mesma validação da base.
                continue
        if base not in official_servers:
            raise ContratoInvalido("A base não corresponde ao servidor do OpenAPI conferido.")
        paths = documento.get("paths")
        if not isinstance(paths, dict) or not recursos or set(recursos) - RECURSOS_CONSULTA:
            raise ContratoInvalido("Recurso fora da allowlist de consultas.")
        result = {}
        for nome in recursos:
            path = "/" + nome
            entry = paths.get(path, {})
            operation = entry.get("get") if isinstance(entry, dict) else None
            if not isinstance(operation, dict) or not isinstance(operation.get("responses"), dict):
                raise ContratoInvalido("GET de consulta ausente na especificação da conta.")
            if "200" not in operation["responses"]:
                raise ContratoInvalido("GET sem resposta 200 documentada.")
            filters = set()
            shared_parameters = entry.get("parameters", [])
            operation_parameters = operation.get("parameters", [])
            if not isinstance(shared_parameters, list) or not isinstance(operation_parameters, list):
                raise ContratoInvalido("Parâmetros inválidos no OpenAPI da conta.")
            for parameter in shared_parameters + operation_parameters:
                if not isinstance(parameter, dict) or "$ref" in parameter:
                    raise ContratoInvalido("Resolva referências de parâmetros antes de aprovar o contrato.")
                if parameter.get("in") == "query":
                    name = parameter.get("name")
                    if not isinstance(name, str) or not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
                        raise ContratoInvalido("Filtro documentado inválido.")
                    filters.add(name)
            if not {"pagina", "limite"}.issubset(filters):
                raise ContratoInvalido("Paginação precisa estar documentada para esta consulta.")
            result[nome] = RecursoConsulta(nome, path, frozenset(filters))
        return cls(base, result)

    @classmethod
    def from_read_contract(
        cls, documento: Mapping[str, Any], base_url: str,
        recursos: tuple[str, ...] = ("produtos", "vendas", "lojas"),
    ) -> ContratoGestaoClick:
        """Valida contrato declarativo equivalente quando não há arquivo OpenAPI.

        O formato fechado contém version=1, base_url, documentation_urls e
        resources. Cada recurso declara somente method="GET" e filters; sua
        rota é derivada do nome permitido. As referências são conferidas
        localmente, sem baixar páginas nem presumir que comprovem a conta.
        Mapas de campos/status e filtros temporais continuam obrigatórios no
        leitor. Cabe ao administrador conferir o conteúdo nas fontes citadas.
        """
        base = _base(base_url)
        if (not isinstance(documento, Mapping) or
                set(documento) != {"version", "base_url", "documentation_urls", "resources"} or
                type(documento.get("version")) is not int or documento["version"] != 1):
            raise ContratoInvalido("Formato ou versão do contrato de leitura inválido.")
        if _base(documento["base_url"]) != base:
            raise ContratoInvalido("A base não corresponde ao contrato de leitura conferido.")
        references = documento["documentation_urls"]
        if not isinstance(references, list) or not 1 <= len(references) <= 20:
            raise ContratoInvalido("O contrato exige referências oficiais HTTPS.")
        seen_references = set()
        for reference in references:
            _referencia_oficial(reference)
            if reference in seen_references:
                raise ContratoInvalido("Referência oficial repetida no contrato de leitura.")
            seen_references.add(reference)
        entries = documento["resources"]
        if (not isinstance(entries, dict) or not entries or set(entries) - RECURSOS_CONSULTA or
                not recursos or set(recursos) - RECURSOS_CONSULTA or
                len(set(recursos)) != len(recursos) or set(recursos) - set(entries)):
            raise ContratoInvalido("Recurso fora da allowlist do contrato de leitura.")
        result = {}
        # Validar também recursos não selecionados impede esconder uma operação
        # indevida em um contrato que aparentemente autoriza apenas vendas.
        for nome, entry in entries.items():
            if (not isinstance(entry, dict) or set(entry) != {"method", "filters"} or
                    entry["method"] != "GET"):
                raise ContratoInvalido("O contrato de leitura permite somente método GET e filtros.")
            filters = entry["filters"]
            if (not isinstance(filters, list) or not 2 <= len(filters) <= 64 or
                    any(not isinstance(name, str) or not re.fullmatch(r"[a-z_][a-z0-9_]*", name)
                        for name in filters) or len(set(filters)) != len(filters)):
                raise ContratoInvalido("Filtro documentado inválido ou repetido.")
            if not {"pagina", "limite"}.issubset(filters):
                raise ContratoInvalido("Paginação precisa estar documentada para esta consulta.")
            if nome in recursos:
                result[nome] = RecursoConsulta(nome, "/" + nome, frozenset(filters))
        return cls(base, result)


def _referencia_oficial(value: Any) -> None:
    """Exige referência HTTPS oficial sem userinfo, porta alternativa ou query."""
    value = _texto(value, 2048)
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ContratoInvalido("Referência oficial HTTPS inválida.") from None
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None or
            port not in (None, 443) or parsed.netloc.lower() not in {host, host + ":443"} or
            "?" in value or any(char.isspace() for char in value) or not _host_oficial(host)):
        raise ContratoInvalido("O contrato exige referências oficiais HTTPS.")


@dataclass(frozen=True, repr=False)
class RespostaGestaoClick:
    """Transporta somente os bytes e metadados necessários; repr não revela dados."""

    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


class TransporteConsulta(Protocol):
    """Contrato de transporte com apenas GET, sem argumento de método nem payload."""

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> RespostaGestaoClick:
        """Consulta HTTPS e nunca transmite um corpo ao ERP."""
        ...


class _SemRedirect(urllib.request.HTTPRedirectHandler):
    """Bloqueia redirects inclusive para outros endereços da mesma origem."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Impede o reenvio de credenciais em redirecionamentos."""
        return None


class TransporteHTTPS:
    """Transporte síncrono com TLS padrão, proxy do ambiente e resposta limitada."""

    def __init__(self) -> None:
        """Reutiliza handlers sem cookies e sem guardar headers de autenticação."""
        self._opener = urllib.request.build_opener(_SemRedirect())

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> RespostaGestaoClick:
        """Fixa GET na construção da requisição e recusa resposta grande demais."""
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        try:
            with self._opener.open(request, timeout=timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise GestaoClickError("Resposta GestãoClick excedeu o limite de tamanho.")
                return RespostaGestaoClick(response.status, body, dict(response.headers))
        except urllib.error.HTTPError as exc:
            status, response_headers = exc.code, dict(exc.headers)
            exc.close()
            return RespostaGestaoClick(status, b"", response_headers)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TimeoutError("Consulta GestãoClick indisponível.") from None


@dataclass(frozen=True, repr=False)
class ConsultaGestaoClick:
    """Entrega um conjunto completo conferido; registros não aparecem em repr/logs."""

    registros: tuple[dict, ...]
    paginas: int
    total: int
    execucao_id: str


def _inteiro(value: Any) -> int:
    """Recusa booleanos, valores fracionários e metadados negativos."""
    if type(value) is not int or value < 0:
        raise ContratoInvalido("Metadados de paginação inválidos.")
    return value


def _rejeitar_constante(value: str) -> None:
    """Recusa NaN e infinito que o parser JSON padrão aceitaria por compatibilidade."""
    raise ValueError("Número JSON inválido.")


def _objeto_sem_duplicatas(pairs: list[tuple[str, Any]]) -> dict:
    """Recusa chaves duplicadas em qualquer nível sem incluir nomes ou valores no erro."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Chave JSON repetida.")
        result[key] = value
    return result


def parse_json_seguro(raw: str | bytes) -> dict:
    """Lê JSON limitado sem duplicatas, Unicode inválido, não finitos ou conteúdo em erros."""
    try:
        if not isinstance(raw, (str, bytes)):
            raise ValueError("Tipo JSON inválido.")
        size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
        if size > MAX_RESPONSE_BYTES:
            raise ValueError("JSON excede limite.")
        result = json.loads(raw, parse_constant=_rejeitar_constante, object_pairs_hook=_objeto_sem_duplicatas)
        if not isinstance(result, dict):
            raise ValueError("Objeto JSON obrigatório.")
        # Escapes Unicode e expoentes são resolvidos pelo parser: validar o
        # resultado também recusa surrogates isolados e números como 1e999.
        pending = [result]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, str):
                value.encode("utf-8")
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Número JSON inválido.")
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise ContratoInvalido("JSON GestãoClick inválido ou ambíguo.") from None
    return result


class GestaoClickClient:
    """Executa apenas recursos GET documentados, com cobertura e limites explícitos."""

    def __init__(self, config: GestaoClickConfig, contrato: ContratoGestaoClick,
                 transport: TransporteConsulta | None = None, *,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        """Isola credenciais e limites por instância, nunca em cache global."""
        self.config, self.contrato = config, contrato
        self._transport = transport
        self._sleep, self._clock = sleep, clock
        self._last_request: float | None = None
        self._requests = 0
        self._response_bytes = 0

    def _get(self, url: str) -> dict:
        """Aplica ritmo, tentativas limitadas e erros seguros, sem registrar o retorno."""
        # A fronteira que recebe os tokens revalida o destino mesmo quando
        # um futuro chamador interno deixar de passar por consultar().
        base = self.config.validar()
        try:
            parsed = urllib.parse.urlsplit(url)
            base_parts = urllib.parse.urlsplit(base)
        except (TypeError, ValueError):
            raise ContratoInvalido("Destino da consulta inválido.") from None
        allowed = {
            base_parts.path + endpoint.path
            for name, endpoint in self.contrato.recursos.items()
            if name in RECURSOS_CONSULTA and endpoint.nome == name and endpoint.path == "/" + name
        }
        if (base != self.contrato.base_url or parsed.scheme != "https" or
                parsed.netloc != base_parts.netloc or parsed.path not in allowed or parsed.fragment):
            raise ContratoInvalido("Destino ou rota fora da allowlist de consultas.")
        for attempt in range(self.config.max_attempts):
            if self._requests >= self.config.max_requests:
                raise GestaoClickError("Limite de requisições atingido; retome em outra execução.")
            if self._last_request is not None:
                self._sleep(max(0, self.config.interval_seconds - (self._clock() - self._last_request)))
            self._last_request = self._clock()
            self._requests += 1
            try:
                if self._transport is None:
                    self._transport = TransporteHTTPS()
                response = self._transport.get(url, {
                    "access-token": self.config.access_token,
                    "secret-access-token": self.config.secret_access_token,
                    "Accept": "application/json",
                }, self.config.timeout_seconds)
            except (TimeoutError, OSError):
                response = RespostaGestaoClick(503, b"")
            if response.status in {401, 403}:
                raise GestaoClickError("Credenciais, usuário, loja ou permissões recusados pelo GestãoClick.")
            if response.status in {429, 500, 502, 503, 504}:
                if attempt + 1 == self.config.max_attempts:
                    raise GestaoClickError("Consulta temporariamente indisponível; tentativas esgotadas.")
                retry_after = next((v for k, v in response.headers.items() if k.lower() == "retry-after"), "")
                delay = 2 ** attempt
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except (TypeError, ValueError):
                        raise GestaoClickError("Limite temporário: aguarde antes de retomar.") from None
                    if not math.isfinite(delay) or delay > 60:
                        raise GestaoClickError("Limite temporário: aguarde antes de retomar.")
                self._sleep(delay)
                continue
            if response.status != 200:
                raise GestaoClickError("Consulta GestãoClick recusada; redirects não são permitidos.")
            self._response_bytes += len(response.body)
            if (len(response.body) > MAX_RESPONSE_BYTES or
                    self._response_bytes > MAX_TOTAL_RESPONSE_BYTES):
                raise ContratoInvalido("Resposta GestãoClick excedeu o limite de tamanho.")
            result = parse_json_seguro(response.body)
            if result.get("code") != 200 or result.get("status") != "success":
                raise ContratoInvalido("Envelope de consulta GestãoClick inválido.")
            return result
        raise GestaoClickError("Consulta não concluída.")

    def consultar(self, recurso: str, filtros: Mapping[str, str] | None = None) -> ConsultaGestaoClick:
        """Varre páginas conferindo contagens e URLs; nunca retorna um conjunto parcial."""
        base = self.config.validar()
        if (base != self.contrato.base_url or recurso not in RECURSOS_CONSULTA or
                recurso not in self.contrato.recursos):
            raise ContratoInvalido("Recurso ou servidor não aprovados no contrato.")
        endpoint = self.contrato.recursos[recurso]
        if endpoint.nome != recurso or endpoint.path != "/" + recurso:
            raise ContratoInvalido("Rota fora da allowlist de consultas.")
        filters = dict(filtros or {})
        if set(filters) - endpoint.filtros or {"pagina", "limite", "usuario_id"} & set(filters):
            raise ContratoInvalido("Filtro não autorizado para a consulta.")
        for value in filters.values():
            _texto(value, 512)
        if "loja_id" in endpoint.filtros:
            if "loja_id" in filters and filters["loja_id"] != self.config.store_id:
                raise ContratoInvalido("Loja diferente da fonte autorizada.")
            filters["loja_id"] = self.config.store_id
        elif "loja_id" in filters:
            raise ContratoInvalido("Filtro de loja não documentado.")
        filters.update({"pagina": "1", "limite": "100"})
        url = base + endpoint.path + "?" + urllib.parse.urlencode(filters)
        seen_urls, result = set(), []
        expected_total = expected_pages = None
        for page in range(1, self.config.max_pages + 1):
            if url in seen_urls:
                raise ContratoInvalido("Ciclo detectado na paginação.")
            seen_urls.add(url)
            response = self._get(url)
            data, meta = response.get("data"), response.get("meta")
            if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
                raise ContratoInvalido("Lista de registros inválida ou incompleta.")
            if not isinstance(meta, dict):
                raise ContratoInvalido("Cobertura sem metadados não aprovada para sincronização.")
            total, pages = _inteiro(meta.get("total_registros")), _inteiro(meta.get("total_paginas"))
            if (pages == 0 and total != 0) or _inteiro(meta.get("pagina_atual")) != page or len(data) > 100:
                raise ContratoInvalido("Sequência de páginas inválida.")
            if expected_total is None:
                expected_total, expected_pages = total, pages
            if total != expected_total or pages != expected_pages or pages > self.config.max_pages:
                raise ContratoInvalido("A origem mudou durante a consulta ou excedeu o limite.")
            if total > self.config.max_records or len(result) + len(data) > self.config.max_records:
                raise ContratoInvalido("Consulta excede o limite de registros.")
            if "total_registros_pagina" in meta and _inteiro(meta["total_registros_pagina"]) != len(data):
                raise ContratoInvalido("Contagem da página diverge dos registros.")
            result.extend(data)
            next_url = meta.get("proxima_url")
            if next_url is None:
                if page != max(1, pages) or len(result) != total:
                    raise ContratoInvalido("Consulta parcial: contagens não reconciliadas.")
                return ConsultaGestaoClick(tuple(result), page, total, str(uuid.uuid4()))
            if not data or page >= pages or not isinstance(next_url, str):
                raise ContratoInvalido("Próxima página inconsistente.")
            parsed = urllib.parse.urlsplit(urllib.parse.urljoin(url, next_url))
            base_parts = urllib.parse.urlsplit(base)
            if (parsed.scheme != "https" or parsed.netloc != base_parts.netloc or parsed.fragment or
                    parsed.path != base_parts.path + endpoint.path):
                raise ContratoInvalido("Próxima página fora do destino autorizado.")
            try:
                pairs = urllib.parse.parse_qsl(parsed.query, strict_parsing=True)
                query = dict(pairs)
            except ValueError:
                raise ContratoInvalido("Parâmetros de paginação inválidos.") from None
            expected = {**filters, "pagina": str(page + 1)}
            if len(query) != len(pairs) or query != expected:
                raise ContratoInvalido("A paginação alterou os filtros autorizados.")
            url = urllib.parse.urlunsplit(parsed)
        raise ContratoInvalido("Paginação excedeu o limite; nenhum conjunto parcial foi aceito.")


def sincronizar_somente_leitura() -> None:
    """Mantém o contrato legado desabilitado: sincronização exige Master e prévia aprovada."""
    raise NotImplementedError("Integração GestãoClick não habilitada: configure o fluxo Master seguro.")


@dataclass(frozen=True, repr=False)
class MapaLinhaGestaoClick:
    """Mapeia campos de venda conferidos na conta, sem deduzir nomes nem status."""

    campos: Mapping[str, tuple[str, ...]]
    status: Mapping[str, str]


def _campo(registro: dict, caminho: tuple[str, ...]) -> Any:
    """Extrai um caminho declarativo de objeto, sem executar expressões da configuração."""
    value: Any = registro
    if not isinstance(caminho, tuple) or not caminho or len(caminho) > 6:
        raise ContratoInvalido("Mapeamento de campo inválido.")
    for part in caminho:
        if not isinstance(part, str) or not part or not isinstance(value, dict) or part not in value:
            raise ContratoInvalido("Campo obrigatório ausente na origem; análise dependente desabilitada.")
        value = value[part]
    return value


def _id(value: Any) -> str:
    """Preserva IDs e SKUs textuais e admite somente inteiros exatos como alternativa."""
    if type(value) is int and value >= 0:
        value = str(value)
    return _texto(value, 120)


def _quantidade(value: Any) -> float:
    """Confere decimal oficial com ponto, rejeitando booleanos, vírgulas e não finitos."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ContratoInvalido("Quantidade inválida na origem.")
    if isinstance(value, str) and (not value or "," in value or len(value) > 40):
        raise ContratoInvalido("Quantidade inválida na origem.")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ContratoInvalido("Quantidade inválida na origem.") from None
    if not math.isfinite(number) or not 0 <= number <= 1e12:
        raise ContratoInvalido("Quantidade inválida na origem.")
    return number


def normalizar_linhas(
    registros: tuple[dict, ...] | list[dict], mapa: MapaLinhaGestaoClick,
    source_id: str, store_id: str,
) -> list[dict]:
    """Transforma linhas autorizadas sem coletar clientes, inferir vendas ou baixar saldo.

    Quantidade devolvida deve estar mapeada explicitamente. Sua ausência não
    comprova devolução zero. O adaptador da conta precisa primeiro confirmar o
    contrato de documentos e itens e disponibilizar linhas estáveis ao conector.
    """
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    required = {"document_id", "line_id", "product_id", "sku", "sale_date", "status",
                "quantity", "returned_quantity", "store_id"}
    allowed = required | {"product_name", "category", "supplier", "store_id", "source_updated_at"}
    if not required.issubset(mapa.campos) or set(mapa.campos) - allowed:
        raise ContratoInvalido("Mapeamento incompleto ou campo não autorizado para vendas.")
    source_id, store_id = _id(source_id), _id(store_id)
    valid_status = {"potential", "separating", "completed", "cancelled", "returned", "unknown"}
    if not mapa.status or set(mapa.status.values()) - valid_status:
        raise ContratoInvalido("Mapa de status precisa ser conferido com a conta.")
    result, seen = [], set()
    for registro in registros:
        if not isinstance(registro, dict):
            raise ContratoInvalido("Linha de venda inválida na origem.")
        values = {name: _campo(registro, path) for name, path in mapa.campos.items()}
        if "store_id" in values and _id(values["store_id"]) != store_id:
            raise ContratoInvalido("Registro de loja diferente da fonte autorizada.")
        status = mapa.status.get(str(values["status"]))
        if status is None:
            raise ContratoInvalido("Status da origem não conferido; venda não inferida.")
        try:
            day = date.fromisoformat(values["sale_date"])
        except (TypeError, ValueError):
            raise ContratoInvalido("Data da venda inválida na origem.") from None
        if day.isoformat() != values["sale_date"] or day > datetime.now(ZoneInfo("America/Fortaleza")).date():
            raise ContratoInvalido("Data da venda precisa seguir AAAA-MM-DD.")
        quantity = _quantidade(values["quantity"])
        returned = _quantidade(values["returned_quantity"])
        if returned > quantity:
            raise ContratoInvalido("Devolução excede a quantidade da linha.")
        line = {name: _id(values[name]) for name in ("document_id", "line_id", "product_id", "sku")}
        line.update(source_id=source_id, store_id=store_id, sale_date=day.isoformat(),
                    status=status, quantity=quantity, returned_quantity=returned)
        for name in ("product_name", "category", "supplier"):
            value = values.get(name, "")
            line[name] = _texto(value, 300).strip() if value else ""
        if not line["product_name"]:
            line["product_name"] = f"SKU {line['sku']} (nome indisponível)"
        if "source_updated_at" in values:
            try:
                stamp = datetime.fromisoformat(values["source_updated_at"])
            except (TypeError, ValueError):
                raise ContratoInvalido("Data de alteração inválida na origem.") from None
            if stamp.tzinfo is None:
                raise ContratoInvalido("Data de alteração sem fuso não aceita.")
            line["source_updated_at"] = stamp.isoformat()
        key = (source_id, store_id, line["document_id"], line["line_id"])
        if key in seen:
            raise ContratoInvalido("Linha duplicada entre páginas; reconcilie a origem.")
        seen.add(key)
        result.append(line)
    return result


@dataclass(frozen=True, repr=False)
class MapaDocumentoGestaoClick:
    """Declara a coleção de itens e separa campos do documento dos campos da linha."""

    caminho_linhas: tuple[str, ...]
    campos_documento: Mapping[str, tuple[str, ...]]
    campos_linha: Mapping[str, tuple[str, ...]]
    status: Mapping[str, str]


def normalizar_documentos(
    documentos: tuple[dict, ...] | list[dict], mapa: MapaDocumentoGestaoClick,
    source_id: str, store_id: str,
) -> list[dict]:
    """Expande documentos e itens com um contrato explícito, descartando dados pessoais.

    O endereço de vendas pode retornar documentos com uma coleção de itens.
    O nome dessa coleção e os campos herdados são conferidos no OpenAPI da
    conta, sem presumir que dois produtos vendidos no mesmo dia sejam uma cesta.
    """
    if set(mapa.campos_documento) & set(mapa.campos_linha):
        raise ContratoInvalido("Mapeamento de documento e linha contém campos sobrepostos.")
    flat = []
    for document in documentos:
        if not isinstance(document, dict):
            raise ContratoInvalido("Documento de venda inválido.")
        document_values = {key: _campo(document, path) for key, path in mapa.campos_documento.items()}
        lines = _campo(document, mapa.caminho_linhas)
        if not isinstance(lines, list) or not lines:
            raise ContratoInvalido("Documento sem itens verificáveis; reconcilie a origem.")
        if len(flat) + len(lines) > 20000:
            raise ContratoInvalido("Quantidade de linhas excede o limite de sincronização.")
        for line in lines:
            if not isinstance(line, dict):
                raise ContratoInvalido("Item de venda inválido.")
            flat.append({**document_values, **{
                key: _campo(line, path) for key, path in mapa.campos_linha.items()
            }})
    campos = {name: (name,) for name in set(mapa.campos_documento) | set(mapa.campos_linha)}
    return normalizar_linhas(flat, MapaLinhaGestaoClick(campos, mapa.status), source_id, store_id)
