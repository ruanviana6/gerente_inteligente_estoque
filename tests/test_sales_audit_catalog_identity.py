"""Regressões: a fotografia real com nome/categoria não bloqueia vendas observadas."""
from datetime import date
from decimal import Decimal

import pytest

from analytics.sales_audit import (
    conciliar_vendas_com_catalogo,
    csv_demanda_observada,
    rotular_agrupamento,
)
from integracoes.gestaoclick import ContratoInvalido
from integracoes.native_preview import DocumentoPreviaVenda, ItemPreviaVenda
from integracoes.sales_audit import AuditoriaVendas


def _auditoria():
    dia = date(2026, 11, 20)
    docs = (
        DocumentoPreviaVenda("venda-1", "PED-1", dia, "SEPARACAO", (
            ItemPreviaVenda("123", None, Decimal("2"), "UN"),
        )),
    )
    return AuditoriaVendas("416023", dia, dia, docs, 1, 1, 1)


def _foto(**extras):
    linha = {"id": "123", "estoque": "10",
             "codigo_interno": "PFF33", "nome": "Perfume Original 100 ml",
             "categoria": "Perfumaria"}
    linha.update(extras)
    return {"store_id": "416023", "product_count": 1, "products": [linha]}


def _conciliar(foto):
    return conciliar_vendas_com_catalogo(
        _auditoria(), foto, separacao_id="SEPARACAO",
        concretizada_id="CONCRETIZADA",
    )


def test_fotografia_real_com_rotulos_nao_interrompe_conciliacao():
    foto = _foto()
    resumo = _conciliar(foto)
    assert resumo.unidades_fechadas_un == Decimal("2")
    assert not resumo.ready_for_replenishment and not resumo.imported
    assert rotular_agrupamento(resumo, foto) == ({
        "Produto": "Perfume Original 100 ml", "Categoria": "Perfumaria",
        "Código interno": "PFF33", "Produto (ID)": "123",
        "Quantidade observada (UN)": "2",
    },)
    csv = csv_demanda_observada(resumo, foto).decode("utf-8-sig")
    assert csv.splitlines() == [
        "produto_id;nome_produto;categoria;codigo_interno;quantidade_observada_un",
        "123;Perfume Original 100 ml;Perfumaria;PFF33;2",
    ]
    assert ";10;" not in csv and "venda-1" not in csv


def test_fotografia_antiga_continua_aceita_sem_inventar_rotulos():
    foto = {"store_id": "416023", "product_count": 1,
            "products": [{"id": "123", "estoque": "10"}]}
    resumo = _conciliar(foto)
    linha = rotular_agrupamento(resumo, foto)[0]
    assert linha["Produto"] == "Não informado"
    assert linha["Categoria"] == "Não informada"
    assert linha["Código interno"] == "Não informado"
    # Compatibilidade com o CSV original utilizado fora da tela nova.
    assert csv_demanda_observada(resumo).decode("utf-8-sig").splitlines() == [
        "produto_id;quantidade_observada_un", "123;2",
    ]
    assert csv_demanda_observada(resumo, foto).decode("utf-8-sig").splitlines()[1] == "123;;;;2"


def test_rotulo_de_planilha_nao_executa_formula_csv():
    foto = _foto(nome="=2+2", categoria="+SUM(A1)", codigo_interno="@CMD")
    resumo = _conciliar(foto)
    csv = csv_demanda_observada(resumo, foto).decode("utf-8-sig")
    assert "123;'=2+2;'+SUM(A1);'@CMD;2" in csv


@pytest.mark.parametrize("campo,valor", [
    ("nome", "<img>"),
    ("categoria", "Categoria" + chr(10) + "Injetada"),
    ("codigo_interno", ["INDEVIDO"]),
    ("nome", "A" * 201),
    ("cliente", "Nao pertence ao contrato"),
])
def test_fotografia_com_dado_estranho_recusada(campo, valor):
    with pytest.raises(ContratoInvalido):
        _conciliar(_foto(**{campo: valor}))


def test_rotulos_nao_podem_ser_reaproveitados_de_outra_loja():
    foto = _foto()
    resumo = _conciliar(foto)
    foto["store_id"] = "LOJA-OUTRA"
    with pytest.raises(ContratoInvalido):
        rotular_agrupamento(resumo, foto, "416023")
