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


@pytest.mark.parametrize("url", [
    "http://conta-ficticia.gestaoclick.com/api", "https://127.0.0.1/api", "https://localhost/api",
    "https://gestaoclick.com.evil.invalid/api", "https://user@gestaoclick.com/api",
    "https://gestaoclick.com:8443/api", "https://gestaoclick.com:abc/api", "https://gestaoclick.com/api?token=x",
    "https://gestaoclick.com/api#x", "https://gestaoclick.com/api/v2", "https://evil.invalid/api",
])
def test_destinos_nao_autorizados_nao_recebem_credenciais(url):
    """Bloqueia HTTP, IP, userinfo, portas, rotas antigas e sufixos maliciosos."""
    client, transport, _ = cliente(config=replace(CONFIG, base_url=url))
    with pytest.raises(ContratoInvalido):
        client.consultar("vendas")
    assert transport.calls == []


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
