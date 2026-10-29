"""Regressões da navegação Flatter usando autenticação e serviços reais.

Somente o transporte HTTP é fictício. A montagem dos indicadores percorre o
mesmo motor e as mesmas verificações do backend usadas pelo aplicativo.
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

SCRIPT = "from web.ui import run_app\nrun_app()\n"


def abrir_cenario(monkeypatch, *, size=100, role="Master", authenticated=True):
    """Isola um cenário por execução sem criar configuração de teste no produto."""
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "fixtures"))
    from flatter_data import Scenario

    import web.service

    scenario = Scenario(size=size, role=role, authenticated=authenticated)
    monkeypatch.setattr(web.service, "create_service", scenario.service_factory)
    app = AppTest.from_string(SCRIPT, default_timeout=45).run()
    assert not app.exception
    return app, scenario


def clicar(app, rotulo):
    """Aciona um botão nativo e exige que a próxima tela termine sem exceção."""
    next(botao for botao in app.button if botao.label == rotulo).click().run()
    assert not app.exception


def test_flash_reutiliza_unica_base_do_painel(monkeypatch):
    """A primeira renderização não consulta nem calcula outra base para o flash."""
    app, scenario = abrir_cenario(monkeypatch)
    assert scenario.calls["rpc_load_inventory"] == 1
    scenario.reset_counts()
    clicar(app, "Próximo insight")
    assert scenario.calls["rpc_load_inventory"] == 1
    assert any("LEITURA DO ESTOQUE" in legenda.value for legenda in app.caption)


def test_reposicao_nao_gera_exportacao_na_renderizacao(monkeypatch):
    """Montar o botão de download não executa leitura extra ou serialização CSV."""
    from web.service import Service

    def exportacao_indevida(*args, **kwargs):
        """Falha se a UI tentar preparar um CSV antes da ação do usuário."""
        raise AssertionError("CSV calculado durante a renderização.")

    monkeypatch.setattr(Service, "export_replenishment", exportacao_indevida)
    app, scenario = abrir_cenario(monkeypatch)
    scenario.reset_counts()
    app.sidebar.radio[0].set_value("Reposição").run()
    assert not app.exception
    assert scenario.calls["rpc_load_inventory"] == 1


def test_csv_preparado_so_e_entregue_a_pagina_apos_clique(monkeypatch):
    """A entrega usa o documento da sessão, sem arquivo remoto reutilizável."""
    app, scenario = abrir_cenario(monkeypatch)
    app.sidebar.radio[0].set_value("Relatórios").run()
    assert not app.exception
    assert not app.get("iframe")
    scenario.reset_counts()
    clicar(app, "Preparar CSV do relatório")
    assert scenario.calls["rpc_load_inventory"] == 2
    quadros = app.get("iframe")
    assert len(quadros) == 1
    documento = quadros[0].proto.srcdoc
    assert "data:text/csv;charset=utf-8;base64," in documento
    assert "Salvar CSV" in documento
    assert "/media/" not in documento


def test_revogacao_impede_preparar_csv_de_botao_antigo(monkeypatch):
    """Um botão já visível não contorna a autorização renovada no novo rerun."""
    from web.service import Service

    def exportacao_proibida(*args, **kwargs):
        """Reprova qualquer geração depois de retirar a concessão do cenário."""
        raise AssertionError("Exportação executada depois de revogar a permissão.")

    app, scenario = abrir_cenario(monkeypatch)
    app.sidebar.radio[0].set_value("Relatórios").run()
    assert not app.exception
    scenario.permissions.discard("reports.export")
    monkeypatch.setattr(Service, "export_monthly_report", exportacao_proibida)
    clicar(app, "Preparar CSV do relatório")
    assert not app.get("iframe")
    assert all(botao.label != "Preparar CSV do relatório" for botao in app.button)


def test_relatorio_exige_geracao_explicita(monkeypatch):
    """Visitar relatórios não consulta fechamentos; gerar carrega os dois meses."""
    app, scenario = abrir_cenario(monkeypatch)
    scenario.reset_counts()
    app.sidebar.radio[0].set_value("Relatórios").run()
    assert not app.exception
    assert scenario.calls["rpc_load_inventory"] == 0
    assert not app.metric
    clicar(app, "Gerar relatório")
    assert scenario.calls["rpc_load_inventory"] == 2
    assert any(metrica.label == "Unidades vendidas" for metrica in app.metric)


def test_catalogo_pagina_exibicao_sem_reduzir_indicadores(monkeypatch):
    """A tabela envia 50 registros inicialmente e o KPI considera o catálogo todo."""
    app, _ = abrir_cenario(monkeypatch, size=120)
    assert next(m for m in app.metric if m.label == "Produtos").value == "120"
    app.sidebar.radio[0].set_value("Estoque").run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 50
    next(c for c in app.number_input if c.label == "Página").set_value(3).run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 20


def test_graficos_produto_somente_quando_solicitados(monkeypatch):
    """A consulta da série detalhada é opcional e não roda durante o catálogo."""
    import web.pages

    chamadas = []
    monkeypatch.setattr(web.pages, "_graficos_produto", lambda dados, codigo: chamadas.append(codigo))
    app, _ = abrir_cenario(monkeypatch)
    app.sidebar.radio[0].set_value("Estoque").run()
    assert not app.exception and chamadas == []
    next(c for c in app.toggle if c.label == "Mostrar gráficos do produto").set_value(True).run()
    assert not app.exception and len(chamadas) == 1


def test_prioridade_abre_filtro_real_de_ruptura(monkeypatch):
    """A ação do painel leva ao catálogo filtrado, sem recalcular regras na UI."""
    app, _ = abrir_cenario(monkeypatch)
    clicar(app, "Conferir rupturas")
    assert app.sidebar.radio[0].value == "Estoque"
    assert not app.dataframe[0].value.empty
    assert app.dataframe[0].value["status"].eq("Ruptura").all()


def test_retorno_prioridade_substitui_filtro_anterior(monkeypatch):
    """Uma segunda ação do painel atualiza o filtro mesmo com widget já criado."""
    app, _ = abrir_cenario(monkeypatch)
    clicar(app, "Conferir rupturas")
    app.sidebar.radio[0].set_value("Painel").run()
    clicar(app, "Conferir risco no prazo")
    assert app.dataframe[0].value["risco_prazo"].all()


@pytest.mark.parametrize("perfil", ["Operador", "Visualizador"])
def test_perfis_sem_financeiro_nao_recebem_metricas_ou_admin(monkeypatch, perfil):
    """O transporte já oculta custos e a UI mantém menus e indicadores autorizados."""
    app, _ = abrir_cenario(monkeypatch, role=perfil)
    assert "Administração" not in app.sidebar.radio[0].options
    assert all("Capital" not in m.label for m in app.metric)
    assert all("R$" not in str(m.value) for m in app.metric)
    if perfil == "Visualizador":
        assert app.sidebar.radio[0].options == ["Painel"]


def test_recuperacao_nao_promete_envio_desconfigurado(monkeypatch):
    """O login não oferece recuperação quando o e-mail do Auth está desativado."""
    app, scenario = abrir_cenario(monkeypatch, authenticated=False)
    assert all("recupera" not in botao.label.lower() for botao in app.button)
    assert all("recupera" not in grupo.label.lower() for grupo in app.expander)
    assert scenario.calls["rpc_load_inventory"] == 0


def test_administracao_nao_consulta_configuracao_de_email_por_padrao(monkeypatch):
    """A edição de usuários não dispara leituras da rotina mensal e do histórico."""
    app, scenario = abrir_cenario(monkeypatch)
    scenario.reset_counts()
    app.sidebar.radio[0].set_value("Administração").run()
    assert not app.exception
    assert scenario.calls["rpc_admin_list_users"] == 1
    assert scenario.calls["rpc_report_settings"] == 0
    assert scenario.calls["rpc_report_job_history"] == 0


def test_revogacao_financeira_remove_valores_na_execucao_seguinte(monkeypatch):
    """Permissões alteradas não reutilizam o painel financeiro anterior na sessão."""
    app, scenario = abrir_cenario(monkeypatch)
    assert any("Capital" in metrica.label for metrica in app.metric)
    scenario.permissions.discard("finance.view")
    app.run()
    assert not app.exception
    assert all("Capital" not in metrica.label for metrica in app.metric)
    assert all("R$" not in str(metrica.value) for metrica in app.metric)
