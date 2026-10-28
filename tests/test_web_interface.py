"""Testes funcionais da aplicação web com serviços fictícios por sessão.

A fábrica é injetada no processo de teste, nunca por flag, URL ou segredo da
aplicação. Os testes não consultam o Supabase nem disponibilizam login alternativo.
"""
from uuid import UUID

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from dados import gerar_dados_exemplo, hoje_local
from motor import gerar_painel
from web.auth import AccessContext, Profile
from web.errors import AuthenticationError, ConfigurationError, ServiceUnavailable

SCRIPT = "from web.ui import run_app\nrun_app()\n"
USUARIO = str(UUID(int=101))
ORGANIZACAO = str(UUID(int=201))


class AuthFicticio:
    """Mantém estado de autenticação apenas na instância do teste funcional."""

    def __init__(self, servico, autenticado=True):
        """Prepara sessão fictícia, sem token, senha armazenada ou cliente remoto."""
        self.servico = servico
        self.session = object() if autenticado else None

    def login(self, email, password):
        """Simula negação ou criação de sessão sem registrar a senha recebida."""
        if self.servico.login_invalido:
            raise AuthenticationError("DETALHE_INTERNO_FICTICIO")
        self.session = object()
        return self.servico.context()

    def logout(self):
        """Encerra somente a sessão fictícia desta instância."""
        self.session = None

    def request_password_reset(self, email):
        """Recusa envio externo no cenário de teste."""
        raise ConfigurationError("Envio não configurado.")

    def recover_password(self, email, otp, password):
        """Simula código rejeitado sem criar sessão."""
        raise AuthenticationError("Código inválido.")

    def change_password(self, password):
        """Mantém teste sem operações externas de identidade."""
        self.session = None


class ServicoFicticio:
    """Entrega os mesmos cálculos do motor com um contexto explicitamente escolhido."""

    def __init__(self, permissoes, autenticado=True, nome="Pessoa fictícia", usuario=USUARIO):
        """Cria identidade, dados e contadores independentes para cada sessão."""
        self.permissoes = frozenset(permissoes)
        self.nome = nome
        self.usuario = usuario
        self.auth = AuthFicticio(self, autenticado)
        self.consultas = []
        self.login_invalido = False
        self.falhar_dados = False
        self.revogado = False
        produtos, vendas, fotos = gerar_dados_exemplo()
        self.dados = {
            "products": produtos, "sales": vendas, "snapshots": fotos,
            "panel": gerar_painel(produtos, vendas, fotos), "imports": [],
            "metadata": {"snapshot_date": hoje_local().date().isoformat(), "source": "Fictícia",
                         "period_start": "2020-01-01", "period_end": hoje_local().date().isoformat()},
        }

    def context(self):
        """Recusa sessão ausente ou revogada antes de retornar permissões."""
        if self.auth.session is None or self.revogado:
            raise AuthenticationError("DETALHE_INTERNO_FICTICIO")
        return AccessContext(Profile(self.usuario, ORGANIZACAO, self.nome, "Visualizador", True),
                             self.permissoes)

    def load_inventory(self, module, month=None):
        """Registra a permissão usada e mantém dados financeiros sentinela no fake."""
        assert self.context().can(module), "A UI pediu dados de uma funcionalidade não autorizada."
        self.consultas.append(module)
        if self.falhar_dados:
            raise ServiceUnavailable("DETALHE_INTERNO_FICTICIO")
        # O serviço real já remove custos; este fake testa também a defesa na UI.
        return self.dados

    def list_decisions(self):
        """Fornece histórico vazio sem persistência externa."""
        return []


def abrir(monkeypatch, servico=None, permissoes=("dashboard.view",), autenticado=True):
    """Injeta a fábrica no teste e executa a interface realmente renderizada."""
    import web.service

    instancias = []

    def fabrica(estado):
        """Guarda o fake no estado individual, reproduzindo o isolamento produtivo."""
        if "_servico_teste" not in estado:
            novo = servico or ServicoFicticio(permissoes, autenticado)
            estado["_servico_teste"] = novo
            instancias.append(novo)
        return estado["_servico_teste"]

    monkeypatch.setattr(web.service, "create_service", fabrica)
    app = AppTest.from_string(SCRIPT, default_timeout=30).run()
    return app, instancias


class MasterFicticio(ServicoFicticio):
    """Exercita os formulários administrativos com o contrato restritivo do banco."""

    def __init__(self):
        """Disponibiliza administração sem credenciais e registra as concessões."""
        super().__init__({"dashboard.view", "users.manage", "permissions.manage", "reports.manage"})
        self.alteracoes = []

    def context(self):
        """Retorna o papel protegido Master para esta sessão de teste."""
        contexto = super().context()
        return AccessContext(Profile(contexto.profile.user_id, ORGANIZACAO, "Master fictício", "Master", True),
                             contexto.permissions)

    def admin_list_users(self):
        """Fornece uma conta administrável com permissões herdadas do perfil."""
        return [{"user_id": str(UUID(int=102)), "display_name": "Operador fictício",
                 "role": "Operador", "active": True, "permissions": {}}]

    def admin_set_user(self, user_id, display_name, role, active, permissions):
        """Rejeita as mesmas chaves reservadas que o banco não permite delegar."""
        assert not {"users.manage", "permissions.manage", "reports.manage", "integrations.manage"} & permissions.keys()
        self.alteracoes.append((user_id, display_name, role, active, permissions))

    def reports_config(self):
        """A configuração inicial não habilita nenhum envio."""
        return {"enabled": False, "dry_run": True, "recipients": []}

    def report_runs(self):
        """Não houve trabalho externo de entrega nesta fixture."""
        return []


def test_master_edita_usuario_sem_enviar_permissoes_reservadas(monkeypatch):
    """O formulário não envia nem mesmo false para as permissões não delegáveis."""
    servico = MasterFicticio()
    app, _ = abrir(monkeypatch, servico=servico)
    app.sidebar.radio[0].set_value("Administração").run()
    assert not app.exception
    next(b for b in app.button if b.label == "Salvar acesso").click().run()
    assert not app.exception
    assert servico.alteracoes[-1][2] == "Operador"
    assert servico.alteracoes[-1][-1] == {}


def test_master_pode_definir_concessoes_individuais(monkeypatch):
    """Desmarcar herança mantém só as funcionalidades selecionadas, sem gestão Master."""
    servico = MasterFicticio()
    app, _ = abrir(monkeypatch, servico=servico)
    app.sidebar.radio[0].set_value("Administração").run()
    next(c for c in app.checkbox if c.label == "Usar permissões iniciais do perfil").set_value(False)
    next(m for m in app.multiselect if m.label == "Permissões concedidas").set_value(["inventory.view"])
    next(b for b in app.button if b.label == "Salvar acesso").click().run()
    assert not app.exception
    permissoes = servico.alteracoes[-1][-1]
    assert permissoes["inventory.view"] is True
    assert permissoes["finance.view"] is False


def test_sem_login_nao_consulta_dados_e_nao_oferece_cadastro(monkeypatch):
    """A página pública contém somente autenticação e recuperação de acesso."""
    app, instancias = abrir(monkeypatch, autenticado=False)
    assert not app.exception
    assert instancias[0].consultas == []
    assert not app.metric
    assert not app.dataframe
    assert any(botao.label == "Entrar" for botao in app.button)
    assert all("cadastrar" not in botao.label.lower() for botao in app.button)


def test_login_invalido_nao_expoe_erro_ou_dados(monkeypatch):
    """Erros de credencial não revelam resposta interna nem iniciam análise."""
    servico = ServicoFicticio(["dashboard.view"], autenticado=False)
    servico.login_invalido = True
    app, _ = abrir(monkeypatch, servico)
    next(c for c in app.text_input if c.label == "E-mail").set_value("ficticio@example.invalid")
    next(c for c in app.text_input if c.label == "Senha").set_value("senha-invalida")
    next(b for b in app.button if b.label == "Entrar").click().run()
    assert not app.exception
    assert any("Não foi possível entrar" in erro.value for erro in app.error)
    assert all("DETALHE_INTERNO" not in erro.value for erro in app.error)
    assert servico.consultas == []
    assert not app.metric


def test_configuracao_ausente_nao_exibe_demo_publica(monkeypatch):
    """Falta de Secrets não permite contornar a autenticação com dados operacionais."""
    import web.service

    def sem_configuracao(estado):
        """Simula instalação ainda sem configuração protegida."""
        raise ConfigurationError("DETALHE_INTERNO_FICTICIO")

    monkeypatch.setattr(web.service, "create_service", sem_configuracao)
    app = AppTest.from_string(SCRIPT).run()
    assert not app.exception
    assert any("Configuração pendente" in aviso.value for aviso in app.info)
    assert not app.metric
    assert not app.dataframe
    assert not app.sidebar.radio


def test_painel_exibe_kpis_do_motor_e_somente_navegacao_autorizada(monkeypatch):
    """Um Visualizador do painel não recebe controles de importação ou administração."""
    servico = ServicoFicticio(["dashboard.view"])
    app, _ = abrir(monkeypatch, servico)
    assert not app.exception
    assert app.sidebar.radio[0].options == ["Painel"]
    metricas = {m.label: m.value for m in app.metric}
    assert metricas["Produtos"] == str(len(servico.dados["panel"]))
    assert metricas["Risco antes da entrega"] == str(int(servico.dados["panel"]["risco_prazo"].sum()))
    assert "Capital conhecido em estoque" not in metricas
    assert "Produtos com sugestão" not in metricas
    assert set(servico.consultas) == {"dashboard.view"}


def test_estoque_omite_financeiro_mesmo_se_transporte_retornar_colunas(monkeypatch):
    """Além do bloqueio do banco, a interface seleciona explicitamente as colunas."""
    app, _ = abrir(monkeypatch, permissoes=["inventory.view"])
    assert not app.exception
    for tabela in app.dataframe:
        assert not {"custo_unitario", "capital_estoque", "classe_abc"}.intersection(tabela.value.columns)
    assert all("Capital" not in metrica.label for metrica in app.metric)
    assert all("Classe ABC" not in texto.value for texto in app.markdown)


def test_filtro_sem_fornecedores_exibe_vazio_sem_falhar(monkeypatch):
    """A seleção vazia não acessa uma linha inexistente para mostrar detalhes."""
    app, _ = abrir(monkeypatch, permissoes=["inventory.view"])
    next(c for c in app.multiselect if c.label == "Fornecedores").set_value([]).run()
    assert not app.exception
    assert any(tabela.value.empty for tabela in app.dataframe)
    assert any("Nenhum produto" in aviso.value for aviso in app.info)


def test_demanda_sem_dias_elegiveis_permanece_nao_estimavel(monkeypatch):
    """Ruptura contínua preserva o aviso de incerteza corrigido no PR #4."""
    servico = ServicoFicticio(["inventory.view", "replenishment.view"])
    produtos = servico.dados["products"].loc[lambda p: p["codigo"].eq("P009")]
    vendas = servico.dados["sales"].iloc[:0]
    fotos = pd.DataFrame({"codigo": ["P009"] * 90,
                          "data": pd.date_range(hoje_local() - pd.Timedelta(days=89), hoje_local()),
                          "estoque": [0] * 90})
    servico.dados.update(products=produtos, sales=vendas, snapshots=fotos,
                         panel=gerar_painel(produtos, vendas, fotos))
    app, _ = abrir(monkeypatch, servico)
    assert not app.exception
    metricas = {m.label: m.value for m in app.metric}
    assert metricas["Venda média/dia"] == "Não estimável"
    assert metricas["Sugestão (un.)"] == "Revisar dados"
    assert any("Sem dias elegíveis" in aviso.value for aviso in app.warning)


def test_falha_remota_nao_expoe_resposta_tecnica(monkeypatch):
    """A indisponibilidade não mostra tokens ou corpo de exceções no navegador."""
    servico = ServicoFicticio(["inventory.view"])
    servico.falhar_dados = True
    app, _ = abrir(monkeypatch, servico)
    assert not app.exception
    assert app.error
    assert all("DETALHE_INTERNO" not in erro.value for erro in app.error)
    assert not app.dataframe


def test_sessoes_distintas_nao_compartilham_servico(monkeypatch):
    """Duas sessões Streamlit mantêm identidades e autenticação independentes."""
    import web.service

    instancias = []

    def fabrica(estado):
        """Cria uma identidade diferente para cada estado do navegador."""
        if "_servico_teste" not in estado:
            indice = len(instancias) + 1
            estado["_servico_teste"] = ServicoFicticio(["dashboard.view"], nome=f"Pessoa {indice}",
                                                      usuario=str(UUID(int=100 + indice)))
            instancias.append(estado["_servico_teste"])
        return estado["_servico_teste"]

    monkeypatch.setattr(web.service, "create_service", fabrica)
    primeiro = AppTest.from_string(SCRIPT, default_timeout=30).run()
    segundo = AppTest.from_string(SCRIPT, default_timeout=30).run()
    assert not primeiro.exception and not segundo.exception
    assert instancias[0] is not instancias[1]
    assert instancias[0].context().profile.user_id != instancias[1].context().profile.user_id
    assert primeiro.sidebar.subheader[0].value == "Pessoa 1"
    assert segundo.sidebar.subheader[0].value == "Pessoa 2"
    instancias[0].auth.logout()
    assert instancias[0].auth.session is None
    assert instancias[1].auth.session is not None


def test_revogacao_limpa_conteudo_na_proxima_execucao(monkeypatch):
    """Uma sessão antiga não continua exibindo dados após nova autorização negar acesso."""
    servico = ServicoFicticio(["inventory.view"])
    app, _ = abrir(monkeypatch, servico)
    assert app.dataframe
    servico.revogado = True
    app.run()
    assert not app.exception
    assert not app.dataframe
    assert not app.metric
    assert any("sessão não está autorizada" in aviso.value for aviso in app.warning)


@pytest.mark.parametrize("permissoes", [[], ["reports.receive"]])
def test_conta_sem_modulos_nao_recebe_dados(monkeypatch, permissoes):
    """Conta ativa não equivale a permissão de consulta de qualquer módulo."""
    app, instancias = abrir(monkeypatch, permissoes=permissoes)
    assert not app.exception
    assert instancias[0].consultas == []
    assert any("nenhuma funcionalidade" in aviso.value for aviso in app.info)
