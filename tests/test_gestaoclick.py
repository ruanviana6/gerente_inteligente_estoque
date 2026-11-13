"""Valida leitura estrita, integridade e falhas do GestãoClick sem rede real."""

import json
from dataclasses import replace
from unittest.mock import MagicMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from integracoes.gestaoclick import (
    MAX_RESPONSE_BYTES,
    ConsultaGestaoClick,
    ContratoGestaoClick,
    ContratoInvalido,
    GestaoClickClient,
    GestaoClickConfig,
    GestaoClickError,
    IntegracaoDesabilitada,
    MapaLinhaGestaoClick,
    RecursoConsulta,
    RespostaGestaoClick,
    TransporteHTTPS,
    normalizar_linhas,
    parse_json_seguro,
)

BASE = "https://conta-ficticia.gestaoclick.com/api"
CONFIG = GestaoClickConfig(True, BASE, "token-ficticio", "segredo-ficticio", "fonte-A", "loja-A")


def especificacao():
    """Produz contrato estritamente fictício; não representa a conta real do ERP."""
    parameters = [{"name": name, "in": "query", "schema": {"type": "string"}}
                  for name in ("pagina", "limite", "loja_id", "data_inicio", "data_fim")]
    return {"openapi": "3.0.3", "servers": [{"url": BASE}], "paths": {
        "/vendas": {"get": {"parameters": parameters, "responses": {"200": {"description": "Consulta"}}}},
    }}


def contrato():
    """Autoriza somente o GET fictício de vendas presente no OpenAPI de teste."""
    return ContratoGestaoClick.from_openapi(especificacao(), BASE, ("vendas",))


def contrato_leitura():
    """Descreve GET e filtros fictícios sem inferir qualquer mapeamento da conta real."""
    return {"version": 1, "base_url": BASE,
            "documentation_urls": ["https://central.gestaoclick.com.br/hc/pt-br/sections/132-api"],
            "resources": {"vendas": {"method": "GET", "filters": [
                "pagina", "limite", "loja_id", "data_inicio", "data_fim",
            ]}}}


def resposta(data=None, *, page=1, pages=1, total=None, next_url=None, status=200, **meta):
    """Monta resposta paginada para exercitar reconciliação e paginação."""
    data = [{"id": "001"}] if data is None else data
    payload = {"code": 200, "status": "success", "data": data, "meta": {
        "pagina_atual": page, "total_paginas": pages,
        "total_registros": len(data) if total is None else total, "proxima_url": next_url, **meta,
    }}
    return RespostaGestaoClick(status, json.dumps(payload).encode())


class TransporteFicticio:
    """Só possui GET e captura chamadas de teste sem abrir sockets."""

    def __init__(self, *responses):
        """Retém a sequência de respostas e falhas simuladas."""
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers, timeout):
        """Retorna a próxima resposta ou reproduz uma falha temporária."""
        self.calls.append((url, dict(headers), timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def cliente(*responses, config=CONFIG, **kwargs):
    """Cria cliente sem espera nem rede, com contagem de pausas verificável."""
    transport = TransporteFicticio(*responses)
    pauses = []
    result = GestaoClickClient(config, contrato(), transport, sleep=pauses.append, clock=lambda: 0, **kwargs)
    return result, transport, pauses


def test_configuracao_default_nao_faz_rede():
    """A ausência do opt-in impede até mesmo criar o transporte HTTPS."""
    client, transport, _ = cliente(config=GestaoClickConfig())
    with pytest.raises(IntegracaoDesabilitada):
        client.consultar("vendas")
    assert transport.calls == []
    assert GestaoClickConfig.from_mapping({}).enabled is False
    assert "token-ficticio" not in repr(CONFIG)
    assert "segredo-ficticio" not in repr(CONFIG)


def test_validacao_conexao_nao_inventa_escopo_nem_habilita_consulta_analitica():
    """Diagnóstico pode validar conexão; leitura analítica ainda exige fonte e loja."""
    config = replace(CONFIG, source_id="", store_id="")
    assert config.validar_conexao() == BASE
    with pytest.raises(ContratoInvalido):
        config.validar()
    client, transport, _ = cliente(config=config)
    with pytest.raises(ContratoInvalido):
        client.consultar("vendas")
    assert transport.calls == []
    with pytest.raises(IntegracaoDesabilitada):
        GestaoClickConfig().validar_conexao()


@pytest.mark.parametrize("changes", [
    {"access_token": ""}, {"access_token": "com espaço"}, {"secret_access_token": "\r\n"},
    {"timeout_seconds": 0}, {"interval_seconds": float("nan")}, {"max_attempts": True},
    {"max_pages": 0}, {"max_records": 100001}, {"max_requests": 5001},
])
def test_validacao_conexao_preserva_todas_as_barreiras_de_credenciais_e_limites(changes):
    """Retirar a exigência de escopo não relaxa credenciais nem orçamento de rede."""
    with pytest.raises(ContratoInvalido):
        replace(CONFIG, source_id="", store_id="", **changes).validar_conexao()


@pytest.mark.parametrize("base", [
    "https://api.gestaoclick.com", "https://api.gestaoclick.com/",
    "https://api.gestaoclick.com:443/", "https://conta-ficticia.gestaoclick.com/v1",
    BASE,
])
def test_base_oficial_raiz_e_prefixos_legados_geram_get_no_destino_conferido(base):
    """A raiz oficial é usada sem adicionar /api; aliases legados continuam explícitos."""
    config = replace(CONFIG, base_url=base)
    normalized_base = config.validar_conexao()
    spec = especificacao()
    spec["servers"] = [{"url": base}]
    contract = ContratoGestaoClick.from_openapi(spec, base, ("vendas",))
    transport = TransporteFicticio(resposta())
    client = GestaoClickClient(config, contract, transport)
    assert client.consultar("vendas").total == 1
    assert transport.calls[0][0].startswith(normalized_base + "/vendas?")


@pytest.mark.parametrize("url", [
    "http://conta-ficticia.gestaoclick.com/api", "https://127.0.0.1/api", "https://localhost/api",
    "https://gestaoclick.com.evil.invalid/api", "https://user@gestaoclick.com/api",
    "https://gestaoclick.com:8443/api", "https://gestaoclick.com:abc/api", "https://gestaoclick.com/api?token=x",
    "https://gestaoclick.com/api#x", "https://gestaoclick.com/api/v2", "https://evil.invalid/api",
    "https://gestaoclick.com", "https://gestaoclick.com.br/", "https://api.gestaoclick.com.br/",
    "https://conta-ficticia.gestaoclick.com/", "https://outra.api.gestaoclick.com/",
    "https://api.gestaoclick.com.evil.invalid/", "http://api.gestaoclick.com/",
    "https://user@api.gestaoclick.com/", "https://api.gestaoclick.com:8443/",
    "https://api.gestaoclick.com/?x=1", "https://api.gestaoclick.com/#x",
    "https://api.gestaoclick.com//", "https://api.gestaoclick.com///",
    "https://@api.gestaoclick.com/", "https://:@api.gestaoclick.com/",
    "https://api.gestaoclick.com:/", "https://api.gestaoclick.com:0443/",
    "https://api.gestaoclick.com/?", "https://api.gestaoclick.com/#",
    " https://api.gestaoclick.com/", "https://.gestaoclick.com/api",
    "https://a..gestaoclick.com/api", "https://-a.gestaoclick.com/api",
    "https://a-.gestaoclick.com/api", "https://api.gestaoclick.com/%2f",
    "https://api.gestaoclick.com/%2e", "https://api.gestaoclick.com/%2e%2e/",
    "https://api.gestaoclick.com/%2Fapi", "https://api%2egestaoclick.com/",
])
def test_destinos_nao_autorizados_nao_recebem_credenciais(url):
    """Bloqueia HTTP, IP, userinfo, portas, rotas antigas e sufixos maliciosos."""
    client, transport, _ = cliente(config=replace(CONFIG, base_url=url))
    with pytest.raises(ContratoInvalido):
        client.consultar("vendas")
    assert transport.calls == []


def test_contrato_de_leitura_deriva_get_e_filtros_sem_rede():
    """O formato alternativo produz a mesma autorização fechada do OpenAPI."""
    contract = ContratoGestaoClick.from_read_contract(contrato_leitura(), BASE, ("vendas",))
    assert contract == contrato()
    transport = TransporteFicticio(resposta())
    assert GestaoClickClient(CONFIG, contract, transport).consultar("vendas").total == 1
    assert transport.calls[0][0].startswith(BASE + "/vendas?")


@pytest.mark.parametrize("mutation", [
    lambda spec: spec.update(version=True),
    lambda spec: spec.update(version=2),
    lambda spec: spec.update(openapi="3.0.3"),
    lambda spec: spec.update(base_url="https://outra.gestaoclick.com/api"),
    lambda spec: spec.update(base_url="https://api.gestaoclick.com"),
    lambda spec: spec.pop("documentation_urls"),
    lambda spec: spec.update(documentation_urls=[]),
    lambda spec: spec.update(documentation_urls="https://gestaoclick.com"),
    lambda spec: spec.update(resources={}),
    lambda spec: spec["resources"].update(clientes={"method": "GET", "filters": ["pagina", "limite"]}),
    lambda spec: spec["resources"].update(produtos={"method": "POST", "filters": ["pagina", "limite"]}),
    lambda spec: spec["resources"]["vendas"].update(method="get"),
    lambda spec: spec["resources"]["vendas"].update(method="POST"),
    lambda spec: spec["resources"]["vendas"].update(path="/vendas"),
    lambda spec: spec["resources"]["vendas"].update(filters="pagina,limite"),
    lambda spec: spec["resources"]["vendas"].update(filters=["pagina", "limite", "pagina"]),
    lambda spec: spec["resources"]["vendas"].update(filters=["pagina", "loja_id"]),
    lambda spec: spec["resources"]["vendas"].update(filters=["pagina", "limite", "filtro&escopo"]),
    lambda spec: spec["resources"]["vendas"].update(filters=["pagina", "limite", {}]),
])
def test_contrato_de_leitura_recusa_schema_ambiguo_e_operacoes_nao_autorizadas(mutation):
    """Não aceita rota, escrita ou filtro oculto mesmo em recurso não selecionado."""
    spec = contrato_leitura()
    mutation(spec)
    with pytest.raises(ContratoInvalido):
        ContratoGestaoClick.from_read_contract(spec, BASE, ("vendas",))


@pytest.mark.parametrize("reference", [
    "http://central.gestaoclick.com.br/documentacao", "https://evil.invalid/documentacao",
    "https://central.gestaoclick.com.br.evil.invalid/documentacao",
    "https://user@gestaoclick.com/documentacao", "https://gestaoclick.com:8443/documentacao",
    "https://127.0.0.1/documentacao", "https://gestaoclick.com/documentacao?token=privado",
    "https://gestaoclick.com/documentacao\n", None,
    "https://@gestaoclick.com/documentacao", "https://:@gestaoclick.com/documentacao",
    "https://gestaoclick.com:/documentacao", "https://gestaoclick.com:0443/documentacao",
    "https://gestaoclick.com/documentacao?", " https://gestaoclick.com/documentacao",
    "https://a..gestaoclick.com/documentacao", "https://-a.gestaoclick.com/documentacao",
])
def test_contrato_de_leitura_exige_referencias_oficiais_https(reference):
    """Fontes ausentes, endereços externos e credenciais em URLs não aprovam contrato."""
    spec = contrato_leitura()
    spec["documentation_urls"] = [reference]
    with pytest.raises(ContratoInvalido):
        ContratoGestaoClick.from_read_contract(spec, BASE, ("vendas",))


def test_contrato_leitura_valida_recursos_requeridos_e_raiz_exata():
    """Mesmo contrato de outra base ou sem o recurso pedido não habilita consultas."""
    spec = contrato_leitura()
    spec["base_url"] = "https://api.gestaoclick.com"
    contract = ContratoGestaoClick.from_read_contract(spec, "https://api.gestaoclick.com/", ("vendas",))
    assert contract.base_url == "https://api.gestaoclick.com"
    for resources in (("clientes",), ("produtos",), (), ("vendas", "vendas")):
        with pytest.raises(ContratoInvalido):
            ContratoGestaoClick.from_read_contract(spec, "https://api.gestaoclick.com", resources)
    spec["documentation_urls"] *= 2
    with pytest.raises(ContratoInvalido):
        ContratoGestaoClick.from_read_contract(spec, "https://api.gestaoclick.com", ("vendas",))


def test_openapi_exige_get_servidor_e_parametros_comprovados():
    """A existência de POST ou o nome de um recurso não autoriza sua consulta."""
    spec = especificacao()
    spec["paths"]["/vendas"]["post"] = spec["paths"]["/vendas"].pop("get")
    with pytest.raises(ContratoInvalido):
        ContratoGestaoClick.from_openapi(spec, BASE, ("vendas",))
    with pytest.raises(ContratoInvalido):
        ContratoGestaoClick.from_openapi(especificacao(), BASE, ("clientes",))
    with pytest.raises(ContratoInvalido):
        ContratoGestaoClick.from_openapi(especificacao(), "https://outra.gestaoclick.com/api", ("vendas",))
    spec = especificacao()
    spec["paths"]["/vendas"]["get"]["parameters"] = []
    with pytest.raises(ContratoInvalido):
        ContratoGestaoClick.from_openapi(spec, BASE, ("vendas",))


@pytest.mark.parametrize("mutation", [
    lambda spec: spec.update(openapi=3.1),
    lambda spec: spec.update(servers=None),
    lambda spec: spec.update(servers={"url": BASE}),
    lambda spec: spec.update(servers=[None]),
    lambda spec: spec.update(servers=[{"url": None}]),
    lambda spec: spec["paths"]["/vendas"].update(parameters=None),
    lambda spec: spec["paths"]["/vendas"].update(parameters={"name": "pagina"}),
    lambda spec: spec["paths"]["/vendas"]["get"].update(parameters=None),
    lambda spec: spec["paths"]["/vendas"]["get"].update(parameters={"name": "pagina"}),
])
def test_openapi_malformado_produz_erro_de_contrato_seguro(mutation):
    """Tipos inválidos não escapam como exceções do parser nem expõem o contrato."""
    spec = especificacao()
    mutation(spec)
    with pytest.raises(ContratoInvalido):
        ContratoGestaoClick.from_openapi(spec, BASE, ("vendas",))


def test_allowlist_de_rota_tambem_no_cliente():
    """Mesmo um objeto de contrato manipulado não vira cliente de rota arbitrária."""
    manipulated = ContratoGestaoClick(BASE, {"vendas": RecursoConsulta("vendas", "/clientes", frozenset())})
    transport = TransporteFicticio()
    client = GestaoClickClient(CONFIG, manipulated, transport)
    with pytest.raises(ContratoInvalido):
        client.consultar("vendas")
    assert transport.calls == []


def test_paginacao_completa_confere_filtros_e_usa_somente_get():
    """Segue somente a próxima URL conferida e reconcilia o total de registros."""
    next_url = BASE + "/vendas?loja_id=loja-A&pagina=2&limite=100"
    client, transport, _ = cliente(resposta(pages=2, total=2, next_url=next_url),
                                     resposta([{"id": "002"}], page=2, pages=2, total=2))
    result = client.consultar("vendas")
    assert isinstance(result, ConsultaGestaoClick)
    assert (result.paginas, result.total) == (2, 2)
    assert [item["id"] for item in result.registros] == ["001", "002"]
    assert len(transport.calls) == 2
    assert parse_qs(urlsplit(transport.calls[0][0]).query)["loja_id"] == ["loja-A"]
    assert transport.calls[0][1]["access-token"] == CONFIG.access_token
    assert not hasattr(transport, "post")
    assert "001" not in repr(result)


@pytest.mark.parametrize("next_url", [
    "https://evil.invalid/api/vendas?pagina=2&limite=100&loja_id=loja-A",
    BASE + "/clientes?pagina=2&limite=100&loja_id=loja-A",
    BASE + "/vendas?pagina=2&limite=100&loja_id=outra",
    BASE + "/vendas?pagina=2&limite=100&loja_id=loja-A&usuario_id=master",
    BASE + "/vendas?pagina=2&pagina=2&limite=100&loja_id=loja-A",
    BASE + "/vendas?pagina=3&limite=100&loja_id=loja-A",
])
def test_proxima_url_nao_pode_mudar_escopo_ou_destino(next_url):
    """Recusa SSRF, troca de recurso/loja, usuário e parâmetros duplicados."""
    client, transport, _ = cliente(resposta(pages=2, total=2, next_url=next_url))
    with pytest.raises(ContratoInvalido):
        client.consultar("vendas")
    assert len(transport.calls) == 1


@pytest.mark.parametrize("filters", [{"usuario_id": "master"}, {"pagina": "9"},
                                      {"loja_id": "outra"}, {"nao_documentado": "x"}])
def test_filtros_nao_autorizados_recusados_antes_da_rede(filters):
    """Filtros ignorados silenciosamente pelo ERP não são transmitidos por engano."""
    client, transport, _ = cliente()
    with pytest.raises(ContratoInvalido):
        client.consultar("vendas", filters)
    assert transport.calls == []


@pytest.mark.parametrize("status", [401, 403])
def test_credencial_e_permissao_recusadas_sem_retry(status):
    """Negações não são tratadas como indisponibilidade temporária."""
    client, transport, pauses = cliente(resposta(status=status))
    with pytest.raises(GestaoClickError, match="recusados"):
        client.consultar("vendas")
    assert len(transport.calls) == 1
    assert pauses == []


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_backoff_temporario_limitado(status):
    """Reexecuta somente GET e encerra dentro do orçamento configurado."""
    client, transport, pauses = cliente(resposta(status=status), resposta())
    assert client.consultar("vendas").total == 1
    assert len(transport.calls) == 2
    assert 1 in pauses


def test_timeout_retry_after_e_orcamento_total():
    """Respeita Retry-After e não contorna espera longa ou orçamento de chamadas."""
    client, transport, _ = cliente(TimeoutError("segredo que não deve aparecer"), resposta())
    assert client.consultar("vendas").total == 1
    client, _, pauses = cliente(RespostaGestaoClick(429, b"", {"Retry-After": "7"}), resposta())
    assert client.consultar("vendas").total == 1
    assert 7 in pauses
    client, transport, _ = cliente(RespostaGestaoClick(429, b"", {"Retry-After": "600"}))
    with pytest.raises(GestaoClickError, match="aguarde"):
        client.consultar("vendas")
    assert len(transport.calls) == 1
    client, transport, _ = cliente(resposta(status=503), config=replace(CONFIG, max_requests=1))
    with pytest.raises(GestaoClickError, match="requisições"):
        client.consultar("vendas")
    assert len(transport.calls) == 1


@pytest.mark.parametrize("response", [
    resposta(total=2), resposta(pages=0), resposta(total_registros_pagina=9),
    RespostaGestaoClick(200, b'{"code":200,"status":"success","data":[],"meta":null}'),
    RespostaGestaoClick(200, b'{"code":200,"status":"error","data":[]}'),
    RespostaGestaoClick(200, b'{"code":200,"status":"success","data":NaN}'),
    RespostaGestaoClick(302, b""), RespostaGestaoClick(200, b"x" * (MAX_RESPONSE_BYTES + 1)),
])
def test_http200_ou_redirect_nao_comprovam_integridade(response):
    """Contagens, formato e limite de bytes precisam passar antes de retornar dados."""
    client, _, _ = cliente(response)
    with pytest.raises(GestaoClickError):
        client.consultar("vendas")


@pytest.mark.parametrize("original,duplicate", [
    (b'"status": "success"', b'"status":"success","status":"success"'),
    (b'"total_registros": 1', b'"total_registros":1,"total_registros":1'),
    (b'"id": "001"', b'"id":"001","id":"001"'),
    (b'"id": "001"', b'"privado-segredo":"valor-privado","privado-segredo":"valor-privado"'),
])
def test_resposta_com_chave_duplicada_em_qualquer_nivel_nao_vira_analise(original, duplicate):
    """Rejeita ambiguidade no envelope, metadados e registros sem revelar conteúdo."""
    response = resposta()
    response = replace(response, body=response.body.replace(original, duplicate))
    client, transport, _ = cliente(response)
    with pytest.raises(ContratoInvalido, match="JSON") as exc:
        client.consultar("vendas")
    assert len(transport.calls) == 1
    assert "privado" not in str(exc.value)
    assert "segredo" not in str(exc.value)
    assert "001" not in str(exc.value)


@pytest.mark.parametrize("raw", [
    b'{"duplicada":1,"dupli\\u0063ada":1}',
    b'{"valor":NaN}', b'{"valor":Infinity}', b'{"valor":-Infinity}',
    b'{"privado":"\\ud800"}', b'{"\\ud800":"privado"}',
    b'{"privado":[{"valor":"\\udfff"}]}', b'{"privado":1e999}',
    b'{"privado":[{"valor":-1e999}]}',
    b'[]', b'"privado"', b'\xff', '{"privado":"\ud800"}',
    b' ' * (MAX_RESPONSE_BYTES + 1), None,
])
def test_parser_reutilizavel_recusa_json_ambiguo_ou_excessivo_sem_vazamento(raw):
    """O diagnóstico e o leitor podem compartilhar a mesma fronteira de JSON seguro."""
    with pytest.raises(ContratoInvalido) as exc:
        parse_json_seguro(raw)
    assert str(exc.value) == "JSON GestãoClick inválido ou ambíguo."


def test_parser_reutilizavel_aceita_objetos_json_texto_e_bytes():
    """O parser mantém IDs como texto sem requerer um envelope de consulta completa."""
    for raw in ('{"data":[{"id":"001"}]}', b'{"data":[{"id":"001"}]}'):
        assert parse_json_seguro(raw) == {"data": [{"id": "001"}]}


def test_parser_reutilizavel_trata_limite_de_recursao_sem_expor_resposta(monkeypatch):
    """A profundidade suportada pelo interpretador não altera a mensagem segura."""
    monkeypatch.setattr("integracoes.gestaoclick.json.loads", Mock(side_effect=RecursionError("privado")))
    with pytest.raises(ContratoInvalido) as exc:
        parse_json_seguro(b'{"data":[]}')
    assert str(exc.value) == "JSON GestãoClick inválido ou ambíguo."


def test_origem_alterada_durante_paginacao_e_limite_de_registros():
    """Uma contagem que muda torna a consulta parcial e impede uso analítico."""
    url = BASE + "/vendas?pagina=2&limite=100&loja_id=loja-A"
    client, _, _ = cliente(resposta(pages=2, total=2, next_url=url),
                              resposta(page=2, pages=2, total=3))
    with pytest.raises(ContratoInvalido, match="origem mudou"):
        client.consultar("vendas")
    client, _, _ = cliente(resposta(total=2), config=replace(CONFIG, max_records=1))
    with pytest.raises(ContratoInvalido, match="limite"):
        client.consultar("vendas")


def test_transporte_real_constroi_apenas_get_e_bloqueia_redirect(monkeypatch):
    """Inspeciona a requisição urllib sem conexão verdadeira e testa seu handler."""
    opener = MagicMock()
    response = Mock(status=200, headers={})
    response.read.return_value = b"{}"
    opener.open.return_value.__enter__.return_value = response
    factory = Mock(return_value=opener)
    monkeypatch.setattr("urllib.request.build_opener", factory)
    transport = TransporteHTTPS()
    transport.get(BASE + "/vendas", {"access-token": "ficticio"}, 20)
    request = opener.open.call_args.args[0]
    assert request.get_method() == "GET"
    assert request.data is None
    assert factory.call_args.args[0].redirect_request(None, None, 302, "", {}, "https://evil.invalid") is None


def mapa():
    """Declara nomes fictícios explicitamente, sem associá-los aos campos da conta real."""
    fields = {name: (name,) for name in ("document_id", "line_id", "product_id", "sku", "sale_date",
                                        "status", "quantity", "returned_quantity", "store_id")}
    return MapaLinhaGestaoClick(fields, {"1": "completed", "2": "potential", "3": "returned"})


def linha(**changes):
    """Produz uma linha fictícia estável e sem informações de cliente."""
    return {"document_id": "001", "line_id": "01", "product_id": "A", "sku": "0001",
            "sale_date": "2020-01-01", "status": "1", "quantity": "10.5",
            "returned_quantity": "1.5", "store_id": "loja-A", "cliente_nome": "NÃO COLETAR", **changes}


def test_normalizador_preserva_identidade_e_exclui_dados_pessoais():
    """Normaliza somente allowlist e preserva zeros de identificadores e SKU."""
    result = normalizar_linhas([linha()], mapa(), "fonte-A", "loja-A")[0]
    assert (result["document_id"], result["sku"]) == ("001", "0001")
    assert (result["quantity"], result["returned_quantity"]) == (10.5, 1.5)
    assert result["status"] == "completed"
    assert "cliente_nome" not in result


@pytest.mark.parametrize("changes", [
    {"status": "nao-mapeado"}, {"quantity": "NaN"}, {"quantity": True}, {"quantity": "1,5"},
    {"quantity": -1}, {"returned_quantity": "99"}, {"sale_date": "20200101"},
    {"sale_date": "01/01/2020"}, {"store_id": "outra"}, {"sku": "x\n"},
])
def test_normalizador_rejeita_ambiguidade_antes_de_persistir(changes):
    """Dados incompletos, status desconhecido e formatos inválidos não viram vendas."""
    with pytest.raises(ContratoInvalido):
        normalizar_linhas([linha(**changes)], mapa(), "fonte-A", "loja-A")


def test_normalizador_duplicidade_e_devolucoes_nao_inventadas():
    """Exige quantidade devolvida explícita e rejeita mesma linha duas vezes."""
    with pytest.raises(ContratoInvalido, match="duplicada"):
        normalizar_linhas([linha(), linha()], mapa(), "fonte-A", "loja-A")
    campos = dict(mapa().campos)
    del campos["returned_quantity"]
    with pytest.raises(ContratoInvalido, match="incompleto"):
        normalizar_linhas([linha()], MapaLinhaGestaoClick(campos, mapa().status), "fonte-A", "loja-A")
    incomplete = linha()
    del incomplete["returned_quantity"]
    with pytest.raises(ContratoInvalido, match="ausente"):
        normalizar_linhas([incomplete], mapa(), "fonte-A", "loja-A")


def test_documentos_com_linhas_sao_expandidos_sem_deduzir_cestas():
    """Compartilha a identidade real do documento, preservando a identidade dos itens."""
    from integracoes.gestaoclick import MapaDocumentoGestaoClick, normalizar_documentos

    document_fields = {key: (key,) for key in ("document_id", "sale_date", "status", "store_id")}
    line_fields = {key: (key,) for key in ("line_id", "product_id", "sku", "quantity", "returned_quantity")}
    contract = MapaDocumentoGestaoClick(("itens-ficticios",), document_fields, line_fields, mapa().status)
    first = linha()
    document = {**first, "itens-ficticios": [first, linha(line_id="02", sku="0002", product_id="B")]}
    result = normalizar_documentos([document], contract, "fonte-A", "loja-A")
    assert [item["document_id"] for item in result] == ["001", "001"]
    assert [item["sku"] for item in result] == ["0001", "0002"]
    assert all("cliente_nome" not in item for item in result)
    with pytest.raises(ContratoInvalido, match="sem itens"):
        normalizar_documentos([{**document, "itens-ficticios": []}], contract, "fonte-A", "loja-A")


@pytest.mark.parametrize("value", ["yes", "1", 1, None, [], {}])
def test_ativacao_ambigua_e_recusada(value):
    """Somente booleano explícito ou texto true/false pode definir o opt-in."""
    with pytest.raises(ContratoInvalido):
        GestaoClickConfig.from_mapping({"GESTAOCLICK_ENABLED": value})
