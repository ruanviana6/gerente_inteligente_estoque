"""Cenários fictícios de reposição, embalagens e riscos antes da chegada."""

from datetime import date
from decimal import Decimal

import pytest

from reposicao.planejamento import Recebimento, RegraProduto, simular_cenario


def simular(**campos):
    """Aplica valores fictícios, substituídos em cada cenário de negócio."""
    base = {"referencia": date(2026, 1, 1), "estoque": 30, "reservas": 2,
            "media_diaria": 2, "demanda_confiavel": True,
            "regra": RegraProduto(prazo_dias=5, seguranca=4, unidades_por_caixa=6),
            "ciclo_dias": 14}
    return simular_cenario(**(base | campos))


def test_ciclos_tem_horizontes_diferentes_sem_dividir_previsao_mensal():
    """O ciclo cobre a demanda diária correspondente e a previsão mensal permanece separada."""
    quatorze, vinte_um = simular(), simular(ciclo_dias=21)
    assert quatorze.demanda_30_dias == vinte_um.demanda_30_dias == Decimal(60)
    assert quatorze.necessidade_liquida == 14
    assert quatorze.unidades == 18 and quatorze.caixas == 3
    assert vinte_um.necessidade_liquida == 28
    assert vinte_um.unidades == 30 and vinte_um.caixas == 5


def test_espera_e_chegada_tardia_nao_ocultam_ruptura():
    """Uma solicitação tardia não é apresentada como solução de ruptura anterior."""
    resultado = simular(estoque=5, reservas=0, esperar_dias=14)
    assert resultado.primeira_ruptura == date(2026, 1, 4)
    assert resultado.data_chegada == date(2026, 1, 20)
    assert resultado.risco_antes_chegada
    assert resultado.data_limite < date(2026, 1, 1)


def test_recebimento_confirmado_tem_data_e_pendencia_nao_e_inventada():
    """Somente uma entrada informada e confirmada altera a projeção do dia."""
    sem = simular(estoque=12, reservas=0)
    com = simular(estoque=12, reservas=0, recebimentos=[Recebimento(date(2026, 1, 3), 12)])
    assert com.necessidade_liquida < sem.necessidade_liquida
    assert not com.risco_antes_chegada


def test_cobertura_alvo_do_produto_nao_e_ignorada_pelo_ciclo():
    """Um alvo maior amplia a cobertura; um menor não deixa o ciclo descoberto."""
    maior = simular(regra=RegraProduto(5, 4, 6, cobertura_alvo_dias=40))
    menor = simular(regra=RegraProduto(5, 4, 6, cobertura_alvo_dias=7))
    assert maior.necessidade_liquida == 66 and maior.caixas == 11
    assert menor.necessidade_liquida == simular().necessidade_liquida
    assert "40 dias" in maior.observacao


def test_recebimento_na_cobertura_reduz_pedido_sem_ocultar_falta_anterior():
    """Entradas posteriores à chegada entram no saldo sem antecipar sua data."""
    com = simular(recebimentos=[Recebimento(date(2026, 1, 10), 20)])
    assert com.unidades == 0
    # O saldo de 18 unidades na chegada só dura nove dias; receber no último
    # dia não elimina o déficit de oito unidades ocorrido antes dessa entrada.
    tarde = simular(recebimentos=[Recebimento(date(2026, 1, 20), 20)])
    assert tarde.necessidade_liquida == 8 and tarde.unidades == 12


def test_embalagem_ausente_impede_inventar_caixas():
    """A necessidade líquida é explicada, mas não vira caixas sem conversão conhecida."""
    resultado = simular(regra=RegraProduto(5, 4))
    assert resultado.necessidade_liquida == 14
    assert resultado.caixas is None and resultado.unidades is None
    assert "embalagem" in resultado.observacao


def test_multiplos_respeitam_caixa_lote_e_maximo_sem_corte_silencioso():
    """O arredondamento usa mínimo múltiplo comum e bloqueia limite incompatível."""
    regra = RegraProduto(5, 4, unidades_por_caixa=6, lote_minimo=20, multiplo=4)
    resultado = simular(regra=regra)
    assert resultado.unidades == 24 and resultado.caixas == 4
    limitado = simular(regra=RegraProduto(5, 4, unidades_por_caixa=6, lote_minimo=20, multiplo=4, maximo=22))
    assert limitado.unidades is None and limitado.caixas is None
    assert "revisão" in limitado.observacao


def test_dados_insuficientes_nao_geram_previsao_ou_sugestao():
    """Ausência de histórico ou disponibilidade incerta não é previsão de zero."""
    resultado = simular(media_diaria=None, demanda_confiavel=False)
    assert resultado.demanda_30_dias is None and resultado.unidades is None
    assert "Histórico" in resultado.observacao


@pytest.mark.parametrize("valor", [0, -1, 1.5, True, 100_001])
def test_conversao_de_caixa_invalida_e_recusada(valor):
    """Não aceita embalagem zero, fracionária, negativa ou acima do limite."""
    with pytest.raises(ValueError):
        RegraProduto(5, 4, unidades_por_caixa=valor)


@pytest.mark.parametrize("valor", [float("nan"), float("inf"), -1, True, None])
def test_demanda_invalida_e_recusada(valor):
    """Dados inválidos não viram um pedido ou previsão silenciosa."""
    with pytest.raises(ValueError):
        simular(media_diaria=valor)


def test_estoque_sem_consumo_nao_inventa_data_de_solicitacao():
    """Sem demanda observada não há data de ruptura projetada."""
    resultado = simular(media_diaria=0, estoque=30)
    assert resultado.demanda_30_dias == 0
    assert resultado.data_limite is None and resultado.primeira_ruptura is None
    assert resultado.unidades == 0 and resultado.caixas == 0
