"""Controles Master do agendamento, sem executar leituras do ERP ao navegar."""

from __future__ import annotations

from datetime import timedelta

import streamlit as st

from dados import hoje_local
from integracoes.configuration import contract_fingerprint, missing_configuration, validated_scope
from integracoes.gestaoclick import GestaoClickError
from sincronizacao.periodos import validar_periodo
from web.errors import AppError, AuthenticationError, AuthorizationError
from web.sales_service import access_stamp


def _protected_settings(auth, loader):
    """Lê Secrets só na ação Master e invalida troca de identidade durante a leitura."""
    initial = auth.require("integrations.manage")
    stamp = access_stamp(initial, auth.generation)
    if initial.profile.role != "Master":
        raise AuthorizationError("Somente o Master configura a sincronização.")
    values = loader()
    current = auth.require("integrations.manage")
    if stamp != access_stamp(current, auth.generation):
        raise AuthorizationError("A autorização mudou durante a configuração.")
    return values, current, stamp


def _rpc(auth, stamp, name, payload):
    """Revalida a sessão antes e depois de cada operação administrativa."""
    if stamp != access_stamp(auth.require("integrations.manage"), auth.generation):
        raise AuthorizationError("A autorização mudou durante a operação.")
    result = auth.rpc(name, payload, "integrations.manage")
    if stamp != access_stamp(auth.require("integrations.manage"), auth.generation):
        raise AuthorizationError("A autorização mudou durante a operação.")
    return result


def _failure(error):
    """Mantém revogação na fronteira global e nunca imprime resposta remota bruta."""
    if isinstance(error, (AuthenticationError, AuthorizationError)):
        raise error
    if isinstance(error, (GestaoClickError, ValueError)):
        st.warning(str(error))
    else:
        st.warning("Não foi possível consultar ou salvar o agendamento no banco. "
                   "Confira a migração de sincronização automática e a disponibilidade do Supabase.")


def automation_controls(servico, loader):
    """Oferece diagnóstico, aprovação e pausa sem trabalho automático no Streamlit."""
    auth = servico.auth
    with st.expander("Configuração e sincronização automática"):
        st.write("A atualização agendada funciona fora do navegador. A carga inicial é dividida "
                 "em períodos menores; depois o processo revisita vendas recentes e o histórico.")
        st.caption("Esta integração atualiza linhas de vendas. O catálogo e o saldo de estoque "
                   "continuam sendo importados por CSV. Não cadastra nem emite notas fiscais.")
        if st.button("Verificar configuração da sincronização"):
            try:
                values, context, stamp = _protected_settings(auth, loader)
                missing = missing_configuration(values)
                if missing:
                    st.warning("A conexão não está pronta para importar vendas. Faltam estas configurações:")
                    st.write("\n".join(f"- `{name}`" for name in missing))
                    st.caption("Comece por ‘Conferir lojas e situações da conta’. O registro da fonte e a "
                               "validação do formato das vendas precisam estar completos antes de ativar o agendamento.")
                else:
                    _, source, store = validated_scope(values, context.profile.organization_id)
                    contract_fingerprint(values)
                    _rpc(auth, stamp, "sync_authorize_source", {"p_source_id": source, "p_store_id": store})
                    st.success("Configuração local e acesso à fonte conferidos. Nenhuma consulta ao ERP foi feita.")
                    st.caption("Ainda é necessário validar uma amostra real antes de aprovar o agendamento.")
            except (AppError, GestaoClickError, ValueError) as error:
                _failure(error)

        if st.button("Consultar estado da atualização automática"):
            try:
                values, context, stamp = _protected_settings(auth, loader)
                _, source, store = validated_scope(values, context.profile.organization_id)
                status = _rpc(auth, stamp, "sync_automation_status", {"p_source_id": source, "p_store_id": store})
                if status:
                    st.write({key: status.get(key) for key in (
                        "enabled", "history_start", "history_end", "phase", "next_due",
                        "last_success", "last_error_code", "run_count", "max_runs",
                    )})
                    st.caption("Ativação no banco não comprova executor configurado. Confira a última execução "
                               "bem-sucedida. Cobertura integral não é presumida pelo agendamento.")
                else:
                    st.info("Nenhuma atualização automática configurada para esta fonte e loja.")
            except (AppError, GestaoClickError, ValueError) as error:
                _failure(error)

        with st.form("configurar_sync_automatica"):
            dates = st.date_input("Histórico inicial da atualização automática",
                                  (hoje_local().date() - timedelta(days=731), hoje_local().date()),
                                  max_value=hoje_local().date())
            interval = st.selectbox("Intervalo após a carga histórica", (24, 48, 168),
                                    format_func=lambda hours: f"A cada {hours} horas")
            approved = st.checkbox("Conferi uma amostra real, loja, campos e situações; autorizo a atualização automática")
            rotate = st.checkbox("Gerar nova credencial do executor e invalidar a anterior")
            save = st.form_submit_button("Ativar atualização automática")
        if save:
            try:
                if not approved:
                    raise ValueError("Confira a amostra e marque a autorização antes de ativar o agendamento.")
                if len(dates) != 2:
                    raise ValueError("Escolha as datas inicial e final do histórico.")
                first, last = validar_periodo(*dates)
                values, context, stamp = _protected_settings(auth, loader)
                if missing_configuration(values):
                    raise ValueError("A atualização automática ainda não pode ser ativada. Primeiro confira "
                                     "as lojas e situações, registre a fonte e conclua a configuração de vendas. "
                                     "As chaves existentes podem ser usadas na conferência da conta.")
                _, source, store = validated_scope(values, context.profile.organization_id)
                fingerprint = contract_fingerprint(values)
                result = _rpc(auth, stamp, "sync_automation_configure", {
                    "p_source_id": source, "p_store_id": store, "p_contract_hash": fingerprint,
                    "p_history_start": first.isoformat(), "p_history_end": last.isoformat(),
                    "p_interval_hours": interval, "p_enabled": True, "p_approved": True,
                    "p_rotate_secret": rotate,
                })
                st.success("Autorização salva. O executor agendado precisa estar configurado para iniciar as leituras.")
                if result.get("secret"):
                    st.warning("Credencial exclusiva do executor, exibida somente nesta ação. "
                               "Salve no GitHub Actions como SYNC_JOB_SECRET dentro do Secret JSON protegido. "
                               "Não publique nem envie por mensagem.")
                    st.code(result["secret"], language=None)
                st.caption("Consulte docs/SINCRONIZACAO_AUTOMATICA.md para configurar o executor gratuito. "
                           "Nenhuma consulta de vendas é disparada ao salvar este formulário.")
            except (AppError, GestaoClickError, ValueError) as error:
                _failure(error)
        if st.button("Pausar atualização automática"):
            try:
                values, context, stamp = _protected_settings(auth, loader)
                _, source, store = validated_scope(values, context.profile.organization_id)
                _rpc(auth, stamp, "sync_automation_pause", {"p_source_id": source, "p_store_id": store})
                st.success("Atualização automática pausada. O histórico foi preservado.")
            except (AppError, GestaoClickError, ValueError) as error:
                _failure(error)
