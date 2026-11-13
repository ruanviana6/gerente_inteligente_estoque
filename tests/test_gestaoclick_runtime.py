"""Verifica configuração protegida até a leitura GET e normalização de vendas."""

import json
from datetime import date, datetime
from unittest.mock import patch

import pytest

from integracoes.gestaoclick import ContratoInvalido, IntegracaoDesabilitada, RespostaGestaoClick
from integracoes.runtime import build_reader

BASE = "https://conta-ficticia.gestaoclick.com/api"


def secrets(*, mode="documents", contract="openapi"):
    """Produz Secrets fictícios completos, sem representar o contrato da conta real."""
    parameters = [{"in": "query", "name": name} for name in (
        "pagina", "limite", "loja_id", "inicio_ficticio", "fim_ficticio", "tipo_ficticio",
    )]
    spec = {"openapi": "3.0.3", "servers": [{"url": BASE}], "paths": {
        "/vendas": {"get": {"parameters": parameters, "responses": {"200": {"description": "Consulta"}}}},
    }}
    doc_fields = {name: [name] for name in ("document_id", "sale_date", "status", "store_id")}
    line_fields = {name: [name] for name in ("line_id", "product_id", "sku", "quantity", "returned_quantity")}
    mapping = {"mode": mode, "lines_path": ["itens_ficticios"],
               "document_fields": doc_fields, "line_fields": line_fields}
    if mode == "lines":
        mapping = {"mode": mode, "fields": {**doc_fields, **line_fields}}
    values = {
        "GESTAOCLICK_ENABLED": True, "GESTAOCLICK_BASE_URL": BASE,
        "GESTAOCLICK_ACCESS_TOKEN": "token-ficticio", "GESTAOCLICK_SECRET_ACCESS_TOKEN": "segredo-ficticio",
        "GESTAOCLICK_SOURCE_ID": "fonte-ficticia", "GESTAOCLICK_STORE_ID": "loja-ficticia",
        "GESTAOCLICK_OPENAPI_JSON": json.dumps(spec), "GESTAOCLICK_FIELD_MAP_JSON": json.dumps(mapping),
        "GESTAOCLICK_STATUS_MAP_JSON": '{"concluida_ficticia":"completed"}',
        "GESTAOCLICK_PERIOD_FILTERS_JSON": '{"start":"inicio_ficticio","end":"fim_ficticio"}',
    }
    if contract == "read_contract":
        values.pop("GESTAOCLICK_OPENAPI_JSON")
        values["GESTAOCLICK_READ_CONTRACT_JSON"] = json.dumps({
            "version": 1, "base_url": BASE,
            "documentation_urls": ["https://gestaoclick.com.br/referencia-ficticia"],
            "resources": {"vendas": {"method": "GET", "filters": [item["name"] for item in parameters]}},
        })
    return values


def document():
    """Separa dados do documento e da linha sem adicionar dados pessoais necessários."""
    return {"document_id": "001", "sale_date": "2020-01-02", "status": "concluida_ficticia",
            "store_id": "loja-ficticia", "cliente_nome": "NÃO COLETAR",
            "itens_ficticios": [{"line_id": "01", "product_id": "A", "sku": "0001",
                                 "quantity": "10.5", "returned_quantity": "1.5"}]}


class TransporteFicticio:
    """Retorna resposta completa sem possuir qualquer operação de escrita."""

    def __init__(self, rows):
        """Armazena linhas de teste e chamadas para comprovar ausência de rede implícita."""
        self.rows, self.calls = rows, []

    def get(self, url, headers, timeout):
        """Produz envelope paginado completo da fonte fictícia."""
        self.calls.append(url)
        payload = {"code": 200, "status": "success", "data": self.rows, "meta": {
            "pagina_atual": 1, "total_paginas": 1, "total_registros": len(self.rows), "proxima_url": None,
        }}
        return RespostaGestaoClick(200, json.dumps(payload).encode())


@pytest.mark.parametrize("mode", ["documents", "lines"])
@pytest.mark.parametrize("contract", ["openapi", "read_contract"])
def test_reader_do_contrato_ao_resultado_canonico(mode, contract):
    """Lê GET sob demanda, com filtros confirmados e mapeamento explícito de cada campo."""
    raw = document()
    if mode == "lines":
        raw = {**raw, **raw["itens_ficticios"][0]}
    transport = TransporteFicticio([raw])
    reader = build_reader(secrets(mode=mode, contract=contract), transport)
    assert transport.calls == []
    rows = reader.read(date(2020, 1, 1), "2020-01-31")
    assert len(transport.calls) == 1
    assert "inicio_ficticio=2020-01-01" in transport.calls[0]
    assert "fim_ficticio=2020-01-31" in transport.calls[0]
    assert "loja_id=loja-ficticia" in transport.calls[0]
    assert rows[0]["sku"] == "0001"
    assert rows[0]["source_id"] == "fonte-ficticia"
    assert rows[0]["status"] == "completed"
    assert rows[0]["returned_quantity"] == 1.5
    assert "cliente_nome" not in rows[0]
    assert "token-ficticio" not in repr(reader)


@pytest.mark.parametrize("values", [
    {},
    {"GESTAOCLICK_ENABLED": False, "GESTAOCLICK_OPENAPI_JSON": "invalido",
     "GESTAOCLICK_READ_CONTRACT_JSON": None},
])
def test_reader_disabled_falha_antes_de_ler_contrato_ou_criar_transporte(values):
    """Ausência de enabled mantém o conector bloqueado mesmo sem os demais Secrets."""
    with patch("urllib.request.build_opener", side_effect=AssertionError("Rede indevida")):
        with pytest.raises(IntegracaoDesabilitada):
            build_reader(values)


@pytest.mark.parametrize("name,value", [
    ("GESTAOCLICK_OPENAPI_JSON", "{nao e json}"),
    ("GESTAOCLICK_OPENAPI_JSON", '[]'),
    ("GESTAOCLICK_FIELD_MAP_JSON", '{"mode":"lines","mode":"documents"}'),
    ("GESTAOCLICK_STATUS_MAP_JSON", '{"1":NaN}'),
    ("GESTAOCLICK_STATUS_MAP_JSON", '{"1":[]}'),
    ("GESTAOCLICK_STATUS_MAP_JSON", '{"1":"inventado"}'),
    ("GESTAOCLICK_PERIOD_FILTERS_JSON", '{"start":"nao_documentado","end":"fim_ficticio"}'),
    ("GESTAOCLICK_PERIOD_FILTERS_JSON", '{"start":"pagina","end":"limite"}'),
    ("GESTAOCLICK_PERIOD_FILTERS_JSON", '{"start":"inicio_ficticio","end":"inicio_ficticio"}'),
    ("GESTAOCLICK_FIXED_FILTERS_JSON", '{"usuario_id":"master"}'),
    ("GESTAOCLICK_FIXED_FILTERS_JSON", '{"loja_id":"outra"}'),
    ("GESTAOCLICK_FIXED_FILTERS_JSON", '{"inicio_ficticio":"2020-01-01"}'),
    ("GESTAOCLICK_FIXED_FILTERS_JSON", '{"tipo_ficticio":true}'),
])
@pytest.mark.parametrize("contract", ["openapi", "read_contract"])
def test_secrets_incompletos_ambiguos_ou_filtros_indevidos_nao_transmitem(name, value, contract):
    """Falha na montagem, antes que dados do servidor ou credenciais deixem a aplicação."""
    if name == "GESTAOCLICK_OPENAPI_JSON" and contract == "read_contract":
        name = "GESTAOCLICK_READ_CONTRACT_JSON"
    values = {**secrets(contract=contract), name: value}
    transport = TransporteFicticio([])
    with pytest.raises(ContratoInvalido):
        build_reader(values, transport)
    assert transport.calls == []


@pytest.mark.parametrize("mutation", [
    lambda fields: fields.update(cliente_nome=["nome"]),
    lambda fields: fields.pop("returned_quantity"),
    lambda fields: fields.update(sku="caminho como expressao"),
    lambda fields: fields.update(sku=[]),
])
@pytest.mark.parametrize("contract", ["openapi", "read_contract"])
def test_mapa_so_pode_projetar_colunas_permitidas_e_campos_completos(mutation, contract):
    """Não admite coleta pessoal, inferência de devoluções ou execução de expressões."""
    values = secrets(mode="lines", contract=contract)
    mapping = json.loads(values["GESTAOCLICK_FIELD_MAP_JSON"])
    mutation(mapping["fields"])
    values["GESTAOCLICK_FIELD_MAP_JSON"] = json.dumps(mapping)
    with pytest.raises(ContratoInvalido):
        build_reader(values)


@pytest.mark.parametrize("first,last", [
    ("2020-01-31", "2020-01-01"), ("20200101", "2020-01-31"),
    ("2020-01-01", "2999-01-01"), ("2000-01-01", "2020-01-01"),
    (datetime(2020, 1, 1), "2020-01-31"),
])
def test_periodos_invalidos_nao_fazem_get(first, last):
    """Recusa período invertido, futuro, excessivo e data ambígua antes da rede."""
    transport = TransporteFicticio([])
    reader = build_reader(secrets(), transport)
    with pytest.raises(ContratoInvalido):
        reader.read(first, last)
    assert transport.calls == []


@pytest.mark.parametrize("contract", ["openapi", "read_contract"])
def test_filtro_ignorado_pela_origem_nao_e_aceito_como_cobertura(contract):
    """Uma venda fora da janela torna a prévia inválida em vez de ser descartada silenciosamente."""
    transport = TransporteFicticio([{**document(), "sale_date": "2019-12-31"}])
    reader = build_reader(secrets(contract=contract), transport)
    with pytest.raises(ContratoInvalido, match="fora do período"):
        reader.read("2020-01-01", "2020-01-31")


@pytest.mark.parametrize("contract", ["openapi", "read_contract"])
def test_filtros_fixos_sao_server_only_e_nao_tem_editor_de_rotas(contract):
    """A interface recebe somente datas; recurso e filtros vêm do contrato protegido."""
    transport = TransporteFicticio([document()])
    values = {**secrets(contract=contract), "GESTAOCLICK_FIXED_FILTERS_JSON": '{"tipo_ficticio":"produto_ficticio"}'}
    reader = build_reader(values, transport)
    reader.read("2020-01-01", "2020-01-31")
    assert "tipo_ficticio=produto_ficticio" in transport.calls[0]
    with pytest.raises(TypeError):
        reader.read("2020-01-01", "2020-01-31", url="https://evil.invalid/")


@pytest.mark.parametrize("contract", ["openapi", "read_contract"])
def test_reader_nao_persiste_nem_faz_chamadas_antes_da_acao(contract):
    """Montagem da configuração e resultado vazio não disparam sincronização implícita."""
    transport = TransporteFicticio([])
    with patch("urllib.request.build_opener", side_effect=AssertionError("Rede indevida")):
        reader = build_reader(secrets(contract=contract), transport)
        assert reader.read("2020-01-01", "2020-01-31") == []


@pytest.mark.parametrize("contract", ["openapi", "read_contract"])
@pytest.mark.parametrize("name", [
    "GESTAOCLICK_FIELD_MAP_JSON", "GESTAOCLICK_STATUS_MAP_JSON", "GESTAOCLICK_PERIOD_FILTERS_JSON",
    "GESTAOCLICK_SOURCE_ID", "GESTAOCLICK_STORE_ID",
])
def test_contrato_alternativo_preserva_mapas_filtros_e_escopo_obrigatorios(contract, name):
    """Trocar o formato do contrato não preenche campos nem amplia o escopo por inferência."""
    values = secrets(contract=contract)
    values.pop(name)
    transport = TransporteFicticio([])
    with pytest.raises(ContratoInvalido):
        build_reader(values, transport)
    assert transport.calls == []


@pytest.mark.parametrize("other", ["", None, "{invalido}", "valido"])
@pytest.mark.parametrize("contract", ["openapi", "read_contract"])
def test_dois_formatos_presentes_falham_sem_escolha_silenciosa(contract, other):
    """Mesmo um segundo Secret vazio ou nulo torna a escolha ambígua e bloqueia os parsers."""
    values = secrets(contract=contract)
    other_contract = "read_contract" if contract == "openapi" else "openapi"
    key = "GESTAOCLICK_READ_CONTRACT_JSON" if other_contract == "read_contract" else "GESTAOCLICK_OPENAPI_JSON"
    values[key] = secrets(contract=other_contract)[key] if other == "valido" else other
    transport = TransporteFicticio([])
    with patch("integracoes.runtime.ContratoGestaoClick.from_openapi") as openapi:
        with patch("integracoes.runtime.ContratoGestaoClick.from_read_contract") as read_contract:
            with pytest.raises(ContratoInvalido):
                build_reader(values, transport)
            openapi.assert_not_called()
            read_contract.assert_not_called()
    assert transport.calls == []


def test_nenhum_formato_presente_nao_cria_contrato_implicito():
    """Credenciais e mapas não substituem o contrato explícito da conta."""
    values = secrets()
    values.pop("GESTAOCLICK_OPENAPI_JSON")
    transport = TransporteFicticio([])
    with pytest.raises(ContratoInvalido):
        build_reader(values, transport)
    assert transport.calls == []


@pytest.mark.parametrize("contract", ["openapi", "read_contract"])
@pytest.mark.parametrize("raw", [
    '{"conteudo-protegido":"primeiro","conteudo-protegido":"segundo"}',
    '{"aninhado":{"conteudo-protegido":1,"conteudo-protegido":2}}',
    '{"conteudo-protegido":"\ud800"}',
])
def test_json_de_contrato_ambiguo_ou_unicode_invalido_nao_vaza_nem_faz_fallback(contract, raw):
    """Chaves repetidas e Unicode inválido falham com diagnóstico fixo e sem outro formato."""
    values = secrets(contract=contract)
    key = "GESTAOCLICK_OPENAPI_JSON" if contract == "openapi" else "GESTAOCLICK_READ_CONTRACT_JSON"
    values[key] = raw
    alternative = "from_read_contract" if contract == "openapi" else "from_openapi"
    transport = TransporteFicticio([])
    with patch("integracoes.runtime.ContratoGestaoClick." + alternative) as fallback:
        with pytest.raises(ContratoInvalido) as error:
            build_reader(values, transport)
        fallback.assert_not_called()
    assert "conteudo-protegido" not in str(error.value)
    assert "token-ficticio" not in str(error.value)
    assert transport.calls == []
