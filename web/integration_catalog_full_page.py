"""Conferência integral e salvamento explícito de fotografia analítica do GestãoClick."""
from __future__ import annotations

import time

import streamlit as st

from integracoes.account_setup import registrar_fonte, validar_conta
from integracoes.catalog_full import PaginacaoCatalogoInvalida, consultar_catalogo_completo
from integracoes.gestaoclick import GestaoClickError
from web.errors import AppError, AuthenticationError, AuthorizationError
from web.sales_service import access_stamp

_KEY = "_gc_catalogo_completo"
_TTL_SECONDS = 300


def _assinatura(auth):
    return access_stamp(auth.require("integrations.manage"), auth.generation)


def catalog_full_controls(servico, carregar_configuracao, conta, loja_id: str) -> None:
    """Um clique lê todas as páginas; outro, separado, autoriza somente histórico no banco."""
    auth = servico.auth
    with st.expander("Conferir catálogo completo e histórico de estoque da loja"):
        st.caption(
            "Consulta todas as páginas de produtos desta loja (até 1.000 itens). "
            "Confere contagens, duplicidades e continuidade da paginação. "
            "Não baixa, não corrige nem altera o estoque do GestãoClick."
        )
        with st.form("gc_catalogo_total_consulta"):
            autorizada = st.checkbox("Autorizo consultar todas as páginas do catálogo desta loja")
            consultar = st.form_submit_button("Conferir catálogo completo")
        if consultar:
            st.session_state.pop(_KEY, None)
            if not autorizada:
                st.warning("É preciso autorizar a consulta completa.")
                return
            try:
                with st.spinner("Lendo e conciliando as páginas do catálogo…"):
                    catalogo = consultar_catalogo_completo(
                        auth, carregar_configuracao, conta, loja_id, autorizado=True,
                    )
                validar_conta(auth, conta)
                st.session_state[_KEY] = {
                    "stamp": _assinatura(auth), "loja": loja_id,
                    "expira": time.monotonic() + _TTL_SECONDS, "catalogo": catalogo,
                }
            except (AuthenticationError, AuthorizationError):
                raise
            except GestaoClickError as erro:
                st.warning(str(erro))
                if isinstance(erro, PaginacaoCatalogoInvalida):
                    st.caption("Diagnóstico técnico sem dados empresariais.")
                    st.json(erro.diagnostico, expanded=False)
                return

        previa = st.session_state.get(_KEY)
        if previa is None:
            return
        if (previa["stamp"] != _assinatura(auth) or previa["loja"] != loja_id
                or time.monotonic() > previa["expira"]):
            st.session_state.pop(_KEY, None)
            st.info("A conferência expirou ou mudou de sessão/loja. Consulte novamente.")
            return
        validar_conta(auth, conta)
        catalogo = previa["catalogo"]

        st.success(
            f"Todas as {catalogo.paginas} página(s) conferidas: "
            f"{len(catalogo.produtos)} de {catalogo.total_informado} produtos. "
            "Nenhum estoque foi importado."
        )
        st.caption(
            f"Loja solicitada: {catalogo.loja_solicitada}. "
            f"Soma dos saldos conhecidos: "
            f"{format(catalogo.soma_estoque, 'f') if catalogo.soma_estoque is not None else 'não confirmada'}. "
            "A soma é somente desta loja, segundo o escopo retornado pela API."
        )
        if catalogo.continuacoes_sem_loja:
            st.warning(
                f"{catalogo.continuacoes_sem_loja} URL(s) de continuação não trouxeram "
                "o filtro da loja. O sistema consultou cada página usando "
                "a loja selecionada explicitamente, sem seguir essas URLs. "
                "Confira a loja e a abrangência no ERP antes de salvar a fotografia."
            )
        pendencias = (
            catalogo.sem_estoque + catalogo.sem_codigo + catalogo.codigos_repetidos
            + catalogo.variacoes_sem_id + catalogo.variacoes_sem_codigo
        )
        if pendencias:
            st.warning(
                f"Pendências para conciliar com o ERP: "
                f"{catalogo.sem_estoque} produtos sem saldo; "
                f"{catalogo.sem_codigo} sem código interno; "
                f"{catalogo.codigos_repetidos} códigos internos repetidos; "
                f"{catalogo.variacoes_sem_id} linhas de variação sem ID; "
                f"{catalogo.variacoes_sem_codigo} linhas sem código de variação. "
                "Essas informações não serão usadas no motor de estoque."
            )
        linhas = [{
            "Produto (ID)": p.id,
            "Código interno": p.codigo_interno or "Não informado",
            "Estoque desta loja": format(p.estoque, "f") if p.estoque is not None else "Não informado",
            "Variações sem ID": sum(v.id is None for v in p.variacoes),
        } for p in catalogo.produtos[:20]]
        st.dataframe(linhas, hide_index=True, width="stretch")
        st.caption(
            "A tabela mostra até 20 produtos da consulta integral, sem preços ou clientes. "
            "Compare a contagem, a loja e os saldos com o GestãoClick antes de confirmar. "
            "O HTTP 200 não comprova, sozinho, que a chave enxerga o estoque da empresa inteira."
        )

        if catalogo.sem_estoque or not catalogo.produtos:
            st.warning("Há produtos sem saldo informado ou catálogo vazio. "
                       "Nenhuma fotografia pode ser salva.")
            return
        with st.form("gc_catalogo_total_confirmacao"):
            conferido = st.checkbox(
                "Conferi no GestãoClick a loja, os produtos e a abrangência do catálogo"
            )
            autorizado = st.checkbox(
                "Autorizo guardar somente esta fotografia analítica no Supabase, "
                "sem atualizar o motor ou alterar o ERP"
            )
            gravar = st.form_submit_button("Salvar fotografia analítica no Supabase")
        if not gravar:
            return
        if not conferido or not autorizado:
            st.warning("A gravação exige as duas confirmações explícitas.")
            return
        try:
            with auth.operation():
                if (previa["stamp"] != _assinatura(auth) or previa["loja"] != loja_id
                        or time.monotonic() > previa["expira"]):
                    raise AuthorizationError("Sessão ou conferência expirada. Consulte novamente.")
                validar_conta(auth, conta)
                fonte_id = registrar_fonte(auth, conta, loja_id)
                resultado = auth.rpc("catalog_stock_commit", {
                    "p_source_id": fonte_id, "p_store_id": loja_id,
                    "p_expected_rows": catalogo.total_informado,
                    "p_expected_stock": format(catalogo.soma_estoque, "f"),
                    "p_rows": catalogo.linhas_snapshot(),
                    "p_confirmed": True,
                }, "integrations.manage")
                validar_conta(auth, conta)
                if (previa["stamp"] != _assinatura(auth)
                        or not isinstance(resultado, dict)
                        or resultado.get("motor_atualizado") is not False
                        or resultado.get("product_count") != catalogo.total_informado):
                    raise AuthorizationError("A gravação não pôde ser validada nesta sessão.")
            st.session_state.pop(_KEY, None)
            st.success(
                "Fotografia analítica salva de forma idempotente no Supabase. "
                "O motor de estoque e o GestãoClick não foram alterados."
            )
            st.caption(
                f"Produtos: {resultado['product_count']}. "
                f"Execução repetida: {'sim' if resultado.get('reused') else 'não'}."
            )
        except (AuthenticationError, AuthorizationError):
            raise
        except (AppError, GestaoClickError):
            st.warning(
                "Não foi possível salvar a fotografia interna. Confira as migrações "
                "do Supabase, a fonte autorizada e o estado da sessão. "
                "Nenhum saldo foi promovido ao motor."
            )
