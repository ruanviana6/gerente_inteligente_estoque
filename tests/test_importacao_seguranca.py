"""Regressões de integridade e segurança na importação e exportação fictícias."""
from datetime import datetime
from io import BytesIO

import numpy as np
import pandas as pd
import pytest

import dados
from dados import (
    MAX_INTEIRO,
    carregar_de_csv,
    exportar_csv_seguro,
    gerar_dados_exemplo,
    validar_produtos,
    validar_snapshots,
    validar_vendas,
)


def catalogo(**campos):
    """Cria catálogo mínimo fictício para exercitar contratos de entrada."""
    return validar_produtos(pd.DataFrame([{"codigo": "001", "nome": "Produto", "estoque": 10, **campos}]))


def csv_vendas(codigo="001", data="2026-10-01", quantidade="2"):
    """Prepara bytes de CSV normalizado sem dados da empresa."""
    return f"data;codigo;quantidade\n{data};{codigo};{quantidade}\n".encode()


@pytest.mark.parametrize("codigo", ["001", "NA", "NULL", "1E3"])
def test_preserva_identidade_textual_do_sku(codigo):
    produtos, vendas = carregar_de_csv(
        f"codigo;nome;estoque\n{codigo};Produto;10\n".encode(), csv_vendas(codigo),
    )
    assert produtos.codigo.tolist() == [codigo]
    assert vendas.codigo.tolist() == [codigo]


def test_datas_iso_e_brasileiras_agregam_o_mesmo_dia():
    vendas = validar_vendas(pd.DataFrame({
        "data": ["2026-10-01", "01/10/2026"], "codigo": ["001", "001"], "quantidade": [2, 3],
    }), catalogo())
    assert vendas.data.tolist() == [pd.Timestamp("2026-10-01")]
    assert vendas.quantidade.tolist() == [5]


@pytest.mark.parametrize("data", ["2099-01-01", "31/02/2026", "01-10-2026", "", None, 123,
                                      "2026-10-01T12:00:00", pd.Timestamp("2026-10-01", tz="UTC"),
                                      datetime(2026, 10, 1, 12)])
def test_datas_invalidas_rejeitadas_em_vendas_e_snapshots(data):
    produtos = catalogo()
    with pytest.raises(ValueError):
        validar_vendas(pd.DataFrame([{"data": data, "codigo": "001", "quantidade": 1}]), produtos)
    with pytest.raises(ValueError):
        validar_snapshots(pd.DataFrame([{"data": data, "codigo": "001", "estoque": 1}]), produtos)


def test_data_futura_considera_fuso_civil(monkeypatch):
    monkeypatch.setattr(dados, "hoje_local", lambda: pd.Timestamp("2026-10-01"))
    with pytest.raises(ValueError, match="futuras"):
        validar_vendas(pd.DataFrame([{"data": "2026-10-02", "codigo": "001", "quantidade": 1}]), catalogo())


@pytest.mark.parametrize("valor", [float("inf"), float("-inf"), True, np.bool_(False), -1, "erro", "nan"])
def test_custo_invalido_e_rejeitado(valor):
    with pytest.raises(ValueError):
        catalogo(custo_unitario=valor)


@pytest.mark.parametrize("valor", [None, float("nan"), "", "   "])
def test_custo_ausente_permanece_desconhecido(valor):
    assert pd.isna(catalogo(custo_unitario=valor).custo_unitario.item())


@pytest.mark.parametrize("valor", [True, False, float("inf"), -1, 1.5, MAX_INTEIRO + 1])
def test_estoque_exige_inteiro_finito_no_intervalo(valor):
    with pytest.raises(ValueError):
        catalogo(estoque=valor)


def test_soma_de_vendas_tambem_respeita_limite():
    with pytest.raises(ValueError, match="limite"):
        validar_vendas(pd.DataFrame({
            "data": ["2026-10-01"] * 2, "codigo": ["001"] * 2,
            "quantidade": [MAX_INTEIRO, 1],
        }), catalogo())


def test_custo_decimal_brasileiro_e_opcionais_vazios():
    produtos, _ = carregar_de_csv(
        b"codigo;nome;estoque;custo_unitario;prazo_entrega_dias;reservas\n001;Produto;10;12,50;;\n",
        csv_vendas(),
    )
    assert produtos.custo_unitario.item() == 12.5
    assert produtos.prazo_entrega_dias.item() == 10
    assert produtos.reservas.item() == 0


@pytest.mark.parametrize("conteudo", [b"", b"codigo;nome;estoque\n", b"codigo;nome;estoque;estoque\n001;X;1;2\n",
    b"codigo;nome;estoque\n001;X;1;extra\n", b"codigo;nome;estoque\n001;X\n",
    b"codigo;nome;estoque\n001;\xff;1\n", b"codigo;nome;estoque\n001;X\x00;1\n",
    b'codigo;nome;estoque\n001;"X;1\n'])
def test_csv_malformado_nao_e_importado(conteudo):
    with pytest.raises(ValueError):
        carregar_de_csv(conteudo, csv_vendas())


def test_limite_de_bytes_e_aplicado_antes_de_processar(monkeypatch):
    monkeypatch.setattr(dados, "MAX_CSV_BYTES", 10)
    for arquivo in (b"x" * 11, BytesIO(b"x" * 11)):
        with pytest.raises(ValueError, match="limite"):
            carregar_de_csv(arquivo, csv_vendas())


def test_limite_de_linhas_nao_descarta_silenciosamente(monkeypatch):
    monkeypatch.setattr(dados, "MAX_CSV_LINHAS", 1)
    with pytest.raises(ValueError, match="limite"):
        carregar_de_csv(b"codigo;nome;estoque\n001;X;1\n002;Y;2\n", csv_vendas())


def test_cabecalho_repetido_em_dataframe_e_rejeitado():
    bruto = pd.DataFrame([["001", "Produto", 1, 2]], columns=["codigo", "nome", "estoque", "estoque"])
    with pytest.raises(ValueError, match="repetidas"):
        validar_produtos(bruto)


def test_snapshots_duplicados_apos_normalizar_datas_sao_rejeitados():
    bruto = pd.DataFrame({"codigo": ["001", "001"], "data": ["2026-10-01", "01/10/2026"], "estoque": [1, 2]})
    with pytest.raises(ValueError, match="repetidos"):
        validar_snapshots(bruto, catalogo())


def test_sem_vendas_e_sem_snapshots_preservam_esquema():
    vendas = validar_vendas(pd.DataFrame(columns=dados.COLUNAS_VENDAS), catalogo())
    fotos = validar_snapshots(pd.DataFrame(), catalogo())
    assert str(vendas.data.dtype) == "datetime64[ns]"
    assert str(fotos.data.dtype) == "datetime64[ns]"
    assert vendas.empty and fotos.empty


def test_sku_desconhecido_e_rejeitado_sem_ecoar_conteudo():
    with pytest.raises(ValueError, match="ausentes") as erro:
        validar_vendas(pd.DataFrame([{"data": "2026-10-01", "codigo": "<script>segredo</script>", "quantidade": 1}]), catalogo())
    assert "segredo" not in str(erro.value)


@pytest.mark.parametrize("texto", ["=1+1", "+1+1", "-1+1", "@SUM(A1)", " \t=1+1", "\r\n+1+1"])
def test_exportacao_neutraliza_formulas(texto):
    tabela = pd.DataFrame({"nome": [texto], "quantidade": [12], "custo": [2.5]})
    exportado = exportar_csv_seguro(tabela)
    lido = pd.read_csv(BytesIO(exportado), sep=";", decimal=",")
    assert lido.nome.item() == "'" + texto
    assert lido.quantidade.item() == 12
    assert lido.custo.item() == 2.5
    assert tabela.nome.item() == texto


def test_exportacao_tambem_neutraliza_cabecalhos():
    exportado = exportar_csv_seguro(pd.DataFrame({"=1+1": [1]})).decode("utf-8-sig")
    assert exportado.startswith("'=1+1")


def test_demo_nao_contradiz_suas_fotos_de_ruptura():
    _, vendas, fotos = gerar_dados_exemplo("2026-10-20")
    cruzado = vendas.merge(fotos, on=["codigo", "data"])
    assert not ((cruzado.quantidade > 0) & (cruzado.estoque == 0)).any()
