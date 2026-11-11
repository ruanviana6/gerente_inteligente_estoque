"""Página comercial que consulta apenas resultados previamente calculados."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd
import streamlit as st

from analytics.occupancy import avisos_ocupacao
from web.errors import AuthorizationError
from web.sales_page import vendas
from web.theme import page_header

ALERTAS = {
    "cadencia_atrasada": "Compras mais espaçadas que o histórico",
    "volume_crescente": "Volume de compras aumentou",
    "volume_reduzido": "Volume de compras diminuiu",
    "mix_alterado": "Composição dos produtos mudou",
    "cidade_crescimento": "Demanda da cidade aumentou",
    "cidade_queda": "Demanda da cidade diminuiu",
    "produtos_juntos": "Produtos comprados no mesmo pedido",
}

def _perfil(contexto, campo):
    """Lê o perfil validado, aceitando também a representação usada em testes."""
    perfil = contexto.profile
    return perfil.get(campo) if isinstance(perfil, Mapping) else getattr(perfil, campo)


def _mesmo_acesso(inicial, atual) -> None:
    """Impede apresentar resultados depois de alteração de identidade ou permissões."""
    if (_perfil(inicial, "user_id") != _perfil(atual, "user_id")
            or _perfil(inicial, "organization_id") != _perfil(atual, "organization_id")
            or inicial.permissions != atual.permissions):
        raise AuthorizationError("Seu acesso mudou. Atualize a página.")


def _armazenamento(servico, contexto) -> None:
    """Mede ocupação somente após ação explícita do Master."""
    if _perfil(contexto, "role") != "Master":
        return
    with st.expander("Armazenamento do projeto"):
        st.caption("Medições do projeto são restritas ao Master e não substituem as cotas oficiais do provedor.")
        if st.button("Medir e registrar ocupação"):
            medida = servico.storage_overview()
            if medida.get("database_bytes") is None:
                st.warning(medida.get("reason") or "Medição indisponível.")
            else:
                servico.record_storage_measurement()
                _mesmo_acesso(contexto, servico.context())
                st.metric("Banco PostgreSQL", f"{medida['database_bytes'] / 1_000_000:.2f} MB")
                tamanho = medida.get("storage_bytes")
                st.metric("Objetos de Storage", f"{tamanho / 1_000_000:.2f} MB" if tamanho is not None else "Não disponível")
                if tamanho is None:
                    st.caption(medida.get("reason") or "A ocupação dos objetos não pôde ser confirmada.")
                for aviso in avisos_ocupacao(medida["database_bytes"], tamanho):
                    st.warning(f"{aviso['resource']}: {aviso['fraction']:.0%} da referência gratuita. Confira a cota atual no painel do Supabase.")
                if medida.get("tables"):
                    st.dataframe(pd.DataFrame(medida["tables"]), hide_index=True, width="stretch")
        if st.button("Consultar histórico de ocupação"):
            historico = servico.storage_history()
            _mesmo_acesso(contexto, servico.context())
            if historico:
                st.dataframe(pd.DataFrame(historico), hide_index=True, width="stretch")
            else:
                st.info("Ainda não há medições registradas.")
        if st.button("Consultar avaliações de modelos"):
            modelos = servico.commercial_model_status()
            _mesmo_acesso(contexto, servico.context())
            if not modelos:
                st.info("Nenhum modelo foi avaliado e publicado por processo autorizado.")
            for modelo in modelos:
                st.write("Método:", modelo["model"], "Treino até:", modelo["training_end"])
                st.json(modelo["evaluation"])


def comercial(servico, contexto) -> None:
    """Reúne visões comerciais e mantém a análise de vendas existente acessível."""
    page_header("Inteligência Comercial", "Indicadores observados, com amostras e limitações explícitas.")
    visoes = []
    if contexto.can("commercial.view"):
        visoes.extend(["Clientes", "Cidades", "Produtos Juntos"])
    if contexto.can("trends.view"):
        visoes.append("Vendas")
    if not visoes:
        st.info("Sua conta não possui acesso à inteligência comercial.")
        return
    visao = st.radio("Visão comercial", visoes, horizontal=True)
    if visao == "Vendas":
        vendas(servico, contexto)
        return
    if visao == "Clientes" and not contexto.can("commercial.customer_detail"):
        st.info("A consulta por cliente exige permissão específica do Master.")
        return
    fontes = servico.commercial_sources()
    _mesmo_acesso(contexto, servico.context())
    if not fontes:
        st.info("Nenhuma fonte comercial autorizada está disponível. Clientes e cidades dependem da validação da API.")
        _armazenamento(servico, contexto)
        return
    opcoes = {f["id"]: f for f in fontes}
    source = st.selectbox("Fonte autorizada", list(opcoes), format_func=lambda uid: opcoes[uid]["name"])
    lojas = opcoes[source].get("stores") or []
    if not lojas:
        st.info("A fonte ainda não tem loja autorizada.")
        return
    store = st.selectbox("Loja", lojas)
    dimensao = {"Clientes": "customer", "Cidades": "city", "Produtos Juntos": "pair"}[visao]
    referencia_cliente = None
    if dimensao == "customer":
        referencia_cliente = st.text_input("Referência do cliente (opcional)", max_chars=64).strip() or None
        if referencia_cliente and (len(referencia_cliente) < 8 or any(c not in "0123456789abcdef" for c in referencia_cliente)):
            st.info("Informe pelo menos oito caracteres da referência mostrada no insight.")
            return
    pagina = st.number_input("Página de insights", min_value=1, max_value=2001, value=1, step=1)
    registros = servico.commercial_insights(source, store, dimensao, (int(pagina) - 1) * 10, referencia_cliente)
    _mesmo_acesso(contexto, servico.context())
    st.caption(f"Última sincronização informada: {opcoes[source].get('last_sync') or 'não disponível'}.")
    if not registros:
        st.info("Nenhum insight pré-calculado está disponível para esta fonte e visão.")
    else:
        st.subheader("Atenções prioritárias")
        for item in registros:
            st.markdown("**" + ALERTAS.get(item["alert_code"], "Variação observada") + "**")
            if dimensao == "customer":
                st.write("Referência pseudonimizada:", item.get("customer_ref", "Não informada"))
            elif dimensao == "city":
                st.write("Local:", item.get("city_name") or item.get("city_code"), item.get("state") or "")
                st.write("Produtos com mais pedidos:", ", ".join(item.get("top_skus") or []) or "Amostra insuficiente por produto")
                st.caption("Período anterior: " + (", ".join(item.get("previous_top_skus") or []) or "amostra insuficiente por produto"))
            else:
                st.write("SKUs:", item.get("sku_a"), "e", item.get("sku_b"))
            st.write(f"Período analisado: {item['period_start']} a {item['period_end']}.")
            st.caption(f"Método: regra estatística. Calculado em: {item.get('computed_at') or 'não informado'}. "
                       f"Cidade utilizada: { {'delivery': 'entrega', 'registration': 'cadastro', 'billing': 'faturamento'}.get(item.get('city_basis'), 'não informada')}.")
            if dimensao == "pair":
                st.caption(f"Coocorrência: {item['current_orders']} de {item['total_orders']} pedidos. Suporte: {item['support']}; "
                           f"confiança: {item['confidence']}; lift: {item['lift']}.")
            else:
                st.write(f"Unidades: {item['current_units']} no período e {item['previous_units']} no anterior de igual duração.")
                st.caption(f"Pedidos: {item['current_orders']} no período e {item['previous_orders']} no anterior. "
                           "A diferença deve ser investigada antes de decidir.")
                if dimensao == "customer":
                    st.caption(f"Intervalo mediano: {item.get('median_gap_days')} dias; "
                               f"dias desde a última compra: {item.get('recency_days')}; "
                               f"similaridade do mix: {item.get('mix_similarity')}.")
        st.caption("Os indicadores descrevem mudanças observadas; não provam causalidade nem abandono.")
    _armazenamento(servico, contexto)
