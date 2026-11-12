"""Visão Geral com até seis KPIs, cinco prioridades e dois gráficos compactos."""

import pandas as pd
import streamlit as st

from web.theme import metric_card, page_header, surface

ALERTAS = {
    'risk': ('Risco antes da chegada da produção', 'O prazo da indústria pode ultrapassar a cobertura elegível.', 'Reposição Inteligente', 'risco'),
    'stockout': ('Ruptura observada', 'O saldo informado é zero; ausência de venda não prova ausência de demanda.', 'Estoque', 'ruptura'),
    'production': ('Revisar a próxima solicitação', 'Há produtos com sugestão. Confira cobertura e conversão por embalagem.', 'Reposição Inteligente', ''),
    'excess': ('Excesso ou produtos com baixo giro', 'A classificação depende do histórico declarado e da disponibilidade.', 'Estoque', ''),
    'quality': ('Revisar a qualidade do histórico', 'Faltam dados ou há disponibilidade incerta; as recomendações exigem revisão.', 'Estoque', 'qualidade'),
    'customer': ('Clientes com redução ou cadência atrasada', 'Alertas publicados com comparação temporal; não representam probabilidade de abandono.', 'Inteligência Comercial', ''),
    'city': ('Mudança relevante de cidades', 'Sinais publicados por fonte/loja, respeitando o tamanho mínimo dos grupos.', 'Inteligência Comercial', ''),
}


def _abrir(pagina, foco=''):
    """Muda somente o destino de navegação, mantendo as permissões do próximo rerun."""
    st.session_state['_pagina'] = pagina
    st.session_state['_foco_prioridade'] = foco


def visao_geral(servico, contexto):
    """Não chama load_inventory, motor, treinamento, histórico bruto nem ERP."""
    page_header('Visão Geral', 'O que precisa de atenção, por quê e onde conferir.')
    dados = servico.dashboard()
    meta = dados.get('metadata') or {}
    resumo = dados.get('summary')
    fonte = meta.get('source', 'Nenhuma importação disponível')
    modo = 'DEMO declarada' if resumo and resumo.get('mode') == 'demo' else 'Origem CSV; classificação do ambiente não informada'
    st.caption(f'Fonte: {fonte} · {modo}')
    st.caption(f'Saldo observado: {meta.get("snapshot_date", "não disponível")} · '
               f'Histórico: {meta.get("period_start", "não disponível")} a {meta.get("period_end", "não disponível")} · '
               f'Resumo calculado: {dados.get("calculated_at") or "pendente"}')
    if dados.get('stale') and resumo:
        st.warning('Este resumo pertence à atualização anterior. Dados, parâmetros ou embalagens mudaram; confira a data antes de decidir.')
    if contexto.profile.role == 'Master':
        if st.button('Atualizar indicadores', help='Processo explícito: recalcula estoque e ciclos e publica somente um resumo compacto.'):
            with st.spinner('Preparando o resumo autorizado…'):
                servico.refresh_dashboard()
            st.rerun()
    if not resumo:
        st.info('O resumo ainda não foi preparado. O Master pode atualizar os indicadores após a importação; navegar não carrega o histórico bruto.')
        return
    estoque = resumo['inventory']
    reposicao = resumo.get('replenishment') or {}
    vendas = resumo.get('sales') or {}
    comercial = dados.get('commercial') or {}
    caixas = reposicao.get('boxes')
    cards = [
        ('Em ruptura', estoque['stockouts'], 'Saldo informado igual a zero', 'danger'),
        ('Risco antes da chegada', estoque['risk'], 'Prazo da indústria e demanda elegível', 'warning'),
        ('Próxima solicitação', f'{caixas} caixas' if caixas is not None else 'Revisar dados',
         'Solicitar até ' + (reposicao.get('next_request') or 'data não estimada'), 'brand'),
        ('Vendas observadas · 30 dias', vendas.get('current_30') if vendas.get('current_30') is not None else 'Não disponível',
         'Unidades observadas; não representam demanda perdida', 'info'),
        ('Clientes com alertas publicados', comercial.get('customers') if comercial else 'Não disponível', 'Regras e modelos já avaliados, sem treino nesta tela', 'info'),
        ('Cidades com alertas publicados', comercial.get('cities') if comercial else 'Não disponível', 'Grupos protegidos, separados por fonte e loja', 'info'),
    ]
    for inicio in (0, 3):
        colunas = st.columns(3, gap='small', wrap=True)
        for indice, (titulo, valor, nota, tom) in enumerate(cards[inicio:inicio+3]):
            with colunas[indice]:
                metric_card(titulo, valor, key=f'flatter_overview_kpi_{inicio+indice}', note=nota, tone=tom, width='stretch')
    if reposicao:
        st.caption(f'Ciclos {reposicao["primary"]}/{reposicao["alternative"]} dias · '
                   f'Referência editável de {reposicao["reference_boxes"]} caixas, sem pressupor capacidade do caminhão. '
                   f'SKUs que exigem revisão de embalagem/qualidade: {reposicao["unknown_packaging"]}.')
    if vendas.get('change_pct') is not None:
        st.caption(f'Variação observada de unidades: {vendas["change_pct"]:+.1f}% frente aos 30 dias anteriores. '
                   'A comparação não corrige demanda perdida nem comprova sazonalidade.')
    if comercial:
        st.caption(f'Comercial publicado: {comercial["period_start"]} a {comercial["period_end"]}. '
                   'Os períodos das fontes podem diferir; contagens consideram somente alertas publicados, com limites por grupo.')
    prioridades = list(resumo.get('priorities', []))
    if comercial.get('customer_decline'):
        prioridades.append({'kind': 'customer', 'count': comercial['customer_decline'], 'priority': 2})
    if comercial.get('city_decline') or comercial.get('cities'):
        prioridades.append({'kind': 'city', 'count': comercial.get('city_decline') or comercial['cities'], 'priority': 3})
    destinos = {'Estoque': 'inventory.view', 'Reposição Inteligente': 'replenishment.view', 'Inteligência Comercial': 'commercial.view'}
    st.subheader('Prioridades para conferência')
    for indice, alerta in enumerate(sorted(prioridades, key=lambda a: a['priority'])[:5]):
        titulo, justificativa, pagina, foco = ALERTAS[alerta['kind']]
        with surface(f'flatter_overview_alert_{indice}'):
            st.markdown(f'**{titulo} · {alerta["count"]}**')
            st.write(justificativa)
            st.caption(f'Período de referência: {meta.get("period_start")} a {meta.get("period_end")}. '
                       'Não é uma leitura em tempo real; confirme a qualidade e os detalhes antes de agir.')
            if contexto.can(destinos[pagina]):
                st.button('Abrir ' + pagina, key=f'overview_acao_{indice}', on_click=_abrir, args=(pagina, foco))
    if not prioridades:
        st.info('Sem alertas prioritários no resumo disponível. Isso não comprova ausência de riscos não observados.')
    if vendas.get('daily'):
        st.subheader('Unidades vendidas · últimos 30 dias observados')
        serie = pd.DataFrame(vendas['daily']).set_index('date')
        st.line_chart(serie.rename(columns={'units': 'Unidades'}), height=220)
    ciclos = reposicao.get('cycles') or []
    if ciclos and all(c.get('boxes') is not None for c in ciclos):
        st.subheader('Comparação de ciclos de solicitação')
        st.bar_chart(pd.DataFrame({'Ciclo': [f'{c["days"]} dias' for c in ciclos],
                                  'Caixas': [c['boxes'] for c in ciclos]}).set_index('Ciclo'), height=220)
    if contexto.can('finance.view') and resumo.get('finance'):
        with st.expander('Capital a custo · consulta financeira autorizada'):
            financeiro = resumo['finance']
            valor = financeiro.get('capital')
            st.write('Capital conhecido em estoque: ' + (('R$ ' + f'{valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')) if valor is not None else 'Não disponível'))
            st.caption('A custo informado, não receita. ' + ('Há custos ausentes; valor parcial.' if financeiro.get('partial') else 'Custos conhecidos no resumo.'))
    if dados.get('fiscal'):
        fiscal = dados['fiscal']
        st.caption(f'Fiscal DEMO: {fiscal["pending"]} pendentes · {fiscal["conference"]} necessitam conferência. '
                   'Nenhum documento ou dado pessoal é mostrado neste resumo.')
        st.button('Abrir Automação / Notas Fiscais', on_click=_abrir, args=('Automação / Notas Fiscais',), key='overview_fiscal')
