"""Integração da fachada web: autorização vigente, dados civis e motor auditado."""

import copy
from datetime import date

import pandas as pd
import pytest

from web.auth import AccessContext, Profile
from web.errors import AuthorizationError, ServiceUnavailable
from web.service import FINANCEIRO, Service, _dia, _registros, create_service, normalizar_payload

MES = date(2020, 3, 1)
FECHAMENTO = date(2020, 3, 31)
USUARIO = "10000000-0000-0000-0000-000000000001"
ORGANIZACAO = "20000000-0000-0000-0000-000000000001"


def test_serializacao_preserva_custos_pequenos_sem_arredondar_para_zero():
    """A serialização não altera os valores validados antes da persistência remota."""
    tabela = pd.DataFrame({"custo_unitario": [1e-12, float("nan")]})
    assert _registros(tabela) == [{"custo_unitario": 1e-12}, {"custo_unitario": None}]


def payload_ficticio():
    """Fornece um fechamento conhecido, com custo e demanda conferíveis à mão."""
    return {
        "products": [{"codigo": "001", "nome": "=1+1", "estoque": 5,
                      "fornecedor": "Fornecedor fictício", "custo_unitario": 17.25,
                      "prazo_entrega_dias": 10, "estoque_seguranca": 2,
                      "pedidos_pendentes": 0, "reservas": 0, "cobertura_alvo_dias": 30}],
        "sales": [{"codigo": "001", "data": "2020-03-31", "quantidade": 90}],
        "snapshots": [{"codigo": "001", "data": "2020-03-31", "estoque": 5}],
        "imports": [],
        "metadata": {"batch_id": "30000000-0000-0000-0000-000000000001",
                     "snapshot_date": "2020-03-31", "period_start": "2020-01-01",
                     "period_end": "2020-03-31", "month_complete": True,
                     "source": "Dados fictícios"},
    }


class AuthFalso:
    """Simula o contrato Auth sem simular a decisão de autorização do banco."""

    def __init__(self, permissions=(), role="Operador", payload=None):
        """Mantém concessões mutáveis para testar revogação entre operações."""
        self.permissions = set(permissions)
        self.role = role
        self.payload = payload or payload_ficticio()
        self.calls = []
        self.after_rpc = None

    def context(self):
        """Retorna sempre um contexto novo, tal como a consulta ao servidor."""
        return AccessContext(Profile(USUARIO, ORGANIZACAO, "Conta fictícia", self.role, True),
                             frozenset(self.permissions))

    def require(self, permission):
        """Nega antes da consulta quando a permissão não está vigente."""
        contexto = self.context()
        if not contexto.can(permission):
            raise AuthorizationError("Acesso não autorizado.")
        return contexto

    def rpc(self, name, payload, permission):
        """Registra chamadas permitidas e permite revogar acesso depois da resposta."""
        self.require(permission)
        self.calls.append((name, copy.deepcopy(payload), permission))
        resposta = copy.deepcopy(self.payload)
        if self.after_rpc is not None:
            self.after_rpc()
        return resposta


def test_payload_sem_financeiro_remove_custos_antes_do_motor_e_da_saida():
    """Mesmo uma resposta indevida do transporte não alcança o usuário sem acesso."""
    payload = payload_ficticio()
    original = copy.deepcopy(payload)
    resultado = normalizar_payload(payload, financeiro=False)
    assert not FINANCEIRO.intersection(resultado["products"].columns)
    assert not FINANCEIRO.intersection(resultado["panel"].columns)
    assert resultado["panel"].iloc[0]["venda_media_diaria"] == 3
    assert resultado["panel"].iloc[0]["quantidade_sugerida"] == 87
    assert payload == original


def test_payload_financeiro_permitido_mantem_custo_e_abc():
    """O acesso autorizado preserva capital a custo e a classificação do motor."""
    linha = normalizar_payload(payload_ficticio(), financeiro=True)["panel"].iloc[0]
    assert linha["capital_estoque"] == 86.25
    assert linha["classe_abc"] == "A"


def test_payload_vazio_nao_inventa_indicadores():
    """Base ainda não importada produz estruturas vazias, sem demanda presumida."""
    resposta = normalizar_payload({})
    assert all(resposta[chave].empty for chave in ("products", "sales", "snapshots", "panel"))
    assert not FINANCEIRO.intersection(resposta["products"].columns)


def test_snapshots_de_skus_arquivados_nao_alteram_lote_consultado():
    """Fotos de catálogo anterior não mudam demanda de produtos do lote atual."""
    payload = payload_ficticio()
    payload["snapshots"].append({"codigo": "ANTIGO", "data": "2020-03-31", "estoque": 100})
    resultado = normalizar_payload(payload)
    assert resultado["snapshots"]["codigo"].tolist() == ["001"]


@pytest.mark.parametrize("module", ["inventory.view", "dashboard.view", "reports.view"])
def test_sem_permissao_nao_consulta_dados(module):
    """A fachada não chega à operação remota sem concessão para o módulo."""
    auth = AuthFalso()
    with pytest.raises(AuthorizationError):
        Service(auth).load_inventory(module)
    assert auth.calls == []


def test_modulo_desconhecido_nao_vira_rpc_arbitraria():
    """O parâmetro de navegação não permite escolher outra operação do servidor."""
    auth = AuthFalso({"users.manage"})
    with pytest.raises(AuthorizationError):
        Service(auth).load_inventory("admin_list_users")
    assert auth.calls == []


def test_revogacao_financeira_apos_consulta_remove_valores_da_resposta():
    """Uma consulta iniciada com acesso financeiro não congela a permissão antiga."""
    auth = AuthFalso({"inventory.view", "finance.view"})
    auth.after_rpc = lambda: auth.permissions.discard("finance.view")
    resultado = Service(auth).load_inventory("inventory.view")
    assert "custo_unitario" not in resultado["products"]
    assert "capital_estoque" not in resultado["panel"]


def test_exportacao_revalida_permissao_depois_de_obter_os_dados(monkeypatch):
    """Revogação durante o cálculo impede produzir um arquivo com permissão antiga."""
    auth = AuthFalso({"replenishment.export", "replenishment.view"})
    service = Service(auth)

    def carregar_e_revogar(module):
        """Simula alteração feita pelo Master enquanto a consulta está em andamento."""
        auth.permissions.discard("replenishment.export")
        return normalizar_payload(payload_ficticio())

    monkeypatch.setattr(service, "load_inventory", carregar_e_revogar)
    with pytest.raises(AuthorizationError):
        service.export_replenishment()


def test_exportacao_remove_financeiro_revogado_e_preserva_csv_seguro(monkeypatch):
    """A revalidação final remove custos já carregados e mantém proteção de fórmula."""
    auth = AuthFalso({"replenishment.export", "replenishment.view", "finance.view"})
    service = Service(auth)

    def carregar_e_revogar(module):
        """Entrega um painel antigo para testar a defesa na saída de exportação."""
        auth.permissions.discard("finance.view")
        return normalizar_payload(payload_ficticio(), financeiro=True)

    monkeypatch.setattr(service, "load_inventory", carregar_e_revogar)
    texto = service.export_replenishment().decode("utf-8-sig")
    assert "capital_estoque" not in texto
    assert "custo_unitario" not in texto
    assert "classe_abc" not in texto
    assert "'=1+1" in texto


def test_exportacao_por_fornecedor_nao_inclui_outros_fornecedores():
    """A lista de reposição respeita o filtro enviado pela interface."""
    service = Service(AuthFalso({"replenishment.export", "replenishment.view"}))
    texto = service.export_replenishment({"fornecedor": ["Outro fornecedor"]}).decode("utf-8-sig")
    assert "Fornecedor fictício" not in texto
    assert "codigo" in texto


@pytest.mark.parametrize("valor", [0, True, 20200101, "2020-01-01T02:00:00", "2020-01-01T00:00:00Z"])
def test_datas_civis_rejeitam_numeros_horario_e_fuso(valor):
    """Inteiros não representam dias e timestamps exigem normalização explícita."""
    with pytest.raises((ValueError, TypeError)):
        _dia(valor)


@pytest.mark.parametrize("inicio,fim,foto", [
    ("2020-04-01", "2020-03-31", "2020-03-31"),
    ("2020-03-01", "2020-04-01", "2020-03-31"),
    ("2020-03-01", "2020-03-30", "2020-03-31"),
])
def test_importacao_rejeita_abrangencia_invalida_antes_do_banco(inicio, fim, foto):
    """Períodos invertidos ou venda fora da abrangência não geram lote remoto."""
    auth = AuthFalso({"inventory.import"})
    with pytest.raises(ValueError):
        Service(auth).import_csv(b"codigo;nome;estoque\n001;Ficticio;5\n",
                                 b"data;codigo;quantidade\n2020-03-31;001;90\n",
                                 foto, inicio, fim)
    assert auth.calls == []


def test_importacao_sem_acesso_financeiro_recusa_custos():
    """Concessão de importação não concede permissão implícita sobre custos."""
    auth = AuthFalso({"inventory.import"})
    with pytest.raises(AuthorizationError, match="financeira"):
        Service(auth).import_csv(b"codigo;nome;estoque;custo_unitario\n001;Ficticio;5;17,25\n",
                                 b"data;codigo;quantidade\n2020-03-31;001;90\n",
                                 FECHAMENTO, MES, FECHAMENTO)
    assert auth.calls == []


def test_importacao_preserva_identidade_e_envia_datas_civis():
    """A integração mantém as correções do PR #4 antes da RPC transacional."""
    auth = AuthFalso({"inventory.import"})
    Service(auth).import_csv(b"codigo;nome;estoque\n001;Ficticio;5\n",
                             b"data;codigo;quantidade\n31/03/2020;001;90\n",
                             FECHAMENTO, MES, FECHAMENTO)
    nome, payload, permissao = auth.calls[-1]
    assert nome == "import_inventory"
    assert permissao == "inventory.import"
    assert payload["p_products"][0]["codigo"] == "001"
    assert payload["p_products"][0]["custo_unitario"] is None
    assert payload["p_sales"][0]["data"] == "2020-03-31"


def test_demonstracao_exige_master_mesmo_com_permissao_de_importar():
    """Um Operador não substitui a base por uma demonstração através da fachada."""
    auth = AuthFalso({"inventory.import"}, role="Operador")
    with pytest.raises(AuthorizationError, match="Master"):
        Service(auth).import_demo(FECHAMENTO)
    assert auth.calls == []


def test_relatorio_mensal_consulta_lote_historico_e_nao_saldo_atual():
    """O mês selecionado vai à RPC e o motor recebe o saldo daquele fechamento."""
    auth = AuthFalso({"reports.view", "inventory.view"})
    relatorio = Service(auth).monthly_report(MES)
    assert auth.calls[0][1]["p_month"] == "2020-03-01"
    assert auth.calls[1][1]["p_month"] == "2020-02-01"
    assert relatorio["resumo"]["unidades_vendidas"] == 90
    assert relatorio["estoque"][0]["estoque"] == 5
    assert "financeiro" not in relatorio


def test_falta_de_mes_anterior_nao_inventa_comparacao():
    """Uma falha de leitura do mês anterior mantém a comparação indisponível."""
    auth = AuthFalso({"reports.view"})
    original = auth.rpc

    def consultar(name, payload, permission):
        """Simula mês atual disponível e ausência do período anterior."""
        if payload.get("p_month") == "2020-02-01":
            raise ServiceUnavailable("Período indisponível.")
        return original(name, payload, permission)

    auth.rpc = consultar
    relatorio = Service(auth).monthly_report(MES)
    assert relatorio["resumo"]["variacao_unidades_pct"] is None


def test_relatorio_incompleto_e_rejeitado_pela_fachada():
    """A seleção do mês não transforma uma importação parcial em fechamento."""
    payload = payload_ficticio()
    payload["metadata"]["month_complete"] = False
    with pytest.raises(ValueError, match="fechamento"):
        Service(AuthFalso({"reports.view"}, payload=payload)).monthly_report(MES)


def test_duas_sessoes_nao_compartilham_cliente_nem_identidade():
    """A fábrica reutiliza apenas o estado do mesmo navegador, nunca um global."""
    configuracao = {"SUPABASE_URL": "https://csiqpzoudruzppfbpqqx.supabase.co",
                   "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_exemplo_ficticio_nao_utilizavel"}
    primeira, segunda = {}, {}
    service_a = create_service(primeira, configuracao)
    service_b = create_service(segunda, configuracao)
    assert service_a is create_service(primeira, configuracao)
    assert service_a is not service_b
    assert service_a.auth is not service_b.auth
    assert service_a.auth.api is not service_b.auth.api
    assert service_a.auth.session is None
    assert service_b.auth.session is None
