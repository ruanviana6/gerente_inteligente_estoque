import pandas as pd
import pytest

from config import Configuracao
from dados import gerar_dados_exemplo
from motor import calcular_reposicao, classificar_status, gerar_painel, medias_por_produto

def test_reposicao():
    r = calcular_reposicao(35, 8, 6, 20, 10, 5, 6)
    assert (r.ponto_pedido, r.posicao_estoque, r.quantidade) == (68, 40, 28)
    assert r.risco_antes_entrega

@pytest.mark.parametrize("kwargs", [{"estoque": -1}, {"media_diaria": -2}, {"prazo_dias": -1}, {"cobertura_alvo": 0}])
def test_valores_invalidos(kwargs):
    dados = dict(estoque=10, media_diaria=2, prazo_dias=5, seguranca=3, cobertura_alvo=20)
    dados.update(kwargs)
    with pytest.raises(ValueError):
        calcular_reposicao(**dados)

def test_ruptura_comprovada_nao_reduz_media():
    hoje = pd.Timestamp("2026-10-20")
    vendas = pd.DataFrame({"codigo": ["A", "A"], "data": [hoje-pd.Timedelta(days=2), hoje], "quantidade": [4,4]})
    fotos = pd.DataFrame({"codigo": ["A"]*3, "data": pd.date_range(hoje-pd.Timedelta(days=2), hoje), "estoque":[5,0,5]})
    medias = medias_por_produto(vendas, fotos, ["A"], hoje, 3)
    assert medias.loc["A", "media"] == 4
    assert medias.loc["A", "dias_ruptura_conhecidos"] == 1

def test_produto_novo_nao_e_parado():
    assert classificar_status(20, 0, float("nan"), float("nan"), Configuracao())[0] == "Sem demanda recente"

def test_painel_simulado():
    p,v,s = gerar_dados_exemplo("2026-10-20")
    result = gerar_painel(p,v,s,hoje="2026-10-20")
    assert len(result) == len(p)
    assert result["quantidade_sugerida"].ge(0).all()
    assert result.loc[result["codigo"]=="P009","status"].item() == "Ruptura"

