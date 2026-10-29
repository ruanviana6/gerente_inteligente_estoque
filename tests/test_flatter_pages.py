"""Regressões das páginas Flatter com dados fictícios e a UI Streamlit verdadeira."""
from __future__ import annotations

import base64
import copy
import re
from dataclasses import replace

from test_web_interface import ServicoFicticio, abrir

from web.errors import AuthorizationError


class ServicoRelatoriosFicticio(ServicoFicticio):
    """Conta geração e exportação para detectar trabalho anterior à ação do usuário."""

    def __init__(self, permissoes):
        """Prepara sentinelas financeiras para também exercitar a defesa da UI."""
        super().__init__(permissoes)
        self.relatorios = 0
        self.exportacoes = 0
        self.callbacks = []

    def monthly_report(self, mes):
        """Entrega relatório sintético; não usa rede, relógio externo ou dados reais."""
        self.relatorios += 1
        return {
            "_access_context": self.context(),
            "periodo": {"inicio": mes.isoformat(), "fim": mes.isoformat()},
            "resumo": {"unidades_vendidas": 30, "produtos_com_venda": 1,
                       "variacao_unidades_pct": None},
            "mais_vendidos": [{"codigo": "FT-00001", "nome": "Produto fictício", "quantidade": 30}],
            "estoque": [{"codigo": "FT-00001", "estoque": 10}],
            "reposicao": [{"codigo": "FT-00001", "quantidade_sugerida": 20}],
            "tendencias": [{"codigo": "FT-00001", "classe_abc": "A", "tendencia": "Estável"}],
            "financeiro": {"capital_estoque": 1234567, "capital_parado": 4321,
                           "custos_parciais": True, "limite_excedido": False},
            "cautelas": [], "fonte": {"origem": "Fictícia"},
        }

    def deferred_export(self, kind, params=None, context=None):
        """Cria a ação sem consultar dados; a contagem só avança ao gerar bytes."""
        assert context is not None
        self.callbacks.append((kind, copy.deepcopy(params)))

        def gerar():
            """Simula a execução de um download sem guardar conteúdo no estado."""
            self.exportacoes += 1
            return b"codigo;quantidade\nFT-00001;1\n"

        return gerar


def test_relatorio_oculta_blocos_e_custos_negados_mesmo_com_transporte_incorreto(monkeypatch):
    """O filtro visual complementa o backend sem tratar reports.view como acesso total."""
    servico = ServicoRelatoriosFicticio(["reports.view", "trends.view"])
    app, _ = abrir(monkeypatch, servico)
    app.sidebar.radio[0].set_value("Relatórios").run()
    next(b for b in app.button if b.label == "Gerar relatório").click().run()
    assert not app.exception
    colunas = {coluna for tabela in app.dataframe for coluna in tabela.value.columns}
    assert "estoque" not in colunas
    assert "quantidade_sugerida" not in colunas
    assert "classe_abc" not in colunas
    assert not any("Capital" in metrica.label for metrica in app.metric)


def test_relatorio_financeiro_parcial_mostra_limitacao(monkeypatch):
    """Custos ausentes não são apresentados como capital total conhecido."""
    servico = ServicoRelatoriosFicticio(["reports.view", "finance.view"])
    app, _ = abrir(monkeypatch, servico)
    next(b for b in app.button if b.label == "Gerar relatório").click().run()
    assert not app.exception
    assert any("Capital parcial" in aviso.value for aviso in app.warning)


def test_limpar_filtros_restaura_catalogo_visivel(monkeypatch):
    """O controle limpa busca e restrições sem perder a sessão autorizada."""
    servico = ServicoFicticio(["inventory.view"])
    app, _ = abrir(monkeypatch, servico)
    next(c for c in app.text_input if c.label == "Buscar por nome ou SKU").set_value("inexistente").run()
    assert app.dataframe[0].value.empty
    next(b for b in app.button if b.label == "Limpar filtros").click().run()
    assert not app.exception
    assert not app.dataframe[0].value.empty
    assert servico.auth.session is not None


def test_conteudo_importado_nao_e_interpolado_no_html_do_tema(monkeypatch):
    """Textos de catálogo permanecem nos widgets seguros, separados do HTML estático."""
    import streamlit as st

    sentinela = '<img src=x onerror="window.injecaoFicticia=true">'
    servico = ServicoFicticio(["inventory.view"])
    servico.dados["panel"].loc[:, "nome"] = sentinela
    servico.dados["panel"].loc[:, "fornecedor"] = sentinela
    original = st.html
    entradas = []

    def registrar(body, *args, **kwargs):
        """Observa o destino inseguro sem impedir a renderização verdadeira do tema."""
        entradas.append(str(body))
        return original(body, *args, **kwargs)

    monkeypatch.setattr(st, "html", registrar)
    app, _ = abrir(monkeypatch, servico)
    assert not app.exception
    assert entradas
    assert all(sentinela not in entrada for entrada in entradas)
    assert app.dataframe[0].value["nome"].eq(sentinela).all()


def test_csv_so_e_entregue_no_clique_e_sem_endpoint_compartilhado(monkeypatch):
    """A página entrega o arquivo como base64 isolado, sem MediaFileManager global."""
    import web.downloads

    documentos = []
    monkeypatch.setattr(web.downloads, "html", lambda documento, **kwargs: documentos.append(documento))
    servico = ServicoRelatoriosFicticio(["reports.view", "reports.export"])
    app, _ = abrir(monkeypatch, servico)
    assert not app.exception
    assert documentos == []
    assert servico.callbacks == []
    assert servico.relatorios == 0
    next(b for b in app.button if b.label == "Preparar CSV do relatório").click().run()
    assert not app.exception
    assert servico.exportacoes == 1
    assert len(documentos) == 1
    assert 'download="relatorio_mensal.csv"' in documentos[0]
    correspondencia = re.search(r'href="data:text/csv;charset=utf-8;base64,([A-Za-z0-9+/=]+)"', documentos[0])
    assert correspondencia
    assert base64.b64decode(correspondencia[1]) == b"codigo;quantidade\nFT-00001;1\n"
    assert "/media/" not in documentos[0]
    assert "<script" not in documentos[0]
    assert not app.get("download_button")
    # Outro rerun não conserva o arquivo nem produz uma segunda geração.
    app.run()
    assert not app.exception
    assert len(documentos) == 1
    assert servico.exportacoes == 1


def test_csv_negado_nao_entrega_bytes_e_limpa_pagina(monkeypatch):
    """Uma revogação na geração impede criar o link com conteúdo confidencial."""
    import web.downloads

    documentos = []
    monkeypatch.setattr(web.downloads, "html", lambda documento, **kwargs: documentos.append(documento))
    servico = ServicoRelatoriosFicticio(["reports.view", "reports.export"])

    def exportacao_negada(*args, **kwargs):
        """Simula a negação da operação sensível no instante da geração."""
        raise AuthorizationError("Permissão revogada durante a geração.")

    servico.deferred_export = exportacao_negada
    app, _ = abrir(monkeypatch, servico)
    next(b for b in app.button if b.label == "Preparar CSV do relatório").click().run()
    assert not app.exception
    assert documentos == []
    assert not app.dataframe
    assert any(b.label == "Entrar" for b in app.button)


def test_mudanca_de_acesso_durante_leitura_recusa_inclusive_metadados(monkeypatch):
    """O contexto inicial não autoriza dados obtidos depois de uma mudança concorrente."""
    servico = ServicoFicticio(["inventory.view"])

    def carregar_alterado(*args, **kwargs):
        """Retorna autorização diferente sem alterar a primeira validação da tela."""
        contexto = replace(servico.context(), permissions=frozenset({"inventory.view", "finance.view"}))
        return {**servico.dados, "_access_context": contexto,
                "metadata": {"source": "METADADO_QUE_NAO_PODE_SER_EXIBIDO"}}

    servico.load_inventory = carregar_alterado
    app, _ = abrir(monkeypatch, servico)
    assert not app.exception
    assert not app.dataframe
    assert not app.metric
    assert not any("METADADO_QUE_NAO_PODE_SER_EXIBIDO" in legenda.value for legenda in app.caption)
    assert any(b.label == "Entrar" for b in app.button)
