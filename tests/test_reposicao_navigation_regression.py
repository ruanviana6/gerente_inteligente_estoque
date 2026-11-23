"""Reproduz o erro de produção: alterar _pagina depois de instanciar st.radio."""

from streamlit.testing.v1 import AppTest

SCRIPT = """
import streamlit as st
from web.real_manual_replenishment_page import avisar_identificacao_incompleta

st.radio("Navegação", ["Reposição Inteligente", "Estoque"], key="_pagina")
if st.session_state["_pagina"] == "Reposição Inteligente":
    avisar_identificacao_incompleta(
        {"rows": [{"Produto": "Não informado", "Categoria": "Não informada"}]},
        "416023",
    )
"""


def test_botao_reposicao_troca_pagina_sem_streamlit_widget_already_instantiated():
    """O callback muda o rádio somente antes da execução do script seguinte."""
    app = AppTest.from_string(SCRIPT, default_timeout=30).run()
    assert not app.exception
    assert app.radio(key="_pagina").value == "Reposição Inteligente"

    app.button(key="repor_abrir_estoque_atualizacao_416023").click().run()
    assert not app.exception
    assert app.radio(key="_pagina").value == "Estoque"

    app.run()
    assert not app.exception
    assert app.radio(key="_pagina").value == "Estoque"


def test_categoria_vazia_so_informa_sem_botao_de_navegacao():
    """Categoria ausente não inviabiliza produto com nome conhecido."""
    script = SCRIPT.replace('"Produto": "Não informado"', '"Produto": "Perfume Teste"')
    app = AppTest.from_string(script, default_timeout=30).run()
    assert not app.exception
    assert not app.button
    assert "sem categoria" in " ".join(x.message for x in app.info)
