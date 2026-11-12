"""Entrada Flatter e ciclo de sessão; páginas, cálculos e autorização separados.

Cada execução consulta o acesso atual. O estado de apresentação não guarda
catálogos ou relatórios, e nenhum cliente ou token pertence a um cache global.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import streamlit as st

from web.browser_session import STATE_KEYS, incoming_ticket, render_browser_cookie, set_browser_cookie
from web.commercial_page import comercial
from web.errors import AppError, AuthenticationError, AuthorizationError, ConfigurationError
from web.fiscal_page import fiscal
from web.pages import ADMIN_PERMISSIONS, PAGES, _administracao
from web.release import RELEASE
from web.telemetry import measure, performance_scope
from web.theme import apply_theme, brand_header, page_header, surface


def _perfil(contexto: Any, campo: str) -> Any:
    """Lê somente o perfil protegido já validado pelo serviço."""
    perfil = contexto.profile
    return perfil.get(campo) if isinstance(perfil, Mapping) else getattr(perfil, campo)


def _assinatura(contexto: Any) -> tuple:
    """Detecta mudanças de identidade, organização, papel e permissões."""
    return (_perfil(contexto, "user_id"), _perfil(contexto, "organization_id"),
            _perfil(contexto, "role"), tuple(sorted(contexto.permissions)))


def _limpar_apresentacao(servico: Any) -> None:
    """Descarta widgets e seleções sem substituir a autenticação desta sessão."""
    for chave in list(st.session_state):
        if st.session_state[chave] is not servico and chave not in STATE_KEYS:
            del st.session_state[chave]


def _encerrar_sessao(servico: Any) -> None:
    """Revoga a sessão e sempre remove o estado local, mesmo sem rede."""
    aviso = None
    try:
        servico.auth.logout()
    except AppError:
        aviso = "Acesso encerrado neste navegador. Não foi possível confirmar a revogação remota; confira sua conexão."
    finally:
        st.session_state.clear()
        set_browser_cookie(None)
        if aviso:
            st.session_state["_auth_notice"] = aviso


@st.fragment(run_every="20s")
def _vigiar_autorizacao(servico: Any, assinatura: tuple, execucao: int) -> None:
    """Revalida telas abertas; a chamada inicial já foi validada pelo rerun.

    O identificador é apenas um contador local de renderização. Nas chamadas
    periódicas um escopo novo força Auth e app_context, sem recarregar o estoque.
    """
    if st.session_state.get("_watch_execution") != execucao:
        st.session_state["_watch_execution"] = execucao
        return
    try:
        with performance_scope("watcher"), servico.execution():
            contexto = servico.context()
    except (AuthenticationError, AuthorizationError):
        _encerrar_sessao(servico)
        st.session_state["_auth_notice"] = "Sua sessão não está autorizada. Entre novamente ou procure o Master."
        st.rerun(scope="app")
    except AppError:
        # Uma falha de confirmação provoca rerun completo: conteúdo antigo é
        # removido e a entrada só volta a desenhar dados após confirmar o acesso.
        st.rerun(scope="app")
    if _assinatura(contexto) != assinatura:
        _limpar_apresentacao(servico)
        st.rerun(scope="app")


def _recuperacao(servico: Any) -> None:
    """Oferece o fluxo por código somente quando o proprietário habilitou e-mail."""
    configuracao = getattr(getattr(servico.auth, "api", None), "settings", None)
    if not getattr(configuracao, "auth_email_enabled", False):
        st.caption("Precisa de acesso? Procure o responsável Master.")
        return
    with st.expander("Recuperar acesso"):
        with st.form("solicitar_recuperacao", clear_on_submit=True):
            email = st.text_input("E-mail da conta", max_chars=254)
            solicitar = st.form_submit_button("Solicitar código de recuperação")
        if solicitar:
            try:
                servico.auth.request_password_reset(email)
                st.info("Se a conta e o envio estiverem habilitados, você receberá as instruções.")
            except AppError:
                st.info("A recuperação não está disponível. Solicite orientação ao Master.")
        with st.form("confirmar_recuperacao", clear_on_submit=True):
            email_codigo = st.text_input("E-mail para validar o código", max_chars=254)
            codigo = st.text_input("Código recebido", type="password", max_chars=128)
            senha = st.text_input("Nova senha", type="password", autocomplete="new-password")
            confirmar = st.form_submit_button("Definir nova senha")
        if confirmar:
            try:
                servico.auth.recover_password(email_codigo, codigo, senha)
                st.success("Senha atualizada. Entre novamente com a nova senha.")
            except AppError:
                st.error("Não foi possível recuperar o acesso. Confira o código e a nova senha.")


def _login(servico: Any) -> None:
    """Apresenta acesso restrito por e-mail e senha, sem cadastro público."""
    with surface("login"):
        brand_header()
        page_header("Gerente Inteligente de Estoque", "Acompanhe o estoque e decida com dados.")
        st.subheader("Acesse sua conta")
        st.caption("Somente usuários autorizados pelo Master podem acessar os dados.")
        aviso = st.session_state.pop("_auth_notice", None)
        if aviso:
            st.warning(aviso)
        with st.form("login", clear_on_submit=True):
            email = st.text_input("E-mail", max_chars=254, autocomplete="email")
            senha = st.text_input("Senha", type="password", autocomplete="current-password")
            lembrar = st.checkbox("Manter acesso neste navegador por até 30 dias", value=True)
            st.caption("Use esta opção apenas em um dispositivo pessoal. Sair encerra o acesso.")
            entrar = st.form_submit_button("Entrar", type="primary", width="stretch")
        if entrar:
            try:
                with st.spinner("Confirmando acesso…"):
                    contexto = servico.auth.login(email, senha)
                _limpar_apresentacao(servico)
                if lembrar and contexto is not None and hasattr(servico.auth, "remember_browser"):
                    try:
                        set_browser_cookie(servico.auth.remember_browser())
                    except AppError:
                        st.session_state["_persistence_notice"] = "Você entrou, mas não foi possível lembrar este navegador. Seu acesso nesta aba continua válido."
                else:
                    set_browser_cookie(None)
                st.rerun()
            except AppError:
                st.error("Não foi possível entrar. Confira os dados e se sua conta está autorizada.")
        _recuperacao(servico)


def _conta(servico: Any, contexto: Any) -> None:
    """Exibe o perfil real e ações de conta sem revelar dados da autenticação."""
    st.subheader(str(_perfil(contexto, "display_name")))
    st.caption(str(_perfil(contexto, "role")))
    if st.button("Sair", width="stretch"):
        _encerrar_sessao(servico)
        st.rerun()
    with st.expander("Alterar senha"):
        with st.form("alterar_senha", clear_on_submit=True):
            nova = st.text_input("Nova senha da conta", type="password", autocomplete="new-password")
            st.caption("Use 12 a 128 caracteres, com letras, números e símbolos.")
            alterar = st.form_submit_button("Atualizar senha")
        if alterar:
            try:
                servico.auth.change_password(nova)
                _limpar_apresentacao(servico)
                set_browser_cookie(None)
                st.session_state["_auth_notice"] = "Senha atualizada. Entre novamente para continuar."
                st.rerun()
            except AppError:
                st.error("Não foi possível alterar a senha. Verifique os requisitos e tente novamente.")


def _primeiro_acesso(servico: Any) -> None:
    """Restringe a sessão temporária à troca de senha antes de apresentar os dados."""
    with surface("primeiro_acesso"):
        brand_header()
        page_header("Defina sua senha", "O acesso aos dados será liberado após a troca.")
        try:
            estado = servico.auth.password_gate_status()
        except AppError:
            st.error("Não foi possível confirmar o acesso temporário. Procure o Master.")
            return
        if estado["expired"] or not estado["ready"]:
            st.warning("O acesso temporário expirou ou ainda não está pronto. Procure o Master.")
        else:
            with st.form("primeiro_acesso_senha", clear_on_submit=True):
                senha = st.text_input("Nova senha pessoal", type="password", autocomplete="new-password")
                confirmacao = st.text_input("Confirme a nova senha", type="password", autocomplete="new-password")
                st.caption("Use 12 a 128 caracteres, com letras, números e símbolos.")
                if st.form_submit_button("Definir minha senha", type="primary"):
                    if senha != confirmacao:
                        st.error("As senhas informadas não coincidem.")
                    else:
                        try:
                            servico.auth.finish_initial_password(senha)
                            _limpar_apresentacao(servico)
                            st.session_state["_auth_notice"] = "Senha definida. Entre novamente para continuar."
                            st.rerun()
                        except AppError:
                            st.error("Não foi possível definir a senha. Confira os requisitos ou procure o Master.")
        if st.button("Sair do acesso temporário"):
            _encerrar_sessao(servico)
            st.rerun()


def _area_autenticada(servico: Any) -> None:
    """Autoriza cada renderização antes de montar menus ou consultar dados."""
    if getattr(servico.auth, "password_change_required", False):
        _primeiro_acesso(servico)
        return
    try:
        contexto = servico.context()
    except (AuthenticationError, AuthorizationError):
        _encerrar_sessao(servico)
        st.session_state["_auth_notice"] = "Sua sessão não está autorizada. Entre novamente ou procure o Master."
        _login(servico)
        return
    except AppError:
        st.error("Não foi possível confirmar seu acesso. Tente novamente em instantes.")
        if st.button("Tentar novamente"):
            st.rerun()
        return
    assinatura = _assinatura(contexto)
    anterior = st.session_state.get("_access_signature")
    if anterior is not None and anterior != assinatura:
        _limpar_apresentacao(servico)
    st.session_state["_access_signature"] = assinatura
    execucao = st.session_state.get("_render_execution", 0) + 1
    st.session_state["_render_execution"] = execucao
    permitidas = {nome: handler for nome, (permissao, handler) in PAGES.items() if contexto.can(permissao)}
    if contexto.can("decisions.write") and "Reposição Inteligente" in PAGES:
        permitidas["Reposição Inteligente"] = PAGES["Reposição Inteligente"][1]
    if (contexto.can("trends.view") or contexto.can("commercial.view")) and "Inteligência Comercial" not in permitidas:
        permitidas["Inteligência Comercial"] = comercial
    if contexto.can("fiscal.view"):
        permitidas["Automação / Notas Fiscais"] = fiscal
    ordem = ["Visão Geral", "Estoque", "Reposição Inteligente", "Inteligência Comercial", "Automação / Notas Fiscais", "Relatórios"]
    permitidas = {nome: permitidas[nome] for nome in ordem if nome in permitidas}
    if _perfil(contexto, "role") == "Master" or any(contexto.can(p) for p in ADMIN_PERMISSIONS):
        permitidas["Administração"] = _administracao
    if st.session_state.get("_pagina") == "Painel":
        st.session_state["_pagina"] = "Visão Geral"
    anterior = st.session_state.get("_pagina")
    destinos_antigos = {"Importação": "Importação manual / DEMO", "Regras de Reposição": "Regras de Reposição",
                       "Auditoria": "Auditoria", "Integrações": "Integrações"}
    if anterior in destinos_antigos and "Administração" in permitidas:
        st.session_state["admin_area"] = destinos_antigos[anterior]
        st.session_state["_pagina"] = "Administração"
    elif anterior == "Acompanhamento de Ações" and "Reposição Inteligente" in permitidas:
        st.session_state["reposicao_area"] = "Acompanhamento de Ações"
        st.session_state["_pagina"] = "Reposição Inteligente"
    with st.sidebar, st.container(key="flatter_sidebar"):
        brand_header(compact=True)
        if permitidas:
            if st.session_state.get("_pagina") not in permitidas:
                st.session_state["_pagina"] = next(iter(permitidas))
            with st.container(key="flatter_navigation"):
                pagina = st.radio("Navegação", list(permitidas), key="_pagina")
        st.divider()
        _conta(servico, contexto)
    _vigiar_autorizacao(servico, assinatura, execucao)
    if not permitidas:
        st.info("Sua conta está ativa, mas nenhuma funcionalidade foi liberada. Solicite acesso ao Master.")
        return
    try:
        with measure("render"):
            permitidas[pagina](servico, contexto)
    except (AuthenticationError, AuthorizationError):
        # Remove inclusive conteúdo desenhado antes de uma revogação concorrente.
        _encerrar_sessao(servico)
        st.session_state["_auth_notice"] = "Acesso negado. Sua sessão ou suas permissões mudaram. Entre novamente."
        st.rerun()
    except ValueError as erro:
        st.warning(str(erro))
    except AppError:
        st.error("Não foi possível concluir a operação. Confira a configuração e tente novamente.")


def run_app(service_factory: Callable | None = None) -> None:
    """Monta a aplicação única, com fábrica injetável somente para testes isolados."""
    st.set_page_config(page_title="Flatter | Gerente Inteligente de Estoque", layout="wide")
    with performance_scope("rerun"), measure("rerun"):
        with measure("bootstrap"):
            apply_theme()
            st.html(f'<span hidden data-flatter-release="{RELEASE}"></span>')
            render_browser_cookie()
            if service_factory is None:
                from web.service import create_service

                service_factory = create_service
            try:
                servico = service_factory(st.session_state)
            except ConfigurationError:
                brand_header()
                st.info("Configuração pendente. Defina SUPABASE_URL e SUPABASE_PUBLISHABLE_KEY nos Secrets do aplicativo.")
                st.caption("Consulte o guia de publicação do repositório. Não informe credenciais no chat ou no GitHub.")
                return
        with st.container(key="flatter_app"), servico.execution():
            if servico.auth.session is None and hasattr(servico.auth, "restore_browser"):
                try:
                    ticket = incoming_ticket()
                    if ticket:
                        with st.spinner("Retomando seu acesso…"):
                            servico.auth.restore_browser(ticket)
                except (AuthenticationError, AuthorizationError):
                    set_browser_cookie(None)
                    render_browser_cookie()
                    st.session_state["_auth_notice"] = "Seu acesso lembrado expirou ou foi encerrado. Entre novamente."
                except AppError:
                    st.warning("Não foi possível retomar o acesso agora. Tente novamente em instantes.")
                    if st.button("Tentar retomar o acesso"):
                        st.session_state.pop("_restore_attempted", None)
                        st.rerun()
                    return
            aviso = st.session_state.pop("_persistence_notice", None)
            if aviso:
                st.warning(aviso)
            if servico.auth.session is None:
                _login(servico)
            else:
                _area_autenticada(servico)
