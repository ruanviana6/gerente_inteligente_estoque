"""Sincronização manual Master: prévia separada da confirmação de persistência."""
from __future__ import annotations

import os
from datetime import timedelta
from uuid import uuid4

import streamlit as st

from analytics.demo import gerar_vendas_ficticias
from dados import hoje_local
from sincronizacao.service import SyncCoordinator
from web.errors import AuthorizationError
from web.sales_service import access_stamp
from web.theme import page_header


def _secrets():
    """Lê configuração protegida só no servidor, sem imprimir valores."""
    valores = {k: v for k, v in os.environ.items() if k.startswith("GESTAOCLICK_")}
    try:
        valores.update({k: v for k, v in st.secrets.items() if k.startswith("GESTAOCLICK_")})
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        pass
    return valores


def integracoes(servico, contexto):
    """Exige Master em cada ação e não oferece edição de credenciais pelo navegador."""
    atual = servico.auth.require("integrations.manage")
    if atual.profile.role != "Master":
        raise AuthorizationError("Somente o Master gerencia sincronizações.")
    page_header("Integrações", "GestãoClick → cópia analítica. Exclusivamente leitura no ERP.")
    st.info("A sincronização é manual. Preparar uma prévia não altera as vendas analíticas consolidadas. "
            "Nenhum fluxo realiza baixa ou outra escrita no GestãoClick.")
    assinatura = access_stamp(atual, servico.auth.generation)
    previa = st.session_state.get("_sync_preview")
    if previa and previa["signature"] != assinatura:
        del st.session_state["_sync_preview"]
        previa = None
    with st.form("preparar_sincronizacao"):
        datas = st.date_input("Período da consulta", (hoje_local().date() - timedelta(days=59), hoje_local().date()),
                             max_value=hoje_local().date())
        ficticia = st.checkbox("Usar somente dados fictícios identificados como DEMO", value=True)
        autorizado = st.checkbox("Confirmo autorização da empresa, contrato e permissões de leitura (consulta real)")
        preparar = st.form_submit_button("Preparar prévia", type="primary")
    coordenador = SyncCoordinator(servico.auth)
    if preparar:
        if len(datas) != 2 or not 0 <= (datas[1] - datas[0]).days <= 365:
            st.warning("Escolha um período de até 366 dias.")
            return
        chave = str(uuid4())
        # A chave de repetição não muda em uma tentativa interrompida do mesmo escopo.
        escopo = (tuple(str(d) for d in datas), ficticia)
        tentativa = st.session_state.get("_sync_attempt")
        if tentativa and tentativa["scope"] == escopo:
            chave = tentativa["key"]
        st.session_state["_sync_attempt"] = {"scope": escopo, "key": chave}
        try:
            if ficticia:
                fonte = servico.auth.rpc("integration_register_source", {
                    "p_key": "demo-vendas", "p_name": "DEMO: vendas fictícias", "p_stores": ["DEMO"]},
                    "integrations.manage")
                fonte_id = fonte["id"]
                loja = "DEMO"
                def leitor():
                    """Gera somente a amostra explicitamente selecionada pelo Master."""
                    return gerar_vendas_ficticias(fonte_id, loja, *datas)
            else:
                if not autorizado:
                    st.warning("A consulta real exige autorização explícita e contrato conferido.")
                    return
                from integracoes.runtime import build_reader

                reader = build_reader(_secrets())
                fonte_id, loja = reader.config.source_id, reader.config.store_id
                def leitor():
                    """Consulta o contrato protegido, sem receber rotas do navegador."""
                    return reader.read(*datas)
            with st.spinner("Consultando, validando e preparando lote interno…"):
                resultado = coordenador.prepare(source_id=fonte_id, store_id=loja,
                    request_key=chave, start=datas[0], end=datas[1], read_all=leitor)
            if resultado["status"] == "completed":
                st.success("Esta execução já foi consolidada. Nenhuma duplicação realizada.")
            else:
                st.session_state["_sync_preview"] = {"signature": assinatura,
                    "id": resultado["id"], "claim_token": resultado["claim_token"]}
                previa = st.session_state["_sync_preview"]
        except (ValueError, RuntimeError) as erro:
            st.warning(str(erro))
            return
        except Exception as erro:
            from integracoes.gestaoclick import GestaoClickError

            if isinstance(erro, GestaoClickError):
                st.warning(str(erro))
                return
            raise
    if previa:
        controle = {"p_run_id": previa["id"], "p_claim_token": previa["claim_token"]}
        resumo = servico.auth.rpc("sync_preview", controle, "integrations.manage")
        final = servico.auth.require("integrations.manage")
        if assinatura != access_stamp(final, servico.auth.generation):
            raise AuthorizationError("O acesso mudou durante a prévia.")
        st.subheader("Conferir prévia")
        st.write({k: resumo.get(k) for k in ("status", "row_count", "net_quantity", "changed_rows",
                                            "period_start", "period_end", "checkpoint")})
        st.caption("A contagem reconciliada comprova o lote recebido; não prova que o ERP liberou todos os registros.")
        st.dataframe(resumo.get("sample", []), hide_index=True, width="stretch")
        confirmado = st.checkbox("Conferi a amostra, quantidades, totais e abrangência completa do período")
        if st.button("Confirmar sincronização interna", disabled=not confirmado, type="primary"):
            coordenador.commit(previa["id"], previa["claim_token"], coverage_confirmed=True)
            st.session_state.pop("_sync_preview", None)
            st.session_state.pop("_sync_attempt", None)
            st.success("Cópia analítica consolidada. Consulte Inteligência de Vendas.")
            st.rerun()
        if st.button("Cancelar esta prévia"):
            coordenador.cancel(previa["id"], previa["claim_token"])
            st.session_state.pop("_sync_preview", None)
            st.session_state.pop("_sync_attempt", None)
            st.rerun()
    st.caption("Fontes reais são registradas pelo procedimento administrativo protegido. "
               "Endereços, mapeamentos e chaves não são configuráveis pelo navegador.")
