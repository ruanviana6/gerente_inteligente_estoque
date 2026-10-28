"""Testes funcionais do MVP local; não representam validação de login ou RLS."""

import sqlite3
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from dados import gerar_dados_exemplo, hoje_local
from motor import gerar_painel
from storage import listar_decisoes, salvar_importacao

APP = Path(__file__).resolve().parent / "fixtures" / "mvp_app.py"


def abrir_app(tmp_path, monkeypatch):
    """Isola arquivos de cada teste; nenhuma base comercial é utilizada."""
    monkeypatch.chdir(tmp_path)
    return AppTest.from_file(str(APP), default_timeout=30).run()


def test_demonstracao_renderiza_indicadores_e_detalhe(tmp_path, monkeypatch):
    """A interface precisa executar o código e apresentar valores do mesmo motor."""
    app = abrir_app(tmp_path, monkeypatch)
    assert not app.exception
    assert len(app.tabs) == 6
    assert any("sem login" in aviso.value for aviso in app.warning)
    assert not (tmp_path / "data").exists()
    produtos, vendas, fotos = gerar_dados_exemplo()
    painel = gerar_painel(produtos, vendas, fotos)
    metricas = {metrica.label: metrica.value for metrica in app.metric}
    assert metricas["Produtos com sugestão"] == str(int(painel["quantidade_sugerida"].gt(0).sum()))
    produto = next(campo for campo in app.selectbox if campo.label == "Produto")
    produto.set_value("P009").run()
    assert not app.exception
    metricas = {metrica.label: metrica.value for metrica in app.metric}
    assert metricas["Estoque"] == "0"


def test_filtro_vazio_nao_quebra_interface(tmp_path, monkeypatch):
    """Nenhum fornecedor selecionado deve produzir uma tabela vazia válida."""
    app = abrir_app(tmp_path, monkeypatch)
    app.multiselect[0].set_value([]).run()
    assert not app.exception
    assert any(tabela.value.empty for tabela in app.dataframe)


def test_base_local_vazia_exibe_orientacao(tmp_path, monkeypatch):
    """O primeiro acesso ao modo CSV orienta a importação sem lançar exceção."""
    app = abrir_app(tmp_path, monkeypatch)
    app.sidebar.radio[0].set_value("CSV salvo localmente").run()
    assert not app.exception
    assert any("Nenhum CSV importado" in aviso.value for aviso in app.warning)


def test_csv_salvo_e_decisao_sao_persistidos(tmp_path, monkeypatch):
    """Exercita UI, motor e SQLite juntos com uma base integralmente fictícia."""
    monkeypatch.chdir(tmp_path)
    produtos, vendas, _ = gerar_dados_exemplo()
    salvar_importacao(produtos, vendas)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    app.sidebar.radio[0].set_value("CSV salvo localmente").run()
    assert not app.exception
    botao = next(botao for botao in app.button if botao.label == "Registrar decisão")
    botao.click().run()
    assert not app.exception
    decisoes = listar_decisoes()
    assert len(decisoes) == 1
    assert decisoes.iloc[0]["acao"] == "Avaliar compra"
    assert app.success


def test_banco_indisponivel_nao_expoe_excecao(tmp_path, monkeypatch):
    """A falha de infraestrutura deve gerar orientação, sem detalhes internos."""
    import storage

    def falhar():
        """Simula erro técnico sem credenciais reais."""
        raise sqlite3.OperationalError("DETALHE_INTERNO_FICTICIO")

    monkeypatch.setattr(storage, "carregar_base", falhar)
    app = abrir_app(tmp_path, monkeypatch)
    app.sidebar.radio[0].set_value("CSV salvo localmente").run()
    assert not app.exception
    assert any("Não foi possível abrir" in erro.value for erro in app.error)
    assert all("DETALHE_INTERNO" not in erro.value for erro in app.error)


def test_sem_dias_elegiveis_exibe_limite_da_estimativa(tmp_path, monkeypatch):
    """Indisponibilidade contínua não pode aparecer como demanda zero confiável."""
    import storage
    from dados import exportar_csv_seguro

    produtos, vendas, _ = gerar_dados_exemplo()
    produtos = produtos.loc[produtos["codigo"] == "P009"]
    vendas = vendas.iloc[:0]
    fotos = pd.DataFrame({
        "codigo": ["P009"] * 90,
        "data": pd.date_range(hoje_local() - pd.Timedelta(days=89), hoje_local()),
        "estoque": [0] * 90,
    })
    monkeypatch.setattr(storage, "carregar_base", lambda: (produtos, vendas, fotos, "fictício"))
    app = abrir_app(tmp_path, monkeypatch)
    app.sidebar.radio[0].set_value("CSV salvo localmente").run()
    assert not app.exception
    metricas = {metrica.label: metrica.value for metrica in app.metric}
    assert metricas["Venda média/dia"] == "Não estimável"
    assert metricas["Sugestão (un.)"] == "Revisar dados"
    assert any("Sem dias elegíveis" in aviso.value for aviso in app.warning)
    # A rota real da UI precisa aplicar a proteção de fórmulas da camada de dados.
    assert "'=1+1" in exportar_csv_seguro(pd.DataFrame({"nome": ["=1+1"]})).decode("utf-8-sig")
