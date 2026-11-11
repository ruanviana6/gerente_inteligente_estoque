"""Contratos de exportação por módulo, revogação e isolamento de identidade."""

import csv
import importlib.util
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from dados import exportar_csv_seguro
from web.errors import AuthenticationError, AuthorizationError
from web.exports import COLUNAS_REPOSICAO, filtrar_reposicao
from web.reports import exportar_relatorio_csv, filtrar_relatorio

_spec = importlib.util.spec_from_file_location(
    "export_scope_fixture", Path(__file__).parent / "fixtures/flatter_data.py",
)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
Scenario = _fixture.Scenario


def _csv(conteudo):
    """Lê o CSV produzido pelo mesmo serviço utilizado no aplicativo."""
    return csv.DictReader(StringIO(conteudo.decode("utf-8-sig")), delimiter=";")


@pytest.mark.parametrize("role", ["Master", "Administrador", "Operador", "Visualizador"])
def test_perfis_respeitam_exportacao_independente(role):
    """O papel sozinho não libera exportação ausente no perfil ou na exceção."""
    scenario = Scenario(size=3, role=role)
    service = scenario.service_factory({})
    if role in {"Operador", "Visualizador"}:
        with pytest.raises(AuthorizationError):
            service.export_replenishment()
        assert not scenario.calls["rpc_load_inventory"]
    else:
        leitor = _csv(service.export_replenishment({"somente_sugestao": False}))
        assert {"custo_unitario", "media_longa", "classe_abc"}.issubset(leitor.fieldnames)


@pytest.mark.parametrize("role", ["Master", "Administrador", "Operador", "Visualizador"])
@pytest.mark.parametrize("financeiro", [False, True])
@pytest.mark.parametrize("tendencias", [False, True])
def test_excecoes_individuais_controlam_colunas_sem_liberacao_por_papel(role, financeiro, tendencias):
    """Tendências exigem concessão própria e ABC exige tendências e finanças."""
    scenario = Scenario(size=3, role=role)
    scenario.permissions = {"replenishment.view", "replenishment.export"}
    if financeiro:
        scenario.permissions.add("finance.view")
    if tendencias:
        scenario.permissions.add("trends.view")
    leitor = _csv(scenario.service_factory({}).export_replenishment({"somente_sugestao": False}))
    assert ("media_longa" in leitor.fieldnames) is tendencias
    assert ("tendencia" in leitor.fieldnames) is tendencias
    assert ("custo_unitario" in leitor.fieldnames) is financeiro
    assert ("capital_estoque" in leitor.fieldnames) is financeiro
    assert ("classe_abc" in leitor.fieldnames) is (financeiro and tendencias)
    assert ("abc_parcial" in leitor.fieldnames) is (financeiro and tendencias)
    linhas = list(leitor)
    for linha in linhas:
        if not financeiro:
            assert "custo" not in linha["aviso_qualidade"].lower()
            assert "capital" not in linha["aviso_qualidade"].lower()
        if not (financeiro and tendencias):
            assert "abc" not in linha["aviso_qualidade"].lower()


def test_colunas_novas_sao_negadas_por_padrao_e_vazio_mantem_cabecalho():
    """O motor não pode conceder acesso apenas adicionando uma coluna ao painel."""
    permissoes = {"replenishment.view", "replenishment.export"}
    tabela = pd.DataFrame([{"codigo": "FICTICIO", "nome": "Produto", "estoque": 0,
                            "indicador_secreto_futuro": 987654, "aviso_qualidade": "ABC SECRETA"}])
    segura = filtrar_reposicao(tabela, permissoes)
    assert "indicador_secreto_futuro" not in segura
    assert "ABC SECRETA" not in str(segura.to_dict())
    vazio = filtrar_reposicao(pd.DataFrame(), permissoes)
    assert tuple(vazio.columns) == COLUNAS_REPOSICAO
    assert _csv(exportar_csv_seguro(vazio)).fieldnames == list(COLUNAS_REPOSICAO)


def test_filtro_qualidade_nao_revela_flag_financeira_revogada():
    """Uma flag de overflow financeiro não afeta seleção de usuário sem finanças."""
    tabela = pd.DataFrame([{"codigo": "FICTICIO", "demanda_estimavel": True,
                            "financeiro_limite_excedido": True, "requer_revisao": True}])
    segura = filtrar_reposicao(tabela, {"replenishment.view", "replenishment.export"})
    assert not segura.iloc[0]["requer_revisao"]
    assert segura.iloc[0]["aviso_qualidade"] == ""


@pytest.mark.parametrize("permissao", ["finance.view", "trends.view"])
def test_revogacao_apos_consulta_remove_colunas_antes_de_serializar(permissao):
    """A geração usa a concessão final, mesmo que o lote veio com mais acesso."""
    scenario = Scenario(size=3)
    service = scenario.service_factory({})
    original = service.load_inventory

    def carregar_e_revogar(module):
        """Revoga somente após devolver o painel calculado com acesso inicial."""
        resultado = original(module)
        scenario.permissions.discard(permissao)
        return resultado

    with patch.object(service, "load_inventory", side_effect=carregar_e_revogar):
        leitor = _csv(service.export_replenishment({"somente_sugestao": False}))
    assert "classe_abc" not in leitor.fieldnames
    assert ("custo_unitario" in leitor.fieldnames) is (permissao != "finance.view")
    assert ("media_longa" in leitor.fieldnames) is (permissao != "trends.view")


@pytest.mark.parametrize("kind", ["replenishment", "monthly"])
@pytest.mark.parametrize("mudanca", ["finance.view", "trends.view", "usuario", "organizacao", "sessao", "expirada"])
def test_mudanca_durante_serializacao_recusa_entrega_direta(kind, mudanca):
    """Nem métodos diretos devolvem bytes após mudar o limite ou vida da sessão."""
    scenario = Scenario(size=3)
    service = scenario.service_factory({})
    alvo = "web.service.exportar_csv_seguro" if kind == "replenishment" else "web.reports.exportar_csv_seguro"

    def serializar_e_mudar(tabela):
        """Prepara bytes que precisam ser recusados na conferência de saída."""
        conteudo = exportar_csv_seguro(tabela)
        if mudanca in {"finance.view", "trends.view"}:
            scenario.permissions.discard(mudanca)
        elif mudanca == "usuario":
            scenario.user_id = "00000000-0000-0000-0000-000000000888"
        elif mudanca == "organizacao":
            scenario.organization_id = "00000000-0000-0000-0000-000000000999"
        elif mudanca == "sessao":
            service.auth._generation += 1
        else:
            scenario.authenticated = False
        return conteudo

    with patch(alvo, side_effect=serializar_e_mudar), pytest.raises((AuthenticationError, AuthorizationError)):
        if kind == "replenishment":
            service.export_replenishment()
        else:
            service.export_monthly_report("2026-09-01")


def test_relatorio_filtra_campos_novos_em_blocos_permitidos():
    """Bloco conhecido não autoriza incluir qualquer indicador em linha ou resumo."""
    relatorio = {
        "resumo": {"unidades_vendidas": 3, "receita_futura": 12345},
        "reposicao": [{"codigo": "FICTICIO", "quantidade_sugerida": 3, "custo_unitario": 12345}],
        "tendencias": [{"codigo": "FICTICIO", "tendencia": "Estável", "classe_abc": "A",
                         "segredo_futuro": 98765}],
        "financeiro": {"capital_estoque": 55555},
        "cautelas": ["ABC RESTRITA: 55555", "Sem fechamento comparável do mês anterior."],
    }
    permissoes = {"reports.view", "reports.export", "replenishment.view", "trends.view"}
    seguro = filtrar_relatorio(relatorio, permissoes)
    texto = exportar_relatorio_csv(relatorio, permissoes).decode("utf-8-sig")
    assert seguro["resumo"] == {"unidades_vendidas": 3}
    for proibido in ("receita_futura", "custo_unitario", "classe_abc", "segredo_futuro", "55555", "98765"):
        assert proibido not in texto
    assert "Sem fechamento comparável do mês anterior." in texto


def test_download_vinculado_a_tela_recusa_troca_de_usuario():
    """O callback antigo não acompanha uma identidade nova, mesmo com mesmo perfil."""
    scenario = Scenario(size=3)
    service = scenario.service_factory({})
    callback = service.deferred_export("replenishment", context=service.context())
    scenario.user_id = "00000000-0000-0000-0000-000000000888"
    service.auth._session = replace(service.auth.session, user_id=scenario.user_id)
    with pytest.raises(AuthorizationError):
        callback()
    assert not scenario.calls["rpc_load_inventory"]
