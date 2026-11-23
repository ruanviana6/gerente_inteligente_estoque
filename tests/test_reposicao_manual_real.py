"""Planejamento por produto real só com dados explicitamente conferidos."""
import csv
import io
from datetime import date

import pytest

from reposicao.cenario_manual_real import preparar_reposicao_real
from web.real_catalog_stock_page import validar_fotografia
from web.real_manual_replenishment_page import (
    exportar_cenario_manual_csv,
    produtos_reais_identificados,
    rotulo_produto_real,
)

DATA = date(2026, 11, 23)


def foto(nome="Perfumaria 100 ml", categoria="Perfumes"):
    bruto = {
        "found": True, "store_id": "416023",
        "created_at": "2026-11-17T15:00:00-03:00",
        "product_count": 2, "total_stock": "1012",
        "products": [
            {"id": "1001", "nome": nome, "categoria": categoria,
             "codigo_interno": "SKU-1001", "estoque": "12"},
            {"id": "1002", "nome": "B", "categoria": "Linha B", "estoque": "1000"},
        ],
    }
    return validar_fotografia(bruto, "416023")


def simular(**overrides):
    params = {
        "fotografia": foto(), "produto_id": "1001", "estoque": "30",
        "reservas": "2", "media_diaria": "2", "unidades_por_caixa": "6",
        "prazo_dias": "5", "seguranca": "4", "ciclo_dias": 14,
        "referencia": DATA, "conferencia_explicita": True,
    }
    return preparar_reposicao_real(**(params | overrides))


def test_exige_saldo_atual_manual_sem_usar_saldo_da_fotografia():
    previa = simular()
    assert previa.produto_id == "1001"
    assert previa.loja_id == "416023"
    assert previa.estoque_conferido == 30
    assert previa.fotografia_em.date() == date(2026, 11, 17)
    assert previa.conferencia_em == DATA
    assert previa.resultado.necessidade_liquida == 46
    assert previa.resultado.unidades == 48
    assert previa.resultado.caixas == 8
    assert previa.resultado.data_chegada == date(2026, 11, 28)


def test_ciclo_21_e_meta_30_nao_sao_ignorados():
    previa = simular(ciclo_dias=21, media_diaria="2,5")
    assert previa.ciclo_dias == 21
    assert previa.media_diaria_conferida == pytest.approx(2.5)
    assert previa.resultado.caixas >= 8
    assert "30 dias" in previa.resultado.observacao


@pytest.mark.parametrize(("field", "value"), [
    ("estoque", ""), ("estoque", "-1"), ("estoque", "3.2"),
    ("reservas", ""), ("reservas", "-0"), ("media_diaria", ""),
    ("media_diaria", "-1"), ("media_diaria", "NaN"),
    ("media_diaria", "1e6"), ("media_diaria", "0,00001"),
    ("unidades_por_caixa", ""), ("unidades_por_caixa", "0"),
    ("unidades_por_caixa", "100001"),
    ("prazo_dias", ""), ("prazo_dias", "366"),
    ("seguranca", ""), ("seguranca", "3,5"),
])
def test_sem_inputs_confirmados_nao_cria_sugestao(field, value):
    with pytest.raises(ValueError):
        simular(**{field: value})


def test_sem_conferencia_nao_computa_nem_com_zero_implicito():
    with pytest.raises(ValueError, match="Confirme"):
        simular(conferencia_explicita=False)
    assert simular(media_diaria="0").resultado.unidades == 0


@pytest.mark.parametrize("produto", ["1000", "1003", "=", "1001;1002", None])
def test_produto_desconhecido_ou_invalido_nao_usa_estoque_de_outra_loja(produto):
    with pytest.raises(ValueError):
        simular(produto_id=produto)


def test_rejeita_foto_com_loja_ausente_ou_sem_data():
    com_falha = foto() | {"created_at": "17/11/2026"}
    with pytest.raises(ValueError):
        simular(fotografia=com_falha)


@pytest.mark.parametrize("ciclo", [0, 7, 30, "14", True])
def test_so_aceita_ciclos_acordados(ciclo):
    with pytest.raises(ValueError):
        simular(ciclo_dias=ciclo)


def test_exportacao_e_apenas_cenario_manual_sem_pedido_no_erp():
    previa = simular(fotografia=foto("=1+1", "+CATEGORIA"))
    conteudo = exportar_cenario_manual_csv(previa).decode("utf-8-sig")
    linhas = list(csv.reader(io.StringIO(conteudo), delimiter=";"))
    assert len(linhas) == 2
    assert linhas[1][2] == "'=1+1"
    assert linhas[1][3] == "'+CATEGORIA"
    assert linhas[1][1] == "1001"
    assert linhas[1][-1] == "Informada e conferida manualmente pelo operador"
    assert linhas[1][14:16] == ["48", "8"]


def test_seletor_real_mostra_nome_categoria_id_e_saldo_historico():
    catalogo = foto()
    opcoes, pendentes = produtos_reais_identificados(catalogo)
    assert pendentes == 0
    assert set(opcoes) == {"1001", "1002"}
    assert rotulo_produto_real(opcoes["1001"]) == (
        "Perfumaria 100 ml · Perfumes · ID 1001 · Saldo histórico: 12 UN"
    )


def test_foto_legada_sem_nomes_nao_exibe_lista_de_ids_para_calcular():
    catalogo = foto()
    for row in catalogo["rows"]:
        row["Produto"] = "Não informado"
        row["Categoria"] = "Não informada"
    opcoes, pendentes = produtos_reais_identificados(catalogo)
    assert opcoes == {}
    assert pendentes == 2
    assert [r["Saldo na fotografia (não atual)"] for r in catalogo["rows"]] == [12, 1000]


def test_identificacao_parcial_nao_esconde_produto_identificado():
    catalogo = foto()
    catalogo["rows"][0]["Produto"] = "Não informado"
    opcoes, pendentes = produtos_reais_identificados(catalogo)
    assert pendentes == 1
    assert list(opcoes) == ["1002"]
    assert "Saldo histórico: 1000 UN" in rotulo_produto_real(opcoes["1002"])


def test_saldo_historico_fracionario_nao_e_arredondado_no_rotulo():
    catalogo = foto()
    catalogo["rows"][0]["Saldo na fotografia (não atual)"] = 2.125
    opcoes, _ = produtos_reais_identificados(catalogo)
    assert "Saldo histórico: 2.125 UN" in rotulo_produto_real(opcoes["1001"])
