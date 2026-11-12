"""Exercita a página Flatter de vendas e sua fachada com transporte fictício."""

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from uuid import UUID

import pytest
from test_web_interface import AuthFicticio, ServicoFicticio, abrir

from analytics.demo import gerar_vendas_ficticias
from web.errors import AuthorizationError, ServiceUnavailable
from web.sales_page import vendas
from web.sales_service import SalesService

SOURCE = "30000000-0000-4000-8000-000000000001"
OTHER_SOURCE = "30000000-0000-4000-8000-000000000002"
STORE = "loja-ficticia"


class AuthVendasFicticio(AuthFicticio):
    """Usa o mesmo contrato por sessão, sem HTTP, tokens ou credenciais."""

    def __init__(self, servico, autenticado=True):
        """Mantém contadores e pontos explícitos de alteração concorrente de acesso."""
        super().__init__(servico, autenticado)
        self.generation = 0
        self.calls = []
        self.before_require = None
        self.after_rpc = None

    def operation(self):
        """Preserva a interface do lock sem criar concorrência artificial no AppTest."""
        return nullcontext()

    def require(self, permission):
        """Consulta o contexto atual em cada verificação, sem herdar concessão do papel."""
        if self.before_require:
            callback, self.before_require = self.before_require, None
            callback()
        contexto = self.servico.context()
        if not contexto.can(permission):
            raise AuthorizationError("Acesso fictício negado.")
        return contexto

    def rpc(self, name, payload, permission):
        """Entrega somente o escopo solicitado e registra consultas para regressão."""
        self.require(permission)
        self.calls.append((name, deepcopy(payload), permission))
        if name == "integration_sources":
            resultado = deepcopy(self.servico.fontes)
        elif name == "sales_payload":
            resultado = {
                "source_id": payload["p_source_id"], "store_id": payload["p_store_id"],
                "metadata": deepcopy(self.servico.metadata),
                "rows": [deepcopy(linha) for linha in self.servico.linhas
                         if linha["source_id"] == payload["p_source_id"]
                         and linha["store_id"] == payload["p_store_id"]
                         and payload["p_start"] <= linha["sale_date"] <= payload["p_end"]],
            }
        else:
            raise AssertionError("RPC inesperada no teste de vendas.")
        if self.after_rpc:
            self.after_rpc(name)
        return resultado


class ServicoVendasFicticio(ServicoFicticio):
    """Preserva a aplicação verdadeira e injeta apenas a fonte analítica de teste."""

    def __init__(self, permissoes=("trends.view",), autenticado=True):
        """Cria uma fonte explicitamente fictícia e dois meses de transações."""
        super().__init__(permissoes, autenticado)
        self.auth = AuthVendasFicticio(self, autenticado)
        self.organizacao = str(UUID(int=201))
        self.role = "Operador"
        self.fontes = [{"id": SOURCE, "name": "Fonte fictícia DEMO", "active": True, "stores": [STORE],
                        "last_sync": {"finished_at": "2026-09-30T18:00:00Z", "rows": 305,
                                      "duration_seconds": 1}}]
        self.metadata = {"coverage_start": "2026-08-01", "coverage_end": "2026-09-30",
                         "coverage_verified": True}
        self.linhas = gerar_vendas_ficticias(SOURCE, STORE, date(2026, 8, 1), date(2026, 9, 30))

    def context(self):
        """Devolve o perfil protegido atual, permitindo simular troca de organização."""
        contexto = super().context()
        return replace(contexto, profile=replace(contexto.profile, organization_id=self.organizacao, role=self.role))


def abrir_vendas(monkeypatch, servico=None):
    """Abre a página através do ciclo completo de autenticação e navegação da UI."""
    import web.sales_page
    import web.ui

    monkeypatch.setattr(web.sales_page, "hoje_local", lambda: datetime(2026, 9, 30))
    monkeypatch.setattr(web.ui, "PAGES", {"Inteligência Comercial": ("trends.view", vendas)})
    return abrir(monkeypatch, servico or ServicoVendasFicticio())[0]


def analisar_pagina(app):
    """Dispara a única ação que autoriza carregar as linhas analíticas."""
    return next(botao for botao in app.button if botao.label == "Analisar vendas").click().run()


def test_vendas_sem_fontes_exibe_estado_vazio_sem_buscar_linhas(monkeypatch):
    """Sem fonte registrada, a página não inventa indicadores ou demonstração."""
    servico = ServicoVendasFicticio()
    servico.fontes = []
    app = abrir_vendas(monkeypatch, servico)
    assert not app.exception
    assert any("Nenhuma fonte sincronizada" in item.value for item in app.info)
    assert [name for name, _, _ in servico.auth.calls] == ["integration_sources"]
    assert not app.dataframe
    assert not app.metric


def test_fonte_desativada_exibe_estado_inativo(monkeypatch):
    """Uma fonte desativada não entra na seleção nem dispara análise."""
    servico = ServicoVendasFicticio()
    servico.fontes[0]["active"] = False
    app = abrir_vendas(monkeypatch, servico)
    assert not app.exception
    assert any("fonte ativa" in item.value for item in app.info)
    assert not app.selectbox


def test_sem_acesso_a_tendencias_nao_consulta_fontes(monkeypatch):
    """O ciclo de navegação não chama o módulo cuja permissão foi negada."""
    servico = ServicoVendasFicticio(permissoes=())
    app = abrir_vendas(monkeypatch, servico)
    assert not app.exception
    assert not app.selectbox
    assert not servico.auth.calls


def test_sem_login_nao_consulta_fontes(monkeypatch):
    """Uma sessão anônima permanece no formulário de autenticação."""
    servico = ServicoVendasFicticio(autenticado=False)
    app = abrir_vendas(monkeypatch, servico)
    assert not app.exception
    assert not servico.auth.calls
    assert any(botao.label == "Entrar" for botao in app.button)


def test_vendas_so_consultam_linhas_apos_acao_explicita(monkeypatch):
    """Renderizar o formulário não prepara relatório, exportação nem consulta ERP."""
    servico = ServicoVendasFicticio()
    app = abrir_vendas(monkeypatch, servico)
    assert not app.exception
    assert [name for name, _, _ in servico.auth.calls] == ["integration_sources"]
    assert not app.metric
    analisar_pagina(app)
    assert not app.exception
    assert [name for name, _, _ in servico.auth.calls].count("sales_payload") == 1
    assert len(app.metric) == 3
    metricas = {metrica.label: metrica.value for metrica in app.metric}
    assert metricas["Pedidos concluídos"] == "90"
    assert metricas["Produtos vendidos"] == "3"


def test_periodo_filtro_e_cesta_efetivos_com_dados_ficticios(monkeypatch):
    """Filtros alteram a população e mantêm o denominador explicado das cestas."""
    servico = ServicoVendasFicticio()
    app = abrir_vendas(monkeypatch, servico)
    app.date_input[0].set_value((date(2026, 9, 1), date(2026, 9, 14)))
    next(item for item in app.text_input if item.label == "Identificador do produto (opcional)").set_value("DEMO-A")
    analisar_pagina(app)
    assert not app.exception
    chamada = next(payload for name, payload, _ in servico.auth.calls if name == "sales_payload")
    assert chamada["p_start"] == "2026-08-18"
    assert chamada["p_end"] == "2026-09-14"
    produtos = next(tabela.value for tabela in app.dataframe if "sku" in tabela.value.columns)
    assert produtos["sku"].tolist() == ["DEMO-A"]
    pares = next(tabela.value for tabela in app.dataframe if "suporte" in tabela.value.columns)
    assert pares.iloc[0]["pedidos_juntos"] == 28
    assert pares.iloc[0]["suporte"] == pytest.approx(2 / 3)


def test_busca_da_tabela_nao_injeta_regex(monkeypatch):
    """A busca é literal, sem interpretar expressões recebidas do usuário."""
    app = abrir_vendas(monkeypatch)
    next(item for item in app.text_input if item.label == "Buscar produto ou SKU na tabela").set_value("[nao-regex")
    analisar_pagina(app)
    assert not app.exception
    produtos = next(tabela.value for tabela in app.dataframe if "sku" in tabela.value.columns)
    assert produtos.empty


def test_sem_linhas_no_periodo_nao_inventa_crescimento(monkeypatch):
    """Abrangência declarada e lista vazia não produzem porcentagem infinita."""
    servico = ServicoVendasFicticio()
    servico.linhas = []
    app = abrir_vendas(monkeypatch, servico)
    analisar_pagina(app)
    assert not app.exception
    assert any("Não há vendas concluídas" in item.value for item in app.info)
    assert any("indisponível" in item.value for item in app.caption)
    assert not any("inf%" in item.value for item in app.caption)


def test_revogacao_durante_payload_remove_dados_e_volta_ao_login(monkeypatch):
    """O resultado calculado não pode aparecer depois da revogação concorrente."""
    servico = ServicoVendasFicticio()
    app = abrir_vendas(monkeypatch, servico)
    def revogar(name):
        """Remove a permissão imediatamente após o backend entregar os insumos."""
        if name == "sales_payload":
            servico.permissoes = frozenset()
    servico.auth.after_rpc = revogar
    analisar_pagina(app)
    assert not app.exception
    assert not app.dataframe
    assert not app.metric
    assert any(botao.label == "Entrar" for botao in app.button)


def test_sessao_expirada_durante_payload_remove_resultados(monkeypatch):
    """A expiração durante uma operação impede apresentar dados retornados."""
    servico = ServicoVendasFicticio()
    app = abrir_vendas(monkeypatch, servico)
    def expirar(name):
        """Simula uma sessão cujo próximo controle já é recusado."""
        if name == "sales_payload":
            servico.auth.logout()
    servico.auth.after_rpc = expirar
    analisar_pagina(app)
    assert not app.exception
    assert not app.dataframe
    assert not app.metric
    assert any(botao.label == "Entrar" for botao in app.button)


@pytest.mark.parametrize("tipo", ["usuario", "organizacao", "geracao", "permissao"])
def test_fachada_recusa_mudanca_de_contexto_apos_dados(tipo):
    """Mesmo mantendo trends.view, outra conta ou vida de sessão não recebe a análise."""
    servico = ServicoVendasFicticio()
    def alterar(name):
        """Troca somente um elemento da assinatura após a leitura sensível."""
        if name != "sales_payload":
            return
        if tipo == "usuario":
            servico.usuario = str(UUID(int=999))
        elif tipo == "organizacao":
            servico.organizacao = str(UUID(int=999))
        elif tipo == "geracao":
            servico.auth.generation += 1
        else:
            servico.permissoes = frozenset({"trends.view", "finance.view"})
    servico.auth.after_rpc = alterar
    with pytest.raises(AuthorizationError):
        SalesService(servico.auth).analyze(SOURCE, STORE, date(2026, 9, 1), date(2026, 9, 30))


@pytest.mark.parametrize("tipo", ["usuario", "organizacao", "geracao", "permissao"])
def test_fontes_recusam_mudanca_de_contexto_durante_rpc(tipo):
    """Metadados da fonte também ficam vinculados à sessão e às permissões atuais."""
    servico = ServicoVendasFicticio()
    def alterar(name):
        """Altera a autorização depois da resposta da consulta de fontes."""
        if tipo == "usuario":
            servico.usuario = str(UUID(int=998))
        elif tipo == "organizacao":
            servico.organizacao = str(UUID(int=998))
        elif tipo == "geracao":
            servico.auth.generation += 1
        else:
            servico.permissoes = frozenset({"trends.view", "finance.view"})
    servico.auth.after_rpc = alterar
    with pytest.raises(AuthorizationError):
        SalesService(servico.auth).sources()


def test_fachada_nao_confia_em_abrangencia_sem_confirmacao():
    """Intervalos informados sem confirmação de cobertura não autorizam comparação."""
    servico = ServicoVendasFicticio()
    servico.metadata["coverage_verified"] = False
    resultado = SalesService(servico.auth).analyze(SOURCE, STORE, date(2026, 9, 1), date(2026, 9, 30))
    assert not resultado["comparacao"]["comparavel"]
    assert not resultado["cestas"]["disponivel"]


def test_cobertura_atual_confirmada_sem_periodo_anterior_libera_cestas():
    """Uma ausência anterior impede comparação, mas não invalida o mês confirmado."""
    servico = ServicoVendasFicticio()
    servico.metadata = {"coverage_start": "2026-09-01", "coverage_end": "2026-09-30",
                        "coverage_verified": False, "period_end_coverage_verified": True}
    resultado = SalesService(servico.auth).analyze(SOURCE, STORE, date(2026, 9, 1), date(2026, 9, 30))
    assert resultado["abrangencia"]["periodo_completo"]
    assert not resultado["comparacao"]["comparavel"]
    assert resultado["cestas"]["disponivel"]
    assert resultado["cestas"]["pares"]


@pytest.mark.parametrize("confirmacao", ["true", 1, None, [], {}])
def test_cobertura_parcial_exige_booleano_explicito(confirmacao):
    """Valores truthy não podem autorizar a cobertura apenas do período selecionado."""
    servico = ServicoVendasFicticio()
    servico.metadata = {"coverage_start": "2026-09-01", "coverage_end": "2026-09-30",
                        "coverage_verified": False, "period_end_coverage_verified": confirmacao}
    with pytest.raises(ServiceUnavailable):
        SalesService(servico.auth).analyze(SOURCE, STORE, date(2026, 9, 1), date(2026, 9, 30))


def test_granularidade_escolhida_pode_ser_analisada_sem_apagar_resultado(monkeypatch):
    """Escolher o gráfico semanal precisa resultar em uma análise visível."""
    app = abrir_vendas(monkeypatch)
    controles = [item for item in app.radio if item.label == "Evolução"]
    if controles:
        controles[0].set_value("semanal")
        analisar_pagina(app)
    else:
        analisar_pagina(app)
        next(item for item in app.radio if item.label == "Evolução").set_value("semanal").run()
    assert not app.exception
    assert len(app.metric) == 3


def test_contexto_da_pagina_tambem_e_revalidado_antes_de_metadados(monkeypatch):
    """Troca ocorrida depois da navegação não pode apresentar metadados em contexto antigo."""
    servico = ServicoVendasFicticio()
    servico.fontes[0]["last_sync"]["finished_at"] = "METADADO_NAO_AUTORIZADO"
    def trocar_identidade():
        """Muda a conta entre o contexto da navegação e a operação do módulo."""
        servico.usuario = str(UUID(int=997))
    servico.auth.before_require = trocar_identidade
    app = abrir_vendas(monkeypatch, servico)
    assert not app.exception
    assert not any("METADADO_NAO_AUTORIZADO" in item.value for item in app.caption)
    assert not app.selectbox


def test_fonte_sem_lojas_tem_estado_vazio_sem_analise_invalida(monkeypatch):
    """Configuração incompleta de fonte não gera seleção ou análise sem escopo."""
    servico = ServicoVendasFicticio()
    servico.fontes[0]["stores"] = []
    app = abrir_vendas(monkeypatch, servico)
    assert not app.exception
    assert not any(botao.label == "Analisar vendas" for botao in app.button)
    assert not any(name == "sales_payload" for name, _, _ in servico.auth.calls)


def test_novo_login_entre_contexto_da_pagina_e_fontes_remove_metadados(monkeypatch):
    """A mesma identidade em outra vida de sessão não reaproveita uma página antiga."""
    servico = ServicoVendasFicticio()
    servico.fontes[0]["last_sync"]["finished_at"] = "METADADO_DA_SESSAO_ANTIGA"
    def trocar_sessao():
        """Renova a vida da sessão depois da validação inicial da navegação."""
        servico.auth.generation += 1
    servico.auth.before_require = trocar_sessao
    app = abrir_vendas(monkeypatch, servico)
    assert not app.exception
    assert not any("METADADO_DA_SESSAO_ANTIGA" in item.value for item in app.caption)
    assert not app.selectbox


def test_periodo_ainda_sem_data_final_exibe_orientacao(monkeypatch):
    """Uma seleção incompleta não chama o backend de vendas nem gera exceção."""
    servico = ServicoVendasFicticio()
    app = abrir_vendas(monkeypatch, servico)
    app.date_input[0].set_value((date(2026, 9, 1),))
    analisar_pagina(app)
    assert not app.exception
    assert any("duas datas" in item.value for item in app.warning)
    assert not any(name == "sales_payload" for name, _, _ in servico.auth.calls)


@pytest.mark.parametrize("payload", [None, [], {}, {"rows": []},
    {"rows": [], "metadata": []}, {"rows": "resposta-invalida", "metadata": {}},
    {"rows": [], "metadata": {}, "financeiro": "campo-novo-nao-autorizado"}])
def test_resposta_malformada_da_fachada_tem_erro_seguro(payload):
    """Estrutura remota incorreta não gera traceback com corpo HTTP ou payload bruto."""
    servico = ServicoVendasFicticio()
    servico.auth.rpc = lambda *args: payload
    with pytest.raises(ServiceUnavailable) as falha:
        SalesService(servico.auth).analyze(SOURCE, STORE, date(2026, 9, 1), date(2026, 9, 30))
    assert "campo-novo-nao-autorizado" not in str(falha.value)


@pytest.mark.parametrize("coverage", ["false", "true", 1, 0, [], {}])
def test_abrangencia_nao_booleana_nao_autoriza_analise(coverage):
    """Valores truthy ou números não substituem confirmação booleana da cobertura."""
    servico = ServicoVendasFicticio()
    servico.metadata["coverage_verified"] = coverage
    with pytest.raises(ServiceUnavailable):
        SalesService(servico.auth).analyze(SOURCE, STORE, date(2026, 9, 1), date(2026, 9, 30))


@pytest.mark.parametrize("alteracao", ["source_id", "store_id", "linha"])
def test_fachada_recusa_escopo_divergente_na_resposta(alteracao):
    """O backend não pode misturar dados de outra fonte ou loja na análise."""
    servico = ServicoVendasFicticio()
    original = servico.auth.rpc
    def divergente(name, payload, permission):
        """Injeta somente no teste uma resposta que viola o escopo solicitado."""
        resultado = original(name, payload, permission)
        if alteracao == "linha":
            resultado["rows"][0]["source_id"] = OTHER_SOURCE
        elif alteracao == "source_id":
            resultado["source_id"] = OTHER_SOURCE
        else:
            resultado["store_id"] = "outra-loja"
        return resultado
    servico.auth.rpc = divergente
    with pytest.raises(AuthorizationError):
        SalesService(servico.auth).analyze(SOURCE, STORE, date(2026, 9, 1), date(2026, 9, 30))


def test_campo_financeiro_em_linha_remota_nao_chega_a_resultado():
    """A fronteira recusa um custo inesperado mesmo que o RPC tenha permissão de tendência."""
    servico = ServicoVendasFicticio()
    servico.linhas[0]["unit_cost"] = "SENTINELA_FINANCEIRA"
    # A primeira linha fica no período anterior consultado, sem depender da UI.
    servico.linhas[0]["sale_date"] = "2026-08-10"
    with pytest.raises(ServiceUnavailable) as falha:
        SalesService(servico.auth).analyze(SOURCE, STORE, date(2026, 9, 1), date(2026, 9, 30))
    assert "SENTINELA_FINANCEIRA" not in str(falha.value)


@pytest.mark.parametrize("fontes", [None, {}, [None], [{"id": SOURCE}],
    [{"id": SOURCE, "name": "Fictícia", "stores": "loja", "active": True}]])
def test_fontes_malformadas_nao_chegam_a_interface(fontes):
    """O formulário recebe somente fontes com estrutura explicitamente validada."""
    servico = ServicoVendasFicticio()
    servico.auth.rpc = lambda *args: fontes
    with pytest.raises(ServiceUnavailable):
        SalesService(servico.auth).sources()


def test_fontes_projetam_somente_metadados_conhecidos():
    """Campos futuros ou indevidos não atravessam a allowlist de apresentação."""
    servico = ServicoVendasFicticio()
    servico.fontes[0]["custo"] = "SENTINELA_PROTEGIDA"
    servico.fontes[0]["last_sync"]["token"] = "SEGREDO_FICTICIO"
    fontes = SalesService(servico.auth).sources()
    assert "SENTINELA_PROTEGIDA" not in str(fontes)
    assert "SEGREDO_FICTICIO" not in str(fontes)


def test_duas_sessoes_de_vendas_nao_compartilham_payload():
    """Instâncias diferentes não reutilizam fonte, linhas ou indicadores globais."""
    primeira, segunda = ServicoVendasFicticio(), ServicoVendasFicticio()
    segunda.usuario = str(UUID(int=909))
    segunda.organizacao = str(UUID(int=919))
    segunda.linhas = []
    a = SalesService(primeira.auth).analyze(SOURCE, STORE, date(2026, 9, 1), date(2026, 9, 30))
    b = SalesService(segunda.auth).analyze(SOURCE, STORE, date(2026, 9, 1), date(2026, 9, 30))
    assert a["resumo"]["unidades"] > 0
    assert b["resumo"]["unidades"] == 0
    assert primeira.auth.calls is not segunda.auth.calls


@pytest.mark.parametrize("inicio,fim", [(date(2026, 9, 30), date(2026, 9, 1)),
    (date(2020, 1, 1), date(2026, 9, 30)), ("2026-09-01", "2026-09-30")])
def test_periodo_invalido_da_fachada_nao_faz_rpc(inicio, fim):
    """Datas inválidas não produzem consultas amplas nem erros de tipo internos."""
    servico = ServicoVendasFicticio()
    with pytest.raises(ValueError, match="1826"):
        SalesService(servico.auth).analyze(SOURCE, STORE, inicio, fim)
    assert not servico.auth.calls


@pytest.mark.parametrize("perfil", ["Master", "Administrador", "Operador", "Visualizador"])
def test_perfil_com_concessao_explicita_acessa_vendas(monkeypatch, perfil):
    """A permissão efetiva, inclusive exceção individual, governa a nova página."""
    servico = ServicoVendasFicticio(permissoes=("trends.view",))
    servico.role = perfil
    app = abrir_vendas(monkeypatch, servico)
    analisar_pagina(app)
    assert not app.exception
    assert len(app.metric) == 3
    assert not any("Capital" in metrica.label for metrica in app.metric)


@pytest.mark.parametrize("perfil", ["Administrador", "Operador", "Visualizador"])
def test_nome_do_perfil_sem_concessao_nao_libera_fachada(perfil):
    """Nem um perfil operacional autoriza o módulo quando trends.view foi negado."""
    servico = ServicoVendasFicticio(permissoes=())
    servico.role = perfil
    with pytest.raises(AuthorizationError):
        SalesService(servico.auth).sources()
    with pytest.raises(AuthorizationError):
        SalesService(servico.auth).analyze(SOURCE, STORE, date(2026, 9, 1), date(2026, 9, 30))
    assert not servico.auth.calls


def test_nome_importado_nao_e_interpolado_no_html_flatter(monkeypatch):
    """Nomes de produtos e fontes ficam nos componentes seguros do Streamlit."""
    import streamlit as st

    sentinela = '<img src=x onerror="window.injecaoFicticia=true">'
    servico = ServicoVendasFicticio()
    servico.fontes[0]["name"] = sentinela
    for linha in servico.linhas:
        linha["product_name"] = sentinela
    documentos = []
    original = st.html
    def observar(body, *args, **kwargs):
        """Registra o HTML do tema sem trocar a renderização nativa dos dados."""
        documentos.append(str(body))
        return original(body, *args, **kwargs)
    monkeypatch.setattr(st, "html", observar)
    app = abrir_vendas(monkeypatch, servico)
    analisar_pagina(app)
    assert not app.exception
    assert documentos
    assert all(sentinela not in documento for documento in documentos)
    tabela = next(tabela.value for tabela in app.dataframe if "nome" in tabela.value.columns)
    assert tabela["nome"].eq(sentinela).all()


def test_interface_analisa_dois_anos_civis_sem_chamar_erp(monkeypatch):
    """A área funcional permite histórico suficiente para observar recorrência."""
    servico = ServicoVendasFicticio()
    referencia = servico.linhas[0]
    inicio, fim = date(2022, 1, 1), date(2025, 12, 31)
    servico.linhas = [{**referencia, "document_id": f"DEMO-{indice}",
                       "sale_date": (inicio + timedelta(days=indice)).isoformat(),
                       "quantity": 4 if (inicio + timedelta(days=indice)).month == 11 else 1}
                      for indice in range((fim - inicio).days + 1)]
    servico.metadata = {"coverage_start": "2021-12-31", "coverage_end": "2025-12-31",
                         "coverage_verified": True}
    app = abrir_vendas(monkeypatch, servico)
    app.date_input[0].set_value((date(2024, 1, 1), date(2025, 12, 31)))
    analisar_pagina(app)
    assert not app.exception
    assert len(app.metric) == 3
    recorrencias = next(tabela.value for tabela in app.dataframe if "indices_relativos" in tabela.value.columns)
    assert recorrencias["mes"].tolist() == [11]
    assert "não é previsão" in recorrencias.iloc[0]["rotulo"]
    assert set(name for name, _, _ in servico.auth.calls) == {"integration_sources", "sales_payload"}


def test_limite_da_fachada_cabe_no_periodo_maximo_da_rpc():
    """O maior período junto ao anterior cabe na janela de 3653 dias do PostgreSQL."""
    servico = ServicoVendasFicticio()
    servico.metadata["coverage_verified"] = False
    inicio = date(2020, 1, 1)
    fim = inicio + timedelta(days=1825)
    SalesService(servico.auth).analyze(SOURCE, STORE, inicio, fim)
    payload = next(payload for name, payload, _ in servico.auth.calls if name == "sales_payload")
    assert (date.fromisoformat(payload["p_end"]) - date.fromisoformat(payload["p_start"])).days == 3651
    assert (date.fromisoformat(payload["p_end"]) - date.fromisoformat(payload["p_start"])).days <= 3653
    with pytest.raises(ValueError, match="1826"):
        SalesService(servico.auth).analyze(SOURCE, STORE, inicio, fim + timedelta(days=1))
