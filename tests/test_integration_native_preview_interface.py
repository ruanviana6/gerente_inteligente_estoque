"""Valida a amostra nativa na interface com transporte e identidade fictícios."""

import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from test_integration_interface import abrir, botao, checkbox, texto_interface

from dados import hoje_local
from integracoes.gestaoclick import RespostaGestaoClick
from integracoes.native_preview import PreviaVendasNativas
from web.auth import AccessContext

ORG = "10000000-0000-4000-8000-000000000001"
CONSENTIMENTO_CONTA = "Autorizo consultar as lojas e situações da minha conta"
CONSENTIMENTO_AMOSTRA = "Autorizo consultar uma amostra real de vendas desta loja"
PRIVADO = "SEGREDO-FICTICIO-NUNCA-RENDERIZAR"
PRODUTO = "900000000123"


@pytest.fixture
def previa_ficticia(monkeypatch):
    """Preserva o leitor e a autorização reais; substitui somente configuração e HTTP."""
    estado = SimpleNamespace(loads=0, gets=[], status=200, revogar=False, meta_invalida=False,
                             unidade="__ausente__", quantidade="2.5", codigo="1001")
    hoje = hoje_local().date()

    def config():
        """A conferência usa só a configuração mínima da conta, sem mapas ou fonte."""
        estado.loads += 1
        return {"GESTAOCLICK_ENABLED": True, "GESTAOCLICK_BASE_URL": "https://api.gestaoclick.com",
                "GESTAOCLICK_ORGANIZATION_ID": ORG, "GESTAOCLICK_ACCESS_TOKEN": PRIVADO,
                "GESTAOCLICK_SECRET_ACCESS_TOKEN": PRIVADO}

    def get(self, url, headers, timeout):
        """Entrega envelopes locais e nunca permite outro recurso além da conta e da amostra."""
        estado.gets.append(url)
        path = urlsplit(url).path
        if path == "/lojas":
            payload = {"data": [{"id": "11", "nome": "Loja A fictícia", "cpf": PRIVADO},
                                {"id": "12", "nome": "Loja B fictícia"}]}
        elif path == "/situacoes_vendas":
            payload = {"data": [{"id": "91", "nome": "Situação fictícia", "privado": PRIVADO}],
                       "meta": {"total_registros": 1, "total_da_pagina": 1,
                                "pagina_atual": 1, "limite_por_pagina": 100,
                                "proxima_url": None, "proxima_pagina": None}}
        elif path == "/vendas":
            if estado.revogar:
                estado.auth.generation += 1
            if estado.status != 200:
                return RespostaGestaoClick(estado.status, json.dumps({"erro": PRIVADO}).encode())
            payload = {"data": [{"id": "VENDA-FICTICIA-001", "codigo": estado.codigo, "data": hoje.isoformat(),
                                "situacao_id": "91", "nome_situacao": "Situação fictícia",
                                "cliente_id": PRIVADO, "nome_cliente": PRIVADO,
                                "valor_total": PRIVADO, "observacoes": PRIVADO,
                                "produtos": [{"produto": {"produto_id": PRODUTO, "variacao_id": "700001",
                                                          "quantidade": estado.quantidade, "nome_produto": PRIVADO,
                                                          "valor_custo": PRIVADO, "valor_total": PRIVADO}}]}],
                       "meta": {"total_registros": 1, "total_da_pagina": 1,
                                "pagina_atual": 1, "limite_por_pagina": 5,
                                "proxima_url": None, "proxima_pagina": None}}
            if estado.unidade != "__ausente__":
                payload["data"][0]["produtos"][0]["produto"]["sigla_unidade"] = estado.unidade
            if estado.meta_invalida:
                payload["meta"]["total_da_pagina"] = PRIVADO
        else:
            raise AssertionError("Recurso fora da conferência autorizada.")
        return RespostaGestaoClick(200, json.dumps({"code": 200, "status": "success", **payload}).encode())

    monkeypatch.setattr("web.integration_page._secrets", config)
    monkeypatch.setattr("integracoes.gestaoclick.TransporteHTTPS.get", get)
    app, auth = abrir()
    auth.context = AccessContext(replace(auth.context.profile, organization_id=ORG), auth.context.permissions)
    app.run()
    estado.app, estado.auth = app, auth
    return estado


def selecionar_loja(estado):
    """Consulta os catálogos e escolhe uma loja como o Master faria na página."""
    checkbox(estado.app, CONSENTIMENTO_CONTA).check()
    botao(estado.app, "Consultar lojas e situações").click().run()
    loja = next(item for item in estado.app.selectbox if item.label == "Loja para a cópia analítica")
    loja.select("11").run()
    assert not estado.app.exception


def consultar_amostra(estado):
    """Marca a confirmação específica e solicita a prévia sem registrar fonte."""
    checkbox(estado.app, CONSENTIMENTO_AMOSTRA).check()
    botao(estado.app, "Conferir amostra de vendas").click().run()
    assert not estado.app.exception


def tabelas(app):
    """Reúne somente as tabelas visíveis, incluindo o catálogo e uma eventual amostra."""
    return str([item.value.to_dict() for item in app.dataframe])


def test_navegacao_e_envio_sem_aceite_nao_consultam_amostra(previa_ficticia):
    """Selecionar a loja ou abrir o formulário não acessa Secrets nem consulta vendas."""
    e = previa_ficticia
    assert e.loads == 0 and not e.gets
    selecionar_loja(e)
    anteriores = (e.loads, list(e.gets))
    e.app.run()
    botao(e.app, "Conferir amostra de vendas").click().run()
    assert not e.app.exception
    assert (e.loads, e.gets) == anteriores
    assert any("Marque a autorização" in item.value for item in e.app.warning)
    assert PRODUTO not in tabelas(e.app)
    assert not e.auth.calls


def test_amostra_exibe_projecao_sem_mapas_pii_financeiro_ou_persistencia(previa_ficticia):
    """O fluxo real da UI usa um GET protegido e não promove a amostra aos indicadores."""
    e = previa_ficticia
    selecionar_loja(e)
    consultar_amostra(e)
    assert len(e.gets) == 3
    url = urlsplit(e.gets[-1])
    assert url.path == "/vendas"
    params = parse_qs(url.query)
    assert params["pagina"] == ["1"] and params["limite"] == ["5"]
    assert params["loja_id"] == ["11"] and params["tipo"] == ["produto"]
    assert {"data_inicio", "data_fim"} <= params.keys()
    assert PRODUTO in tabelas(e.app) and "2.5" in tabelas(e.app)
    texto = texto_interface(e.app) + tabelas(e.app)
    assert PRIVADO not in texto
    assert "Amostra parcial" in texto and "não confirma a loja efetiva" in texto
    assert "não foram comprovados" in texto and "Nenhuma venda foi importada" in texto
    assert not e.auth.calls and not e.auth.pages and not e.auth.consolidated
    assert "_sync_preview" not in e.app.session_state
    estado_sessao = e.app.session_state.to_dict()
    assert PRODUTO not in str(estado_sessao)
    assert not any(isinstance(value, PreviaVendasNativas) for value in estado_sessao.values())


def test_amostra_mostra_duas_caixas_e_numero_comercial_sem_conversao(previa_ficticia):
    """A conferência mostra o número da venda e a medida recebida, sem assumir unidades."""
    e = previa_ficticia
    e.unidade, e.quantidade, e.codigo = "CX", "2", "2025/001"
    selecionar_loja(e)
    consultar_amostra(e)
    tabela = next(item.value for item in e.app.dataframe if "Documento (ID)" in item.value.columns)
    assert tabela["Unidade"].tolist() == ["CX"]
    assert tabela["Quantidade"].tolist() == ["2"]
    assert tabela["Número"].tolist() == ["2025/001"]
    assert len(e.gets) == 3 and not e.auth.calls
    assert PRIVADO not in texto_interface(e.app) + tabelas(e.app)


@pytest.mark.parametrize("unidade", ["__ausente__", None])
def test_amostra_exibe_unidade_desconhecida_sem_adotar_padrao(previa_ficticia, unidade):
    """Quando a API não informa medida, a tabela não chama a quantidade de UN ou CX."""
    e = previa_ficticia
    e.unidade = unidade
    selecionar_loja(e)
    consultar_amostra(e)
    tabela = next(item.value for item in e.app.dataframe if "Documento (ID)" in item.value.columns)
    assert tabela["Unidade"].tolist() == ["Não informada"]
    assert tabela["Quantidade"].tolist() == ["2.5"]
    assert len(e.gets) == 3 and not e.auth.calls


def test_unidade_invalida_nao_aparece_na_tabela_ou_aviso(previa_ficticia):
    """Um campo de medida incompatível recusa a amostra inteira sem refletir seu valor."""
    e = previa_ficticia
    e.unidade = PRIVADO
    selecionar_loja(e)
    consultar_amostra(e)
    assert e.app.warning
    assert PRODUTO not in tabelas(e.app)
    assert PRIVADO not in texto_interface(e.app) + tabelas(e.app)
    assert len(e.gets) == 3 and not e.auth.calls


def test_amostra_nao_reaparece_nem_consulta_novamente_no_rerun(previa_ficticia):
    """O conteúdo comercial existe somente no retorno daquele clique autorizado."""
    e = previa_ficticia
    selecionar_loja(e)
    consultar_amostra(e)
    anteriores = (e.loads, list(e.gets))
    assert PRODUTO in tabelas(e.app)
    e.app.run()
    assert not e.app.exception
    assert PRODUTO not in tabelas(e.app)
    assert (e.loads, e.gets) == anteriores
    assert not e.auth.calls


def test_tipo_pdv_e_escopo_da_loja_sao_explicitos(previa_ficticia):
    """A seleção de balcão é enviada de modo separado, sem combinar universos de vendas."""
    e = previa_ficticia
    selecionar_loja(e)
    next(item for item in e.app.selectbox if item.label == "Tipo de venda da amostra").select("vendas_balcao")
    consultar_amostra(e)
    assert parse_qs(urlsplit(e.gets[-1]).query)["tipo"] == ["vendas_balcao"]
    assert "PDV / vendas de balcão" in texto_interface(e.app)
    assert len(e.gets) == 3 and not e.auth.calls


@pytest.mark.parametrize("durante_http", [False, True])
def test_sessao_alterada_impede_exibir_amostra(previa_ficticia, durante_http):
    """A autorização é conferida antes de consultar e depois de receber os registros."""
    e = previa_ficticia
    selecionar_loja(e)
    e.revogar = durante_http
    if not durante_http:
        e.auth.generation += 1
    consultar_amostra(e)
    assert e.app.error
    assert PRODUTO not in tabelas(e.app)
    assert len(e.gets) == (3 if durante_http else 2)
    assert not e.auth.calls and not e.auth.consolidated


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_falha_http_exibe_diagnostico_sem_corpo_ou_credenciais(previa_ficticia, status):
    """O erro devolvido pelo ERP não é reproduzido na interface nem nos dados locais."""
    e = previa_ficticia
    selecionar_loja(e)
    e.status = status
    consultar_amostra(e)
    assert e.app.warning
    assert PRODUTO not in tabelas(e.app)
    assert PRIVADO not in texto_interface(e.app) + tabelas(e.app)
    assert len(e.gets) == 3 and not e.auth.calls


def test_metadado_invalido_nao_exibe_valor_da_resposta(previa_ficticia):
    """Dados incompatíveis são recusados sem expor o conteúdo usado no campo inválido."""
    e = previa_ficticia
    selecionar_loja(e)
    e.meta_invalida = True
    consultar_amostra(e)
    assert e.app.warning
    assert PRODUTO not in tabelas(e.app)
    assert PRIVADO not in texto_interface(e.app) + tabelas(e.app)
    assert not e.auth.calls


@pytest.mark.parametrize("dias", [None, 32])
def test_periodo_incompleto_ou_longo_nao_acessa_secrets(previa_ficticia, dias):
    """A amostra pequena não permite usar o formulário como carga histórica disfarçada."""
    e = previa_ficticia
    selecionar_loja(e)
    hoje = hoje_local().date()
    datas = (hoje,) if dias is None else (hoje - timedelta(days=dias - 1), hoje)
    next(item for item in e.app.date_input if item.label == "Período da amostra de vendas").set_value(datas)
    anteriores = (e.loads, list(e.gets))
    consultar_amostra(e)
    assert e.app.warning
    assert (e.loads, e.gets) == anteriores
    assert PRODUTO not in tabelas(e.app)
    assert not e.auth.calls


def test_diagnostico_da_amostra_rejeitada_expoe_apenas_tipos(previa_ficticia):
    """Uma inconsistência real de paginação deve ser identificável sem valores."""
    e = previa_ficticia
    selecionar_loja(e)
    e.meta_invalida = True
    consultar_amostra(e)
    assert not e.app.exception
    assert e.app.warning and "PV_CONTAGEM" in str(e.app.warning[0].value)
    diagnosticos = [item.value for item in e.app.json]
    assert any("PV_CONTAGEM" in str(d) for d in diagnosticos)
    conteudo = texto_interface(e.app) + str(diagnosticos) + tabelas(e.app)
    assert PRIVADO not in conteudo and PRODUTO not in tabelas(e.app)
    assert len(e.gets) == 3 and not e.auth.calls
