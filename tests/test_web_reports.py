"""Exercita fechamento mensal, origem histórica e autorização de relatórios."""

from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from web.reports import exportar_relatorio_csv, gerar_relatorio_mensal, mes_anterior

MES = date(2026, 9, 1)
HOJE = date(2026, 10, 15)
PERMISSOES = {
    "reports.view", "reports.export", "inventory.view", "replenishment.view",
    "trends.view", "finance.view",
}


def base_mensal():
    """Fornece fechamento fictício com vendas históricas fora do mês também."""
    produtos = [
        {"codigo": "001", "nome": "Peça A", "estoque": 10, "custo_unitario": 5,
         "fornecedor": "Fornecedor A", "prazo_entrega_dias": 10},
        {"codigo": "B", "nome": "Peça B", "estoque": 0, "custo_unitario": 9,
         "fornecedor": "Fornecedor B", "prazo_entrega_dias": 5},
        {"codigo": "C", "nome": "Peça C", "estoque": 300, "custo_unitario": 2},
        {"codigo": "D", "nome": "Peça D", "estoque": 8, "custo_unitario": 7},
    ]
    return {
        "products": produtos,
        "sales": [
            {"codigo": "001", "data": "2026-08-20", "quantidade": 1000},
            {"codigo": "001", "data": "2026-09-01", "quantidade": 20},
            {"codigo": "001", "data": "2026-09-30", "quantidade": 40},
            {"codigo": "B", "data": "2026-09-10", "quantidade": 30},
            {"codigo": "C", "data": "2026-09-05", "quantidade": 1},
            {"codigo": "D", "data": "2026-07-01", "quantidade": 1},
        ],
        "snapshots": [
            {"codigo": item["codigo"], "data": "2026-09-30", "estoque": item["estoque"]}
            for item in produtos
        ],
        "imports": [],
        "metadata": {
            "batch_id": "00000000-0000-0000-0000-000000000123",
            "snapshot_date": "2026-09-30", "period_start": "2026-07-01",
            "period_end": "2026-09-30", "created_at": "2026-10-01T08:00:00Z",
            "source": "CSV fictício", "month_complete": True,
        },
    }


def gerar(payload=None, permissoes=None, **opcoes):
    """Executa o mês de referência com data de avaliação fixa e reproduzível."""
    return gerar_relatorio_mensal(
        payload if payload is not None else base_mensal(), MES,
        PERMISSOES if permissoes is None else permissoes, hoje=HOJE, **opcoes,
    )


def chaves_recursivas(valor):
    """Inspeciona todas as camadas para detectar vazamento por blocos aninhados."""
    if isinstance(valor, dict):
        return set(valor).union(*(chaves_recursivas(item) for item in valor.values()))
    if isinstance(valor, list):
        return set().union(*(chaves_recursivas(item) for item in valor))
    return set()


def test_total_mensal_exclui_vendas_de_meses_anteriores():
    """A janela do relatório não se confunde com as janelas móveis do motor."""
    resultado = gerar()
    assert resultado["resumo"]["unidades_vendidas"] == 91
    assert resultado["resumo"]["produtos_com_venda"] == 3
    assert resultado["mais_vendidos"][0]["codigo"] == "001"
    assert resultado["mais_vendidos"][0]["quantidade"] == 60
    assert resultado["resumo"]["variacao_unidades_pct"] is None


def test_relatorio_preserva_a_identidade_e_a_origem_do_fechamento():
    """O leitor consegue identificar lote, período e momento da importação."""
    resultado = gerar()
    assert resultado["periodo"] == {"inicio": "2026-09-01", "fim": "2026-09-30"}
    assert resultado["fonte"]["batch_id"] == base_mensal()["metadata"]["batch_id"]
    assert resultado["fonte"]["origem"] == "CSV fictício"
    assert resultado["fonte"]["atualizado_em"] == "2026-10-01T08:00:00Z"


def test_geracao_nao_modifica_payload_nem_permissoes():
    """Um usuário não pode alterar o material reutilizado por outro cálculo."""
    payload = base_mensal()
    original = deepcopy(payload)
    permissoes = set(PERMISSOES)
    gerar(payload, permissoes)
    assert payload == original
    assert permissoes == PERMISSOES


@pytest.mark.parametrize("permissoes", [set(), {"reports.export"}, {"finance.view"}])
def test_geracao_exige_permissao_de_visualizacao(permissoes):
    """Exportação ou financeiro isoladamente não autorizam ler o relatório."""
    with pytest.raises(PermissionError):
        gerar(permissoes=permissoes)


@pytest.mark.parametrize("campo,valor", [
    ("month_complete", False), ("month_complete", "true"),
    ("period_start", "2026-09-02"), ("period_end", "2026-09-29"),
    ("snapshot_date", "2026-10-01"), ("snapshot_date", "2026-09-29"),
])
def test_dados_sem_fechamento_do_mes_sao_rejeitados(campo, valor):
    """Não se fabrica fechamento com estoque atual ou intervalo parcial."""
    payload = base_mensal()
    payload["metadata"][campo] = valor
    with pytest.raises(ValueError):
        gerar(payload)


def test_mes_em_andamento_nao_pode_ser_fechado():
    """Mesmo metadados coerentes não tornam encerrado o mês civil atual."""
    with pytest.raises(ValueError):
        gerar_relatorio_mensal(base_mensal(), MES, PERMISSOES, hoje=date(2026, 9, 30))


@pytest.mark.parametrize("alteracao", ["ausente", "saldo_divergente"])
def test_foto_do_fechamento_deve_corresponder_ao_catalogo(alteracao):
    """A data do lote não substitui evidência do saldo de cada produto."""
    payload = base_mensal()
    if alteracao == "ausente":
        payload["snapshots"].pop()
    else:
        payload["snapshots"][0]["estoque"] = 999
    with pytest.raises(ValueError):
        gerar(payload)


def test_ausencia_de_snapshots_intermediarios_e_explicita():
    """O fechamento não converte dias sem foto em disponibilidade comprovada."""
    resultado = gerar()
    assert resultado["cautelas"]
    assert any("snapshot" in aviso.lower() or "disponibilidade" in aviso.lower()
               for aviso in resultado["cautelas"])


def test_relatorio_sem_financeiro_nao_expoe_custo_nem_abc():
    """A remoção ocorre antes de devolver qualquer bloco ao navegador."""
    resultado = gerar(permissoes=PERMISSOES - {"finance.view"})
    proibidas = {"financeiro", "custo_unitario", "capital_estoque", "capital_parado", "classe_abc"}
    assert not (chaves_recursivas(resultado) & proibidas)


def test_financeiro_autorizado_usa_custo_do_fechamento():
    """10×5 + 0×9 + 300×2 + 8×7 = 706, sem confundir capital com receita."""
    resultado = gerar()
    assert resultado["financeiro"]["capital_estoque"] == 706
    assert resultado["financeiro"]["capital_parado"] == 56
    assert not resultado["financeiro"]["custos_parciais"]


def test_custos_ausentes_nao_viram_capital_zero():
    """O relatório financeiro conserva a ausência de informação do motor."""
    payload = base_mensal()
    for produto in payload["products"]:
        produto["custo_unitario"] = None
    resultado = gerar(payload)
    assert resultado["financeiro"]["capital_estoque"] is None
    assert resultado["financeiro"]["capital_parado"] is None
    assert resultado["financeiro"]["custos_parciais"]


def test_custo_invalido_nao_e_processado_sem_permissao_financeira():
    """Até um payload excessivo é reduzido ao escopo autorizado antes de calcular."""
    payload = base_mensal()
    payload["products"][0]["custo_unitario"] = "CONTEUDO_FINANCEIRO_RESTRITO"
    resultado = gerar(payload, PERMISSOES - {"finance.view"})
    assert resultado["resumo"]["unidades_vendidas"] == 91
    assert "CONTEUDO_FINANCEIRO_RESTRITO" not in str(resultado)


def test_permissoes_de_modulo_controlam_os_blocos():
    """Acesso a relatórios não concede implicitamente todos os módulos."""
    resultado = gerar(permissoes={"reports.view"})
    assert {"estoque", "reposicao", "tendencias", "financeiro"}.isdisjoint(resultado)
    assert resultado["mais_vendidos"]


def test_mes_sem_venda_nao_inventa_variacao_ou_receita():
    """Um catálogo completo sem transações é diferente de um período incompleto."""
    payload = base_mensal()
    payload["sales"] = []
    resultado = gerar(payload)
    assert resultado["resumo"]["unidades_vendidas"] == 0
    assert resultado["resumo"]["produtos_com_venda"] == 0
    assert resultado["mais_vendidos"] == []
    assert resultado["resumo"]["variacao_unidades_pct"] is None
    assert not ({"faturamento", "margem", "receita"} & chaves_recursivas(resultado))


def test_comparacao_usa_apenas_mes_anterior_fechado_e_comparavel():
    """Setembro de 91 unidades ante agosto de 50 corresponde a aumento de 82%."""
    anterior = base_mensal()
    anterior["metadata"].update(snapshot_date="2026-08-31", period_end="2026-08-31")
    anterior["sales"] = [{"codigo": "001", "data": "2026-08-15", "quantidade": 50}]
    for foto in anterior["snapshots"]:
        foto["data"] = "2026-08-31"
    resultado = gerar(anterior=anterior)
    assert resultado["resumo"]["variacao_unidades_pct"] == pytest.approx(82)


def test_periodo_anterior_incompleto_nao_gera_percentual_enganoso():
    """O relatório atual pode existir sem inventar comparação de outro fechamento."""
    anterior = base_mensal()
    anterior["metadata"]["month_complete"] = False
    resultado = gerar(anterior=anterior)
    assert resultado["resumo"]["variacao_unidades_pct"] is None


def test_mes_anterior_sem_vendas_nao_produz_divisao_por_zero():
    """Passar de zero a unidades positivas não possui crescimento percentual finito."""
    anterior = base_mensal()
    anterior["metadata"].update(snapshot_date="2026-08-31", period_end="2026-08-31")
    anterior["sales"] = []
    for foto in anterior["snapshots"]:
        foto["data"] = "2026-08-31"
    resultado = gerar(anterior=anterior)
    assert resultado["resumo"]["variacao_unidades_pct"] is None


@pytest.mark.parametrize("permissoes", [set(), {"reports.view"}, {"reports.export"}])
def test_exportacao_exige_visualizacao_e_exportacao(permissoes):
    """A permissão de download é independente e também depende da leitura atual."""
    with pytest.raises(PermissionError):
        exportar_relatorio_csv(gerar(), permissoes)


def test_exportacao_revalida_revogacao_financeira_e_de_modulos():
    """Um relatório antigo em memória não mantém direitos já retirados."""
    resultado = gerar()
    dados = exportar_relatorio_csv(resultado, {"reports.view", "reports.export"}).decode("utf-8-sig")
    for proibido in ("capital_estoque", "capital_parado", "custo_unitario", "classe_abc", "financeiro"):
        assert proibido not in dados
    assert "quantidade_sugerida" not in dados
    assert "tendencia" not in dados


def test_csv_neutraliza_formula_em_nome_de_produto():
    """O relatório exportado preserva a proteção de planilhas do PR #4."""
    payload = base_mensal()
    payload["products"][0]["nome"] = '=HYPERLINK("https://invalid.example")'
    dados = exportar_relatorio_csv(gerar(payload), PERMISSOES).decode("utf-8-sig")
    assert "'=HYPERLINK" in dados


@pytest.mark.parametrize("agora,esperado", [
    (datetime(2026, 1, 1, 1, tzinfo=timezone.utc), date(2025, 11, 1)),
    (datetime(2026, 1, 1, 3, tzinfo=timezone.utc), date(2025, 12, 1)),
    (datetime(2024, 3, 1, 9, tzinfo=timezone.utc), date(2024, 2, 1)),
])
def test_mes_anterior_respeita_fortaleza_e_virada_de_ano(agora, esperado):
    """O cron deve fechar o mês civil local, não o mês indicado pelo relógio UTC."""
    assert mes_anterior(agora) == esperado
