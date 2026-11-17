"""Prévia de estoque com transporte fictício: segurança e contrato GET por loja."""

import json
from dataclasses import asdict
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from test_gestaoclick_account_setup import (
    ACCESS_TOKEN,
    PRIVADO,
    SECRET_TOKEN,
    TransporteFicticio,
    consultar,
)
from test_gestaoclick_account_setup import auth as auth
from test_gestaoclick_account_setup import configuracao as configuracao

from integracoes.catalog_preview import consultar_previa_catalogo
from integracoes.gestaoclick import ContratoInvalido, GestaoClickError, RespostaGestaoClick
from web.errors import AuthorizationError

MARCADOR = "VALOR-COMERCIAL-SENSIVEL"


@pytest.fixture(autouse=True)
def sem_internet(monkeypatch):
    """Os testes nunca usam rede ou a conta real do usuário."""
    monkeypatch.setattr("urllib.request.build_opener", Mock(side_effect=AssertionError("Rede proibida")))


@pytest.fixture
def conta(auth, configuracao):
    return consultar(auth, configuracao)[0]


def produto(numero=100, **alteracoes):
    """Documento mínimo com variação e extras que não podem ser mostrados."""
    row = {
        "id": numero, "codigo_interno": "FL-100", "estoque": "7.500000",
        "nome_cliente": PRIVADO, "valor_total": MARCADOR, "token": SECRET_TOKEN,
        "variacoes": [{
            "id": 900, "codigo": "FL-100-A", "variacao_api_id": "13:45",
            "estoque": "2.500000", "valor_venda": MARCADOR, "observacoes": PRIVADO,
        }],
    }
    return {**row, **alteracoes}


def resposta(data=None, *, meta=None, status_http=200, **extras):
    data = [produto()] if data is None else data
    if meta is None:
        meta = {"total_registros": len(data), "pagina_atual": 1, "limite_por_pagina": 5}
    body = {"code": 200, "status": "success", "meta": meta, "data": data, **extras}
    return RespostaGestaoClick(status_http, json.dumps(body).encode())


def executar(auth, configuracao, conta, response=None, *, store_id="001", autorizado=True, depois=None):
    transport = TransporteFicticio(response if response is not None else resposta(), depois=depois)
    result = consultar_previa_catalogo(auth, lambda: configuracao, conta, store_id,
                                        autorizado=autorizado, transporte=transport)
    return result, transport


def test_conferencia_unica_da_loja_nao_importa_e_nao_exibe_financeiro(auth, configuracao, conta):
    previa, transport = executar(auth, configuracao, conta)
    assert len(transport.calls) == 1
    url, headers, timeout = transport.calls[0]
    assert urlsplit(url).scheme == "https" and urlsplit(url).netloc == "api.gestaoclick.com"
    assert urlsplit(url).path == "/produtos"
    assert parse_qs(urlsplit(url).query) == {
        "loja_id": ["001"], "pagina": ["1"], "limite": ["5"],
    }
    assert headers["access-token"] == ACCESS_TOKEN and headers["secret-access-token"] == SECRET_TOKEN
    assert timeout <= 15
    assert previa.loja_solicitada == "001"
    assert previa.recebidos == 1 and previa.total_informado == 1
    assert not previa.imported and not previa.coverage_confirmed
    item = previa.produtos[0]
    assert item.id == "100" and item.codigo_interno == "FL-100"
    assert str(item.estoque) == "7.500000"
    assert item.variacoes[0].id == "900"
    assert item.variacoes[0].codigo == "FL-100-A"
    assert item.variacoes[0].variacao_api_id == "13:45"
    for senha in (PRIVADO, MARCADOR, ACCESS_TOKEN, SECRET_TOKEN):
        assert senha not in str(asdict(previa)) and senha not in str(previa)
    auth.rpc.assert_not_called()


def test_sem_consentimento_nao_carrega_credenciais_nem_consulta(auth, conta):
    loader = Mock()
    transport = TransporteFicticio()
    with pytest.raises(AuthorizationError):
        consultar_previa_catalogo(auth, loader, conta, "001",
                                  autorizado=False, transporte=transport)
    loader.assert_not_called()
    assert not transport.calls
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("loja", ["999", "", None])
def test_loja_nao_conferida_nao_abre_secrets(auth, conta, loja):
    loader = Mock()
    transport = TransporteFicticio()
    with pytest.raises(AuthorizationError):
        consultar_previa_catalogo(auth, loader, conta, loja, autorizado=True, transporte=transport)
    loader.assert_not_called()
    assert not transport.calls


def test_amostra_limitada_nao_indica_cobertura_total(auth, configuracao, conta):
    rows = [produto(numero=100 + i, codigo_interno=f"FL-{i}") for i in range(7)]
    previa, t = executar(auth, configuracao, conta,
                        resposta(rows, meta={"total_registros": 170, "pagina_atual": 1}))
    assert len(previa.produtos) == 5 and previa.recebidos == 7
    assert previa.total_informado == 170
    assert not previa.imported and not previa.coverage_confirmed
    assert len(t.calls) == 1


def test_estoque_nao_informado_permanece_desconhecido(auth, configuracao, conta):
    row = produto(estoque=None, variacoes=[])
    previa, _ = executar(auth, configuracao, conta, resposta([row], meta={}))
    assert previa.total_informado is None
    assert previa.produtos[0].estoque is None
    assert previa.produtos[0].variacoes == ()


@pytest.mark.parametrize("campo,valor", [
    ("id", True), ("id", -1), ("codigo_interno", "<script>"),
    ("estoque", "nan"), ("estoque", float("inf")), ("estoque", "999999999999"),
    ("variacoes", "errado"), ("variacoes", [{"id": 0, "codigo": "X", "estoque": "1"}]),
    ("variacoes", [{"id": 900, "codigo": "<x>", "estoque": "1"}]),
    ("variacoes", [{"id": 900, "codigo": "A", "estoque": "1.0"},
                   {"id": 900, "codigo": "B", "estoque": "2.0"}]),
])
def test_campos_invalidos_nao_se_transformam_em_estoque(auth, configuracao, conta, campo, valor):
    with pytest.raises(ContratoInvalido) as falha:
        executar(auth, configuracao, conta, resposta([produto(**{campo: valor})]))
    for secreto in (PRIVADO, MARCADOR, ACCESS_TOKEN, SECRET_TOKEN):
        assert secreto not in str(falha.value)
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("meta", [
    {"total_registros": -1}, {"total_registros": True}, {"total_registros": "1"},
    {"total_registros": 0}, {"pagina_atual": 2}, {"pagina_atual": True},
])
def test_metadados_inconsistentes_nao_sao_exibidos(auth, configuracao, conta, meta):
    with pytest.raises(ContratoInvalido):
        executar(auth, configuracao, conta, resposta(meta=meta))


def test_produtos_duplicados_e_variações_excessivas_bloqueados(auth, configuracao, conta):
    with pytest.raises(ContratoInvalido):
        executar(auth, configuracao, conta, resposta([produto(), produto()]))
    rows = [produto(100 + i, variacoes=[{"id": i * 1000 + j + 1, "codigo": "X", "estoque": "0"}
                                         for j in range(26)]) for i in range(4)]
    with pytest.raises(ContratoInvalido):
        executar(auth, configuracao, conta, resposta(rows))


@pytest.mark.parametrize("status", [301, 302, 401, 403, 429, 500])
def test_erros_http_sao_fixos_e_nao_expoem_corpo(auth, configuracao, conta, status):
    transport = TransporteFicticio(RespostaGestaoClick(status, MARCADOR.encode()))
    with pytest.raises(GestaoClickError) as falha:
        consultar_previa_catalogo(auth, lambda: configuracao, conta, "001",
                                  autorizado=True, transporte=transport)
    assert MARCADOR not in str(falha.value)
    assert len(transport.calls) == 1


def test_revogacao_durante_http_descarta_resultado(auth, configuracao, conta):
    def revogar(_):
        auth.generation += 1
    with pytest.raises(AuthorizationError):
        executar(auth, configuracao, conta, depois=revogar)
    auth.rpc.assert_not_called()


def test_decimais_numericos_finitos_sao_apenas_exibidos(auth, configuracao, conta):
    """Não altera unidades ou arredonda saldo 2.5 vindo como número JSON."""
    row = produto(estoque=2.5, variacoes=[
        {"id": 900, "codigo": "FL-100-A", "estoque": -0.5, "variacao_api_id": ""},
    ])
    previa, transport = executar(auth, configuracao, conta, resposta([row]))
    assert str(previa.produtos[0].estoque) == "2.5"
    assert str(previa.produtos[0].variacoes[0].estoque) == "-0.5"
    assert previa.imported is False and previa.coverage_confirmed is False
    assert len(transport.calls) == 1


@pytest.mark.parametrize("campo,valor,remover,motivo,tipo", [
    ("produto.id", None, True, "CAMPO_AUSENTE", "ausente"),
    ("produto.id", None, False, "CAMPO_NULO", "nulo"),
    ("produto.id", True, False, "TIPO_INCOMPATIVEL", "booleano"),
    ("produto.id", 1.0, False, "TIPO_INCOMPATIVEL", "decimal"),
    ("produto.id", {"segredo": "NUNCA"}, False, "TIPO_INCOMPATIVEL", "objeto"),
    ("produto.id", "IDENTIFICADOR-INTERNO", False, "NAO_NUMERICO", "texto"),
    ("produto.id", "", False, "TEXTO_VAZIO", "texto"),
    ("produto.id", "1" * 31, False, "COMPRIMENTO", "texto"),
    ("produto.id", 0, False, "NAO_POSITIVO", "inteiro"),
    ("variacao.id", None, False, "CAMPO_NULO", "nulo"),
    ("variacao.id", "VARIACAO-SECRET", False, "NAO_NUMERICO", "texto"),
    ("variacao.id", -1, False, "NAO_NUMERICO", "inteiro"),
])
def test_diagnostico_de_id_restrito_nao_expoe_resposta(
    auth, configuracao, conta, campo, valor, remover, motivo, tipo,
):
    """Só localiza produto ou variação e tipo; a resposta bruta é descartada."""
    from integracoes.catalog_preview import IdentificadorCatalogoInvalido

    doc = produto(id=731, variacoes=[{
        "id": 900, "codigo": "FL-A", "estoque": "2.0",
        "variacao_api_id": "", "cliente": PRIVADO, "valor_venda": MARCADOR,
    }])
    alvo = doc if campo == "produto.id" else doc["variacoes"][0]
    if remover:
        alvo.pop("id")
    else:
        alvo["id"] = valor
    with pytest.raises(IdentificadorCatalogoInvalido) as exc:
        executar(auth, configuracao, conta, resposta([doc]))
    diagnostico = exc.value.diagnostico
    assert diagnostico == {"campo": campo, "tipo": tipo, "motivo": motivo}
    texto = str(exc.value) + json.dumps(diagnostico)
    for secreto in (PRIVADO, MARCADOR, ACCESS_TOKEN, SECRET_TOKEN, "IDENTIFICADOR-INTERNO",
                    "VARIACAO-SECRET", "NUNCA"):
        assert secreto not in texto
    assert "731" not in texto and "900" not in texto
    auth.rpc.assert_not_called()


def test_id_numerico_textual_ou_inteiro_continua_valido(auth, configuracao, conta):
    """O novo diagnóstico não flexibiliza o contrato validado."""
    previa, transport = executar(auth, configuracao, conta, resposta([
        produto(id="000731", variacoes=[
            {"id": 900, "codigo": "FL-A", "estoque": 2.5, "variacao_api_id": ""},
        ]),
    ]))
    assert previa.produtos[0].id == "000731"
    assert previa.produtos[0].variacoes[0].id == "900"
    assert not previa.imported and not previa.coverage_confirmed
    assert len(transport.calls) == 1



def test_variacao_sem_id_mantem_previa_sem_atribuir_saldo(auth, configuracao, conta):
    """Campo ausente não pode virar ID inventado nem saldo associado."""
    row = produto(variacoes=[
        {"codigo": "FL-SEM-ID", "estoque": "18.0", "variacao_api_id": "13:45"},
        {"id": 902, "codigo": "FL-COM-ID", "estoque": "2.5", "variacao_api_id": ""},
    ])
    previa, transport = executar(auth, configuracao, conta, resposta([row]))
    desconhecida, identificada = previa.produtos[0].variacoes
    assert desconhecida.id is None
    assert desconhecida.estoque is None
    assert desconhecida.codigo == "FL-SEM-ID"
    assert desconhecida.variacao_api_id == "13:45"
    assert identificada.id == "902"
    assert str(identificada.estoque) == "2.5"
    assert previa.imported is False and previa.coverage_confirmed is False
    assert len(transport.calls) == 1
    auth.rpc.assert_not_called()


def test_multiplas_variacoes_sem_id_nao_sao_unificadas(auth, configuracao, conta):
    """Sem identidade não deduplicar por código nem assumir mesmo saldo."""
    row = produto(variacoes=[
        {"codigo": "FL-A", "estoque": "1"},
        {"codigo": "FL-B", "estoque": "3"},
    ])
    previa, _ = executar(auth, configuracao, conta, resposta([row]))
    assert len(previa.produtos[0].variacoes) == 2
    assert all(v.id is None and v.estoque is None for v in previa.produtos[0].variacoes)
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("codigo_interno,tem_variacao_codigo,codigo_variacao", [
    (None, False, None),
    ("", True, ""),
    ("   ", True, None),
    ("SKU-OK", False, None),
])
def test_codigos_ausentes_ou_vazios_nao_interrompem_previa(
    auth, configuracao, conta, codigo_interno, tem_variacao_codigo, codigo_variacao,
):
    """Só códigos de exibição ficam desconhecidos: IDs e saldos não são inventados."""
    variacao = {"id": 900, "estoque": "2.5", "variacao_api_id": ""}
    if tem_variacao_codigo:
        variacao["codigo"] = codigo_variacao
    row = produto(codigo_interno=codigo_interno, variacoes=[variacao])
    if codigo_interno is None:
        row.pop("codigo_interno")
    previa, transport = executar(auth, configuracao, conta, resposta([row]))
    item = previa.produtos[0]
    assert item.id == "100"
    assert item.codigo_interno == (codigo_interno.strip() if codigo_interno and codigo_interno.strip() else None)
    assert item.variacoes[0].id == "900"
    assert item.variacoes[0].codigo is None
    assert str(item.variacoes[0].estoque) == "2.5"
    assert not previa.imported and not previa.coverage_confirmed
    assert len(transport.calls) == 1
    auth.rpc.assert_not_called()


@pytest.mark.parametrize("campo,valor,motivo,tipo", [
    ("produto.codigo_interno", True, "TIPO_INCOMPATIVEL", "booleano"),
    ("produto.codigo_interno", {"segredo": PRIVADO}, "TIPO_INCOMPATIVEL", "objeto"),
    ("produto.codigo_interno", "<script>", "CARACTERE_INSEGURO", "texto"),
    ("variacao.codigo", "C" * 101, "COMPRIMENTO", "texto"),
    ("variacao.codigo", ["NUNCA"], "TIPO_INCOMPATIVEL", "lista"),
    ("variacao.variacao_api_id", "L\\nX", "CARACTERE_INSEGURO", "texto"),
])
def test_diagnostico_codigo_invalido_nao_expoe_dados(
    auth, configuracao, conta, campo, valor, motivo, tipo,
):
    from integracoes.catalog_preview import CodigoCatalogoInvalido

    row = produto()
    if campo.startswith("produto."):
        row["codigo_interno"] = valor
    else:
        row["variacoes"][0][campo.split(".")[-1]] = valor
    with pytest.raises(CodigoCatalogoInvalido) as exc:
        executar(auth, configuracao, conta, resposta([row]))
    assert exc.value.diagnostico == {"campo": campo, "tipo": tipo, "motivo": motivo}
    texto = str(exc.value) + json.dumps(exc.value.diagnostico)
    for secreto in (PRIVADO, MARCADOR, ACCESS_TOKEN, SECRET_TOKEN, "<script>", "NUNCA"):
        assert secreto not in texto
    auth.rpc.assert_not_called()


def test_codigo_numerico_preserva_formato_e_nao_altera_erp(auth, configuracao, conta):
    row = produto(codigo_interno=123, variacoes=[{"id": 901, "codigo": 88, "estoque": 0}])
    previa, transport = executar(auth, configuracao, conta, resposta([row]))
    assert previa.produtos[0].codigo_interno == "123"
    assert previa.produtos[0].variacoes[0].codigo == "88"
    assert len(transport.calls) == 1
    auth.rpc.assert_not_called()
