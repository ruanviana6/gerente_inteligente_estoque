"""Valida leitura mobile de saldo e vendas sem depender da tabela horizontal."""
import pytest
from streamlit.testing.v1 import AppTest

from integracoes.gestaoclick import ContratoInvalido
from web.real_batch_replenishment_page import _mostrar_detalhe_produto

SCRIPT = """
from web.real_batch_replenishment_page import _mostrar_detalhe_produto

linhas = [
    {
        "Produto": "Body Mist Charme",
        "Categoria": "Body Mist",
        "Código interno": "BM-101",
        "Produto (ID)": "101",
        "Saldo GET (unidade do ERP não conferida)": "12",
        "Vendas fechadas observadas (UN)": "75",
        "Variações": "Sem variações no retorno",
    },
    {
        "Produto": "Perfume B",
        "Categoria": "Não informada",
        "Código interno": "Não informado",
        "Produto (ID)": "102",
        "Saldo GET (unidade do ERP não conferida)": "0",
        "Vendas fechadas observadas (UN)": "Não observado no período",
        "Variações": "Conferir unidades/variações",
    },
]
_mostrar_detalhe_produto(linhas, "416023")
"""


def test_mobile_exibe_saldo_e_vendas_em_destaque_sem_scroll_horizontal():
    app = AppTest.from_string(SCRIPT, default_timeout=30).run()
    assert not app.exception
    assert len(app.selectbox) == 1
    assert len(app.metric) == 2
    assert app.metric[0].value == "12"
    assert app.metric[1].value == "75"


def test_mobile_ausencia_de_venda_nao_vira_zero_e_saldo_zero_e_preservado():
    script = SCRIPT.replace(
        '_mostrar_detalhe_produto(linhas, "416023")',
        '_mostrar_detalhe_produto(linhas[1:], "416023")',
    )
    app = AppTest.from_string(script, default_timeout=30).run()
    assert not app.exception
    assert app.metric[0].value == "0"
    assert app.metric[1].value == "—"


def test_mobile_sem_correspondencia_nao_cria_selecao():
    app = AppTest.from_string(
        'from web.real_batch_replenishment_page import _mostrar_detalhe_produto\n'
        '_mostrar_detalhe_produto([], "416023")', default_timeout=30,
    ).run()
    assert not app.exception
    assert len(app.selectbox) == 0
    assert len(app.metric) == 0


def test_mobile_nao_aceita_duplicidade_de_ids_antes_de_exibir():
    iguais = [
        {"Produto (ID)": "101"},
        {"Produto (ID)": "101"},
    ]
    with pytest.raises(ContratoInvalido, match="repetidos"):
        _mostrar_detalhe_produto(iguais, "416023")
