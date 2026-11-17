"""Amostra do catálogo no Streamlit com conta Master e transporte fictícios."""

import json
from urllib.parse import urlsplit

from test_integration_interface import botao, checkbox, texto_interface
from test_integration_native_preview_interface import (
    previa_ficticia as previa_ficticia,
)
from test_integration_native_preview_interface import (
    selecionar_loja,
)

from integracoes.gestaoclick import RespostaGestaoClick

SENSIVEL = "SEGREDO-FICTICIO-NUNCA-RENDERIZAR"


def configurar_catalogo(monkeypatch):
    """Responde só ao GET catálogo; preserva os demais endpoints de testes."""
    from integracoes.gestaoclick import TransporteHTTPS

    original = TransporteHTTPS.get

    def get(self, url, headers, timeout):
        if urlsplit(url).path != "/produtos":
            return original(self, url, headers, timeout)
        payload = {
            "code": 200, "status": "success",
            "meta": {"total_registros": 22, "pagina_atual": 1, "limite_por_pagina": 5},
            "data": [{
                "id": 7001, "codigo_interno": "FL-PROD-001",
                "estoque": "8.00", "cliente": SENSIVEL, "valor_venda": SENSIVEL,
                "variacoes": [{"id": 9001, "codigo": "FL-001-A",
                               "variacao_api_id": "10:20", "estoque": "3.00",
                               "valor_custo": SENSIVEL}],
            }],
        }
        return RespostaGestaoClick(200, json.dumps(payload).encode())

    monkeypatch.setattr("integracoes.gestaoclick.TransporteHTTPS.get", get)


def test_navegacao_sem_consentimento_nao_consulta_produtos(previa_ficticia, monkeypatch):
    e = previa_ficticia
    configurar_catalogo(monkeypatch)
    selecionar_loja(e)
    anteriores = list(e.gets)
    botao(e.app, "Conferir catálogo e estoque").click().run()
    assert not e.app.exception
    assert e.gets == anteriores
    assert any("Autorize a leitura do catálogo" in str(x.value) for x in e.app.warning)
    assert "FL-PROD-001" not in texto_interface(e.app)
    assert not e.auth.calls


def test_catalogo_e_estoque_da_loja_nao_alteram_indicadores(previa_ficticia, monkeypatch):
    e = previa_ficticia
    configurar_catalogo(monkeypatch)
    selecionar_loja(e)
    checkbox(e.app, "Autorizo consultar uma amostra real do catálogo desta loja").check()
    botao(e.app, "Conferir catálogo e estoque").click().run()
    assert not e.app.exception
    assert any("Catálogo consultado" in str(x.value) for x in e.app.success)
    tabelas = [str(x.value.to_dict()) for x in e.app.dataframe]
    assert any("FL-PROD-001" in x and "FL-001-A" in x for x in tabelas)
    conteudo = texto_interface(e.app) + str(tabelas)
    assert "Nenhum saldo foi importado" in conteudo
    assert "IDs das variações" in conteudo
    assert SENSIVEL not in conteudo
    assert not e.auth.calls and not e.auth.consolidated
    assert "FL-PROD-001" not in str(e.app.session_state.to_dict())
    e.app.run()
    assert not e.app.exception
    assert "FL-PROD-001" not in str([x.value for x in e.app.dataframe])
