"""Entrada exclusiva do navegador de teste fiscal, sem API ou login alternativo produtivo."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(RAIZ), str(RAIZ / 'tests')]


def executar():
    """Injeta o gateway somente nesta fixture, nunca em app.py ou Secrets."""
    if os.environ.get('GERENTE_FISCAL_BROWSER_FIXTURE') != '1':
        raise RuntimeError('Entrada exclusiva de testes fictícios.')
    import streamlit as st
    from test_fiscal import AuthFiscal, GatewayFiscalDemo

    import web.fiscal_page
    from web.theme import apply_theme
    st.set_page_config(page_title='Flatter DEMO fiscal', layout='wide')
    apply_theme()
    if '_gateway' not in st.session_state:
        st.session_state['_gateway'] = GatewayFiscalDemo(AuthFiscal())
    gateway = st.session_state['_gateway']
    web.fiscal_page.FiscalService = lambda auth: gateway
    web.fiscal_page.fiscal(SimpleNamespace(auth=gateway.auth), gateway.auth.require('fiscal.view'))
    st.caption(f'Controle fictício: execuções={gateway.executions}; solicitações={len(gateway.requests)}')


if __name__ == '__main__':
    executar()
