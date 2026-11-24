"""Releitura GET de uma auditoria existente para detectar alterações de vendas.

Uma referência dura até 15 minutos apenas na sessão autenticada do Master.
Não se guarda cliente, número comercial ou dado fiscal em session_state.
"""
from __future__ import annotations

import time

import streamlit as st

from analytics.sales_window_recheck import assinar_janela, comparar_janelas
from integracoes.account_setup import validar_conta
from integracoes.diagnostico import _stamp
from integracoes.gestaoclick import ContratoInvalido, GestaoClickError
from integracoes.sales_audit import AuditoriaVendas, VendaIntegralInvalida, consultar_vendas_integrais
from web.errors import AppError, AuthenticationError, AuthorizationError

_JANELA_KEY = "_vendas_revalidacao_efemera"
_TTL_SECONDS = 15 * 60


def guardar_referencia_vendas(auth, loja_id: str, source_id: str,
                              auditoria: AuditoriaVendas) -> None:
    """Guarda apenas identidades e conteúdo resumidos, vinculados à autorização."""
    referencia = assinar_janela(auditoria)
    if referencia.loja_id != loja_id:
        raise ContratoInvalido("A auditoria não pertence à loja selecionada.")
    st.session_state[_JANELA_KEY] = {
        "stamp": _stamp(auth), "loja_id": loja_id, "source_id": source_id,
        "expira": time.monotonic() + _TTL_SECONDS, "referencia": referencia,
    }


def _referencia_valida(auth, loja_id: str):
    entrada = st.session_state.get(_JANELA_KEY)
    if entrada is None:
        return None
    if (not isinstance(entrada, dict) or entrada.get("loja_id") != loja_id
            or entrada.get("stamp") != _stamp(auth)
            or time.monotonic() >= entrada.get("expira", 0)):
        st.session_state.pop(_JANELA_KEY, None)
        return None
    return entrada


def revalidar_vendas_controls(
    servico, carregar_configuracao, conta, loja_id: str, fonte_da_loja,
) -> None:
    """Somente um novo clique autorizado faz GET; nunca cruza lojas ou contas."""
    auth = servico.auth
    entrada = _referencia_valida(auth, loja_id)
    if entrada is None:
        return
    referencia = entrada["referencia"]
    with st.container(border=True):
        st.markdown("**Comparar novamente as vendas desta janela**")
        st.caption(
            f"Referência da loja {loja_id}: {referencia.inicio:%d/%m/%Y} "
            f"a {referencia.fim:%d/%m/%Y} · "
            f"{referencia.documentos} documento(s). "
            "Somente hashes efêmeros de pedidos e itens ficam na sessão, por até 15 minutos."
        )
        with st.form(f"revalidar_vendas_get_{loja_id}"):
            autoriza = st.checkbox(
                "Autorizo uma SEGUNDA leitura GET das vendas da mesma loja e período "
                "para conferir mudanças, sem alterar o ERP ou gerar pedidos."
            )
            executar = st.form_submit_button("Reconsultar e comparar vendas")
        if not executar:
            return
        if not autoriza:
            st.warning("É preciso autorizar explicitamente a nova consulta GET.")
            return
        try:
            with st.spinner("Reconsultando a mesma janela de vendas, sem modificar o ERP…"):
                with auth.operation():
                    validar_conta(auth, conta)
                    if entrada["stamp"] != _stamp(auth):
                        raise AuthorizationError("A sessão mudou desde a referência.")
                    if fonte_da_loja(auth, loja_id) != entrada["source_id"]:
                        raise AuthorizationError("A fonte desta loja mudou desde a referência.")
                    nova = consultar_vendas_integrais(
                        auth, carregar_configuracao, conta, loja_id,
                        referencia.inicio, referencia.fim, autorizado=True,
                    )
                    validar_conta(auth, conta)
                    if (entrada["stamp"] != _stamp(auth)
                            or time.monotonic() >= entrada["expira"]):
                        raise AuthorizationError("A referência expirou durante a consulta.")
                    segundo = assinar_janela(nova, sal=referencia.sal)
                    diferencas = comparar_janelas(referencia, segundo)
        except (AuthenticationError, AuthorizationError):
            st.session_state.pop(_JANELA_KEY, None)
            raise
        except (AppError, GestaoClickError, ValueError, TypeError):
            st.warning(
                "Não foi possível comparar integralmente esta janela. "
                "Nenhum dado foi alterado e a consulta não liberou reposição."
            )
            return
        st.caption(
            f"Documentos na primeira leitura: {diferencas.documentos_antes}; "
            f"na segunda: {diferencas.documentos_depois}."
        )
        a, b = st.columns(2)
        a.metric("Apareceram na segunda leitura", diferencas.novos_na_leitura)
        b.metric("Não localizados na segunda", diferencas.nao_localizados_na_leitura)
        c, d, e = st.columns(3)
        c.metric("Situação diferente", diferencas.situacoes_diferentes)
        d.metric("Itens/quantidades diferentes", diferencas.itens_diferentes)
        e.metric("Data diferente", diferencas.datas_diferentes)
        if any((
            diferencas.novos_na_leitura, diferencas.nao_localizados_na_leitura,
            diferencas.situacoes_diferentes, diferencas.itens_diferentes,
            diferencas.datas_diferentes,
        )):
            st.warning(
                "Foram identificadas divergências entre leituras. Um pedido que deixou "
                "de aparecer NÃO comprova cancelamento/exclusão: pode ter mudado de "
                "data, permissão ou visibilidade. Confira no GestãoClick."
            )
        else:
            st.info(
                "As duas consultas coincidiram para esta janela. Isso NÃO comprova "
                "cobertura histórica, ausência de devoluções ou estabilidade futura."
            )
        st.caption(
            "Esta comparação não identifica devoluções, não comprova IDs de linhas "
            "ou horários de atualização, não soma períodos e NÃO alimenta a "
            "Reposição Inteligente automaticamente."
        )
