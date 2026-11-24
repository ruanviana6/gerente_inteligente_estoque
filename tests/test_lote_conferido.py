"""Testes do planejamento em lote com dados confirmados pelo operador."""
from datetime import date, datetime, timezone

import pytest

from reposicao.lote_conferido import calcular_lote_conferido, exportar_lote_conferido
from reposicao.quadro_real import QuadroReposicaoReal


def quadro():
    linhas = tuple({
        "Produto (ID)": str(i), "Produto": nome,
        "Variações": "Sem variações no retorno" if i != 3 else "Conferir unidades/variações",
        "Saldo GET (unidade do ERP não conferida)": "999",
        "Vendas fechadas observadas (UN)": "Não observado no período",
    } for i, nome in [(1, "Perfume A"), (2, "=Produto B"), (3, "Produto C")])
    return QuadroReposicaoReal("001", datetime(2026, 11, 24, tzinfo=timezone.utc),
                              None, None, 3, 3, 0, 0, linhas)


def entrada(ident="1", **alteracoes):
    return {
        "Produto (ID)": ident, "Disponível (UN)": "10",
        "Saída líquida (UN/dia)": "2", "UN/CX": "12", "Segurança (UN)": "0",
        **alteracoes,
    }


def calcular(entradas, confirmado=True):
    return calcular_lote_conferido(
        quadro(), entradas, referencia=date(2026, 11, 24), prazo_dias="5",
        ciclo_dias=14, confirmado=confirmado,
    )


def test_calcula_varios_produtos_sem_usar_venda_ou_saldo_get():
    linhas = calcular([entrada(), entrada("2", **{"Disponível (UN)": "100"})])
    assert linhas[0]["Necessidade (UN)"] == 60
    assert linhas[0]["Sugestão (CX)"] == 5
    assert linhas[0]["Sugestão (UN)"] == 60
    assert linhas[0]["Risco antes da chegada"] == "Sim"
    assert linhas[1]["Sugestão (CX)"] == 0
    assert all(linha["Loja (ID)"] == "001" for linha in linhas)
    assert "'=Produto B" in exportar_lote_conferido(linhas).decode("utf-8-sig")


@pytest.mark.parametrize("entradas", [
    [], [entrada("999")], [entrada("3")], [entrada(), entrada()],
    [entrada(**{"Disponível (UN)": ""})],
    [entrada(**{"Saída líquida (UN/dia)": ""})],
    [entrada(**{"UN/CX": "0"})],
    [entrada(**{"Segurança (UN)": ""})],
    [entrada(**{"Disponível (UN)": "-1"})],
])
def test_recusa_lote_invalido_sem_resultado_parcial(entradas):
    with pytest.raises(ValueError):
        calcular(entradas)


def test_exige_confirmacao_e_aceita_zero_explicito():
    with pytest.raises(ValueError):
        calcular([entrada()], confirmado=False)
    linhas = calcular([entrada(**{"Saída líquida (UN/dia)": "0"})])
    assert linhas[0]["Sugestão (CX)"] == 0
