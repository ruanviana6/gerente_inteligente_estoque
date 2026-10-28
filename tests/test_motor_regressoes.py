"""Regressões de cálculo e interpretação identificadas na auditoria da Issue #3."""

import math

import numpy as np
import pandas as pd
import pytest

from config import Configuracao
from dados import validar_produtos
from motor import (
    calcular_reposicao,
    classificar_abc,
    classificar_status,
    classificar_tendencia,
    gerar_painel,
    medias_por_produto,
    somar_valores_financeiros,
)

HOJE = pd.Timestamp("2026-10-20")


def produtos(**campos):
    """Monta um catálogo fictício com os contratos normalizados do aplicativo."""
    return validar_produtos(pd.DataFrame([{"codigo": "A", "nome": "Produto A", "estoque": 10, **campos}]))


def vendas_vazias():
    """Fornece colunas sem linhas para verificar ausência completa de vendas."""
    return pd.DataFrame(columns=["codigo", "data", "quantidade"])


def snapshots_vazios():
    """Representa disponibilidade desconhecida, nunca disponibilidade comprovada."""
    return pd.DataFrame(columns=["codigo", "data", "estoque"])


def test_preserva_venda_com_snapshot_zero_e_sinaliza_conflito():
    """Uma foto zero não pode apagar as nove unidades efetivamente vendidas."""
    vendas = pd.DataFrame({"codigo": ["A"], "data": [HOJE], "quantidade": [9]})
    fotos = pd.DataFrame({"codigo": ["A"], "data": [HOJE], "estoque": [0]})
    linha = medias_por_produto(vendas, fotos, ["A"], HOJE, 1).loc["A"]
    assert linha["media"] == 9
    assert linha["dias_elegiveis"] == 1
    assert linha["dias_ruptura_conhecidos"] == 0
    assert linha["dias_snapshots_conflitantes"] == 1


def test_snapshot_ausente_nao_prova_ruptura_nem_inventa_demanda():
    """Dias desconhecidos permanecem no denominador com aviso explícito."""
    vendas = pd.DataFrame({"codigo": ["A"], "data": [HOJE], "quantidade": [6]})
    linha = medias_por_produto(vendas, snapshots_vazios(), ["A"], HOJE, 3).loc["A"]
    assert linha["media"] == 2
    assert linha["dias_elegiveis"] == 3
    assert linha["dias_disponibilidade_desconhecida"] == 3
    assert linha["dias_ruptura_conhecidos"] == 0


def test_janela_sem_dias_elegiveis_nao_prova_ausencia_demanda():
    """Sem disponibilidade observável, o painel exige revisão da recomendação."""
    fotos = pd.DataFrame({
        "codigo": ["A"] * 30, "data": pd.date_range(HOJE - pd.Timedelta(days=29), HOJE),
        "estoque": [0] * 30,
    })
    linha = gerar_painel(produtos(estoque=0), vendas_vazias(), fotos, hoje=HOJE).iloc[0]
    assert not linha["demanda_estimavel"]
    assert linha["requer_revisao"]
    assert linha["status"] == "Dados insuficientes"
    assert linha["tendencia"] == "Sem referência"
    assert "Sem dias elegíveis" in linha["aviso_qualidade"]
    assert math.isnan(linha["cobertura_dias"])


def test_venda_zero_nao_rejuvenesce_produto_parado():
    """Um lançamento sem unidades não altera a data da última venda real."""
    vendas = pd.DataFrame({
        "codigo": ["A", "A"], "data": [HOJE - pd.Timedelta(days=70), HOJE], "quantidade": [5, 0],
    })
    linha = gerar_painel(produtos(), vendas, hoje=HOJE).iloc[0]
    assert linha["dias_sem_venda"] == 70
    assert linha["status"] == "Parado"


def test_catalogo_sem_vendas_nao_falha_nem_presume_parado():
    """A ausência de histórico deve ser exibida sem falhar em operações de datas."""
    linha = gerar_painel(produtos(), vendas_vazias(), hoje=HOJE).iloc[0]
    assert linha["sem_historico_vendas"]
    assert linha["status"] == "Sem demanda recente"
    assert math.isnan(linha["dias_sem_venda"])
    assert "Sem venda positiva" in linha["aviso_qualidade"]


def test_historico_curto_avisa_sem_encurtar_denominador():
    """Não se presume que a primeira venda coincide com o início da operação."""
    vendas = pd.DataFrame({"codigo": ["A"], "data": [HOJE], "quantidade": [30]})
    linha = gerar_painel(produtos(), vendas, hoje=HOJE).iloc[0]
    assert linha["historico_curto"]
    assert linha["venda_media_diaria"] == 1
    assert linha["media_longa"] == pytest.approx(1 / 3)
    assert "confirme se o arquivo cobre" in linha["aviso_qualidade"]


@pytest.mark.parametrize("janela", [0, -1, True, 1.5, float("nan"), float("inf")])
def test_rejeita_janela_invalida(janela):
    """Recusa parâmetros que mudariam silenciosamente o intervalo calculado."""
    with pytest.raises(ValueError, match="janela"):
        medias_por_produto(vendas_vazias(), snapshots_vazios(), ["A"], HOJE, janela)


def test_snapshots_duplicados_nao_sao_resolvidos_pela_ordem_do_arquivo():
    """Dois saldos no mesmo dia são ambíguos e precisam ser reconciliados."""
    fotos = pd.DataFrame({"codigo": ["A", "A"], "data": [HOJE, HOJE], "estoque": [0, 10]})
    with pytest.raises(ValueError, match="repetidos"):
        medias_por_produto(vendas_vazias(), fotos, ["A"], HOJE, 1)


def test_codigos_duplicados_no_motor_sao_rejeitados():
    """Evita duplicar métricas ao chamar o motor diretamente."""
    with pytest.raises(ValueError, match="duplicados"):
        medias_por_produto(vendas_vazias(), snapshots_vazios(), ["A", "A"], HOJE, 1)


def test_medias_sem_catalogo_preservam_contrato_de_colunas():
    """Uma seleção vazia retorna uma tabela vazia, sem erro de índice."""
    resultado = medias_por_produto(vendas_vazias(), snapshots_vazios(), [], HOJE, 1)
    assert resultado.empty
    assert "media" in resultado


@pytest.mark.parametrize("config", [
    {"janela_venda": float("nan")}, {"janela_venda": 2.5}, {"janela_venda": True},
    {"janela_tendencia": 30}, {"dias_parado": 0}, {"limite_tendencia_pct": float("inf")},
    {"limite_tendencia_pct": float("nan")}, {"limite_tendencia_pct": True},
])
def test_configuracao_rejeita_valores_sem_sentido(config):
    """Valida tipos e finitude antes de construir as janelas de análise."""
    with pytest.raises(ValueError):
        Configuracao(**config)


@pytest.mark.parametrize("media", [True, "2", None, float("nan"), float("inf"), -1])
def test_reposicao_rejeita_demanda_invalida(media):
    """A API do domínio recebe números, não conversões implícitas de texto ou booleanos."""
    with pytest.raises(ValueError, match="Demanda"):
        calcular_reposicao(10, media, 5, 0)


def test_reposicao_nao_apaga_demanda_positiva_pequena():
    """O teto matemático de uma necessidade positiva não pode virar zero."""
    resultado = calcular_reposicao(0, 1e-11, 1, 0, cobertura_alvo=1)
    assert resultado.ponto_pedido == resultado.estoque_alvo == resultado.quantidade == 1


def test_reposicao_tolera_apenas_ruido_binario_perto_de_inteiro():
    """A média de 29 unidades em sete dias recompõe 29, não 30 por erro binário."""
    resultado = calcular_reposicao(0, 29 / 7, 7, 0, cobertura_alvo=7)
    assert resultado.ponto_pedido == resultado.estoque_alvo == 29
    assert calcular_reposicao(0, 1.000000001, 1, 0, cobertura_alvo=1).quantidade == 2


def test_reposicao_rejeita_overflow_float():
    """Entradas finitas que ultrapassam a representação não geram números falsos."""
    with pytest.raises(ValueError, match="limite de cálculo"):
        calcular_reposicao(10, 1e308, 10, 0)


def test_pendentes_reduzem_compra_sem_ocultar_risco_imediato():
    """Sem datas de recebimento, pendências não provam disponibilidade no prazo."""
    resultado = calcular_reposicao(5, 2, 10, 0, pendentes=100, cobertura_alvo=10)
    assert resultado.quantidade == 0
    assert resultado.risco_antes_entrega


def test_inteiros_numpy_nao_provocam_overflow_na_posicao():
    """Dados de pandas são aceitos e somados como inteiros Python sem wraparound."""
    limite = np.int64(np.iinfo(np.int64).max)
    resultado = calcular_reposicao(limite, 0, 1, 0, pendentes=limite)
    assert resultado.posicao_estoque == 2 * int(limite)
    assert resultado.quantidade == 0


@pytest.mark.parametrize("estoque,media,cobertura,dias,status", [
    (0, 1, 0, 0, "Ruptura"), (7, 1, 7, 0, "Crítico"),
    (8, 1, 8, 0, "Alto"), (15, 1, 15, 0, "Alto"),
    (16, 1, 16, 0, "Atenção"), (30, 1, 30, 0, "Atenção"),
    (31, 1, 31, 0, "OK"), (90, 1, 90, 0, "OK"),
    (91, 1, 91, 0, "Excesso"), (10, 0, float("nan"), 60, "Parado"),
])
def test_limites_do_semaforo(estoque, media, cobertura, dias, status):
    """Protege todos os limites inclusivos estabelecidos na regra operacional."""
    assert classificar_status(estoque, media, cobertura, dias, Configuracao())[0] == status


def test_tendencia_estavel_com_limiar_zero():
    """Médias iguais não representam crescimento mesmo quando o limiar é zero."""
    assert classificar_tendencia(2, 2, 0) == "➡ Estável"
    assert classificar_tendencia(0, 0, 20) == "Sem referência"
    assert classificar_tendencia(1, 2, 20) == "⬇ Caindo"
    assert classificar_tendencia(3, 2, 20) == "⬆ Crescendo"


@pytest.mark.parametrize("valor", [float("nan"), float("inf"), -1, True, None])
def test_tendencia_rejeita_valores_invalidos(valor):
    """Média inválida nunca deve aparecer como tendência estável."""
    with pytest.raises(ValueError):
        classificar_tendencia(valor, 2, 20)


def test_abc_custo_ausente_nao_e_receita_nem_custo_zero():
    """ABC usa somente os custos disponíveis e o painel revela a parcialidade."""
    catalogo = validar_produtos(pd.DataFrame([
        {"codigo": "A", "nome": "A", "estoque": 1, "custo_unitario": 10},
        {"codigo": "B", "nome": "B", "estoque": 1, "custo_unitario": None},
    ]))
    vendas = pd.DataFrame({"codigo": ["A", "B"], "data": [HOJE, HOJE], "quantidade": [1, 1000]})
    painel = gerar_painel(catalogo, vendas, hoje=HOJE).set_index("codigo")
    assert painel.loc["A", "classe_abc"] == "A"
    assert painel.loc["B", "classe_abc"] == "Não calculável"
    assert math.isnan(painel.loc["B", "capital_estoque"])
    assert painel["abc_parcial"].all()


def test_abc_em_empates_independe_da_ordem_do_catalogo():
    """O desempate é estável por SKU para uma reimportação não trocar classes."""
    catalogo = validar_produtos(pd.DataFrame([
        {"codigo": codigo, "nome": codigo, "estoque": 1, "custo_unitario": 1}
        for codigo in ["A", "B", "C", "D", "E"]
    ]))
    vendas = pd.DataFrame({"codigo": list("ABCDE"), "data": [HOJE] * 5, "quantidade": [1] * 5})
    antes = classificar_abc(vendas, catalogo, HOJE).sort_index()
    depois = classificar_abc(vendas, catalogo.iloc[::-1], HOJE).sort_index()
    pd.testing.assert_series_equal(antes, depois)


def test_painel_nao_muda_entradas_nem_usa_vendas_futuras():
    """Uma análise de data histórica não deve usar informações posteriores."""
    catalogo = produtos(custo_unitario=10)
    vendas = pd.DataFrame({
        "codigo": ["A", "A"], "data": [HOJE, HOJE + pd.Timedelta(days=1)], "quantidade": [30, 3000],
    })
    antes_p, antes_v = catalogo.copy(deep=True), vendas.copy(deep=True)
    linha = gerar_painel(catalogo, vendas, hoje=HOJE).iloc[0]
    assert linha["venda_media_diaria"] == 1
    assert linha["dias_sem_venda"] == 0
    pd.testing.assert_frame_equal(catalogo, antes_p)
    pd.testing.assert_frame_equal(vendas, antes_v)


def test_referencia_utc_respeita_dia_civil_de_fortaleza():
    """01h UTC ainda pertence ao dia anterior no fuso operacional aprovado."""
    vendas = pd.DataFrame({"codigo": ["A"], "data": [HOJE], "quantidade": [30]})
    painel = gerar_painel(produtos(), vendas, hoje="2026-10-21T01:00:00Z")
    assert painel.iloc[0]["dias_sem_venda"] == 0
    assert painel.iloc[0]["venda_media_diaria"] == 1


def test_data_de_referencia_vazia_e_rejeitada():
    """NaT não deve produzir silenciosamente janelas vazias e ausência de risco."""
    with pytest.raises(ValueError, match="data de referência"):
        gerar_painel(produtos(), vendas_vazias(), hoje=pd.NaT)


def test_overflow_de_capital_e_consumo_nao_vira_infinito_ou_classe_abc():
    """Custos finitos ainda podem produzir multiplicações impossíveis de representar."""
    catalogo = validar_produtos(pd.DataFrame([
        {"codigo": "A", "nome": "A", "estoque": 2, "custo_unitario": 1e308},
        {"codigo": "B", "nome": "B", "estoque": 2, "custo_unitario": 10},
    ]))
    vendas = pd.DataFrame({"codigo": ["A", "B"], "data": [HOJE, HOJE], "quantidade": [2, 1]})
    painel = gerar_painel(catalogo, vendas, hoje=HOJE).set_index("codigo")
    assert math.isnan(painel.loc["A", "capital_estoque"])
    assert painel.loc["B", "capital_estoque"] == 20
    assert painel["classe_abc"].eq("Não calculável").all()
    assert painel["financeiro_limite_excedido"].all()
    assert painel["aviso_qualidade"].str.contains("Limite numérico financeiro").all()
    assert painel["requer_revisao"].all()


def test_soma_financeira_excede_limite_mesmo_com_produtos_individualmente_finitos():
    """A soma de dois valores válidos pode invalidar os totais e a participação ABC."""
    catalogo = validar_produtos(pd.DataFrame([
        {"codigo": codigo, "nome": codigo, "estoque": 1, "custo_unitario": 1e308}
        for codigo in ("A", "B")
    ]))
    vendas = pd.DataFrame({"codigo": ["A", "B"], "data": [HOJE, HOJE], "quantidade": [1, 1]})
    painel = gerar_painel(catalogo, vendas, hoje=HOJE)
    assert painel["capital_estoque"].map(math.isfinite).all()
    assert math.isnan(somar_valores_financeiros(painel["capital_estoque"]))
    assert painel["classe_abc"].eq("Não calculável").all()
    assert painel["financeiro_limite_excedido"].all()


def test_limiar_financeiro_finito_continua_calculavel():
    """Valores representáveis no limite não são removidos por uma tolerância arbitrária."""
    limite = float(np.finfo(float).max)
    catalogo = produtos(estoque=2, custo_unitario=limite / 2)
    vendas = pd.DataFrame({"codigo": ["A"], "data": [HOJE], "quantidade": [2]})
    linha = gerar_painel(catalogo, vendas, hoje=HOJE).iloc[0]
    assert linha["capital_estoque"] == limite
    assert linha["classe_abc"] == "A"
    assert not linha["financeiro_limite_excedido"]
    assert somar_valores_financeiros([limite / 2, limite / 2]) == limite


@pytest.mark.parametrize("custo_ausente", [None, pd.NA, float("nan")], ids=["sqlite-null", "pandas-na", "nan"])
def test_sem_custo_nao_produz_total_zero_nem_falso_overflow(custo_ausente):
    """NULL do SQLite e ausências do pandas preservam indicadores não calculáveis."""
    catalogo = produtos()
    catalogo["custo_unitario"] = pd.Series([custo_ausente], dtype=object)
    vendas = pd.DataFrame({"codigo": ["A"], "data": [HOJE], "quantidade": [2]})
    linha = gerar_painel(catalogo, vendas, hoje=HOJE).iloc[0]
    assert math.isnan(linha["capital_estoque"])
    assert linha["classe_abc"] == "Não calculável"
    assert not linha["financeiro_limite_excedido"]
    assert math.isnan(somar_valores_financeiros([float("nan"), None]))
    assert math.isnan(somar_valores_financeiros([]))
    assert somar_valores_financeiros([float("nan"), 10, 20]) == 30
