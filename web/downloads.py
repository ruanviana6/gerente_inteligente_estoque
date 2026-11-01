"""Entrega CSV autorizado pela conexão da página, sem arquivo remoto compartilhado.

O primeiro clique gera os bytes com as verificações do serviço. O segundo salva
o conteúdo já entregue ao navegador em um link data:, sem registrar arquivo no
MediaFileManager do Streamlit. Nenhum CSV ou callback permanece em session_state.
"""
from __future__ import annotations

import base64
from typing import Any

import streamlit as st
from streamlit.components.v1 import html

EXPORTACOES = {
    "replenishment": ("Preparar CSV de reposição", "reposicao.csv"),
    "monthly": ("Preparar CSV do relatório", "relatorio_mensal.csv"),
}

_LINK = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><style>
body {margin:0;background:#101720;color:#F5F7FA;font-family:system-ui,sans-serif;}
a {display:inline-flex;align-items:center;justify-content:center;min-height:44px;
padding:0 1rem;background:#B55440;color:#fff;border-radius:.65rem;text-decoration:none;}
a:focus-visible {outline:2px solid #9FC7FF;outline-offset:3px;}
</style></head><body>
<a download="{arquivo}" href="data:text/csv;charset=utf-8;base64,{conteudo}">Salvar CSV</a>
</body></html>"""


def csv_export(servico: Any, tipo: str, parametros: dict, contexto: Any) -> None:
    """Gera somente após ação explícita e entrega bytes sem endpoint reutilizável.

Os únicos valores interpolados são o nome fixo da allowlist e base64 padrão.
Nomes de produtos, fornecedores ou pessoas nunca entram como marcação HTML.
Os dados já entregues ao navegador equivalem a um arquivo baixado: a revogação
impede a próxima geração, mas não pode apagar cópias que o usuário já recebeu.
"""
    if tipo not in EXPORTACOES:
        raise ValueError("Tipo de exportação não reconhecido.")
    rotulo, arquivo = EXPORTACOES[tipo]
    if not st.button(rotulo, key="preparar_csv_" + tipo, type="primary"):
        return
    with st.spinner("Confirmando permissões e preparando o CSV…"):
        conteudo = servico.deferred_export(tipo, parametros, context=contexto)()
    if not isinstance(conteudo, bytes):
        raise TypeError("A exportação precisa produzir um arquivo CSV em bytes.")
    codificado = base64.b64encode(conteudo).decode("ascii")
    # replace evita interpretar as chaves do CSS. Base64 não contém caracteres
    # capazes de fechar atributos HTML e o nome de arquivo vem da allowlist.
    documento = _LINK.replace("{arquivo}", arquivo).replace("{conteudo}", codificado)
    st.success("Arquivo pronto para salvar. A autorização foi conferida nesta geração.")
    html(documento, height=60, scrolling=False)
    st.caption("O link contém o arquivo já entregue a esta página. Uma nova geração exige nova autorização.")
