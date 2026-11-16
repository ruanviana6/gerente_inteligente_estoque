"""Exercita descoberta e registro de fonte com API e identidade fictícias."""

import json
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from test_integration_interface import SOURCE, abrir, botao, checkbox, texto_interface

from integracoes.gestaoclick import RespostaGestaoClick
from web.auth import AccessContext

ORG = "10000000-0000-4000-8000-000000000001"
CONSENT = "Autorizo consultar as lojas e situações da minha conta"
PRIVATE = "NAO-EXIBIR-CREDENCIAL-FICTICIA"


@pytest.fixture
def conta_ficticia(monkeypatch):
    """Substitui somente Secrets e HTTP, preservando autorização e registro reais."""
    estado = SimpleNamespace(loads=0, gets=[], formato="moderno")

    def config():
        """Entrega configuração mínima que não contém fonte nem mapas de vendas."""
        estado.loads += 1
        return {"GESTAOCLICK_ENABLED": True, "GESTAOCLICK_BASE_URL": "https://api.gestaoclick.com",
                "GESTAOCLICK_ORGANIZATION_ID": ORG, "GESTAOCLICK_ACCESS_TOKEN": PRIVATE,
                "GESTAOCLICK_SECRET_ACCESS_TOKEN": PRIVATE}

    def get(self, url, headers, timeout):
        """Retorna apenas os dois recursos de metadados permitidos ao assistente."""
        estado.gets.append(url)
        path = urlsplit(url).path
        if path == "/lojas":
            payload = {"data": [{"id": "11", "nome": "Loja A fictícia", "cpf": PRIVATE},
                                {"id": "12", "nome": "Loja B fictícia"}]}
        elif path == "/situacoes_vendas":
            payload = {"data": [{"id": "91", "nome": "Situação fictícia", "privado": PRIVATE}],
                       "meta": {"total_registros": 1, "total_da_pagina": 1,
                                "pagina_atual": 1, "limite_por_pagina": 100,
                                "proxima_url": None, "proxima_pagina": None}}
            if estado.formato == "legado":
                payload["meta"]["total_registros_pagina"] = payload["meta"].pop("total_da_pagina")
                payload["meta"]["total_paginas"] = 1
            elif estado.formato == "limite20":
                payload["meta"]["limite_por_pagina"] = 20
            elif estado.formato == "invalido":
                payload["meta"]["total_da_pagina"] = PRIVATE
                payload["meta"]["credencial_indesejada"] = PRIVATE
        else:
            raise AssertionError("O assistente não pode consultar vendas ou outros recursos.")
        return RespostaGestaoClick(200, json.dumps({"code": 200, "status": "success", **payload}).encode())

    monkeypatch.setattr("web.integration_page._secrets", config)
    monkeypatch.setattr("integracoes.gestaoclick.TransporteHTTPS.get", get)
    app, auth = abrir()
    auth.context = AccessContext(replace(auth.context.profile, organization_id=ORG), auth.context.permissions)
    app.run()
    estado.app, estado.auth = app, auth
    return estado


def consultar(estado):
    """Confirma a leitura de metadados e envia o formulário visível ao usuário."""
    checkbox(estado.app, CONSENT).check()
    botao(estado.app, "Consultar lojas e situações").click().run()
    assert not estado.app.exception


def test_navegar_e_consultar_sem_consentimento_nao_le_configuracao(conta_ficticia):
    """Navegação e clique sem aceite não consultam ERP nem registram fonte."""
    e = conta_ficticia
    e.app.run()
    botao(e.app, "Consultar lojas e situações").click().run()
    assert not e.app.exception
    assert e.loads == 0 and not e.gets and not e.auth.calls
    assert any("Marque a autorização" in x.value for x in e.app.warning)


@pytest.mark.parametrize("formato", ["moderno", "legado", "limite20"])
def test_selecao_e_registro_sao_etapas_separadas_sem_importar_vendas(conta_ficticia, formato):
    """A fonte usa a loja retornada e é criada só no Supabase após ação distinta."""
    e = conta_ficticia
    e.formato = formato
    consultar(e)
    assert len(e.gets) == 2 and not e.auth.calls
    assert botao(e.app, "Registrar fonte analítica desta loja").disabled
    loja = next(x for x in e.app.selectbox if x.label == "Loja para a cópia analítica")
    loja.select("12").run()
    assert len(e.gets) == 2 and not e.auth.calls
    botao(e.app, "Registrar fonte analítica desta loja").click().run()
    assert not e.app.exception
    assert len(e.gets) == 2
    assert [nome for nome, _ in e.auth.calls] == ["integration_register_source"]
    assert e.auth.calls[0][1]["p_stores"] == ["12"]
    texto = texto_interface(e.app) + str([x.value for x in e.app.code])
    assert SOURCE in texto and "GESTAOCLICK_STORE_ID" in texto
    assert PRIVATE not in texto
    assert PRIVATE not in str([x.value.to_dict() for x in e.app.dataframe])
    assert not e.auth.consolidated and not e.auth.pages


def test_diagnostico_de_paginacao_exibe_apenas_tipos_sem_valores(conta_ficticia):
    """Formato incompatível continua bloqueado, mas deixa evidência sem dados da conta."""
    e = conta_ficticia
    e.formato = "invalido"
    consultar(e)
    assert any("SC_CONTAGEM" in x.value for x in e.app.warning)
    texto = texto_interface(e.app) + str([x.value for x in e.app.json])
    assert "total_da_pagina" in texto and "texto" in texto
    assert PRIVATE not in texto and "credencial_indesejada" not in texto
    assert not e.app.dataframe and not e.auth.calls
    assert "_gestaoclick_account_setup" not in e.app.session_state


def test_mudanca_de_sessao_remove_catalogo_antes_de_exibir(conta_ficticia):
    """Dados de metadados não reaparecem em sessão com geração diferente."""
    e = conta_ficticia
    consultar(e)
    e.auth.generation += 1
    e.app.run()
    assert not e.app.exception
    assert not e.app.dataframe
    assert not e.app.code
    assert len(e.gets) == 2 and not e.auth.calls
    assert "_gestaoclick_account_setup" not in e.app.session_state
