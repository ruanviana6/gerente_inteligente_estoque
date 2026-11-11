"""Valida insights determinísticos sem expor indicadores não autorizados."""

import math
import re

import pandas as pd

from dados import validar_produtos, validar_vendas
from motor import gerar_painel
from web.insights import gerar_insights


def painel_ficticio():
    """Produz risco, excesso e incerteza usando o mesmo motor do aplicativo."""
    produtos = validar_produtos(pd.DataFrame([
        {"codigo": "A", "nome": "Produto de risco", "estoque": 2,
         "prazo_entrega_dias": 10, "custo_unitario": 123.45},
        {"codigo": "B", "nome": "Produto em excesso", "estoque": 1000,
         "prazo_entrega_dias": 5, "custo_unitario": 43.21},
    ]))
    vendas = validar_vendas(pd.DataFrame([
        {"codigo": "A", "data": "2026-09-10", "quantidade": 60},
        {"codigo": "B", "data": "2026-09-10", "quantidade": 1},
    ]), produtos)
    return gerar_painel(produtos, vendas, hoje="2026-09-30")


def test_sem_dashboard_nao_gera_insights():
    """Outras permissões não autorizam o flash da página inicial."""
    assert gerar_insights(painel_ficticio(), {"inventory.view", "finance.view"}) == []


def test_insights_exigem_texto_e_explicacao_do_calculo():
    """Cada observação deve ser compreensível, verificável e ordenável."""
    itens = gerar_insights(painel_ficticio(), {"dashboard.view", "inventory.view", "replenishment.view"})
    assert itens
    for item in itens:
        assert item.titulo.strip()
        assert item.texto.strip()
        assert item.explicacao.strip()
        assert isinstance(item.prioridade, int)


def test_mesmos_dados_produzem_mesmos_insights():
    """O flash não contém geração aleatória nem números inventados por IA."""
    painel = painel_ficticio()
    permissoes = {"dashboard.view", "replenishment.view", "inventory.view"}
    assert gerar_insights(painel, permissoes) == gerar_insights(painel, permissoes)


def test_insights_nao_modificam_painel_nem_permissoes():
    """A preparação visual não altera indicadores usados por outros módulos."""
    painel = painel_ficticio()
    original = painel.copy(deep=True)
    permissoes = {"dashboard.view", "inventory.view"}
    gerar_insights(painel, permissoes)
    pd.testing.assert_frame_equal(painel, original)
    assert permissoes == {"dashboard.view", "inventory.view"}


def test_financeiro_nao_aparece_sem_permissao():
    """Custos eventualmente presentes no DataFrame não autorizam sua divulgação."""
    itens = gerar_insights(painel_ficticio(), {"dashboard.view", "inventory.view", "trends.view"})
    textos = " ".join(item.titulo + " " + item.texto + " " + item.explicacao for item in itens)
    assert "R$" not in textos
    assert "123.45" not in textos
    assert "43.21" not in textos
    assert "Curva ABC" not in textos


def test_painel_vazio_nao_inventa_alertas():
    """Sem produtos não pode haver afirmação de risco ou de capital existente."""
    itens = gerar_insights(pd.DataFrame(), {"dashboard.view", "finance.view"})
    assert isinstance(itens, list)
    assert not any("R$" in item.texto for item in itens)


def test_capital_desconhecido_nao_vira_infinito_no_flash():
    """Os limites financeiros validados no motor também valem para os avisos."""
    painel = painel_ficticio()
    painel["capital_estoque"] = math.nan
    painel["custo_unitario"] = math.nan
    itens = gerar_insights(painel, {"dashboard.view", "finance.view", "inventory.view"})
    textos = " ".join(item.titulo + " " + item.texto + " " + item.explicacao for item in itens).lower()
    assert re.search(r"\b(?:nan|inf|infinity)\b", textos) is None
