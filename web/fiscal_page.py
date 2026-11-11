"""Área fiscal única: escolhas manuais, prévia, confirmação e histórico DEMO."""

import streamlit as st

from fiscal.regras import ESTADOS, MODALIDADES_FRETE, TIPOS_CONTRIBUINTE, elegibilidade
from web.errors import AppError
from web.fiscal_service import FiscalService
from web.theme import page_header, surface


def _invalidar_previa():
    """Uma edição exige gerar e conferir outra prévia antes de confirmar."""
    st.session_state.pop('_fiscal_preview_id', None)


def _reiniciar_escolhas():
    """Cada novo pedido exige ambas as escolhas, sem reaproveitar defaults."""
    for chave in ('_fiscal_preview_id', 'fiscal_frete', 'fiscal_contribuinte', 'fiscal_confirmacao', 'fiscal_aprovacao'):
        st.session_state.pop(chave, None)


def _processar(servico, identificador, reconciliar=False):
    """Aciona uma tentativa explícita e conserva a solicitação em caso de falha."""
    try:
        with st.spinner('Consultando o executor DEMO…'):
            resultado = servico.executar(identificador, reconciliar)
        st.success('DEMO: ' + ESTADOS.get(resultado.get('state'), 'Consulte o histórico para conferir o resultado.'))
    except AppError:
        st.warning('A solicitação está preservada. O executor não confirmou o resultado; consulte o histórico antes de tentar novamente.')


def _pedido(servico, contexto):
    """Pesquisa somente pedidos fictícios declarados; abrir a tela não solicita nota."""
    with st.form('fiscal_busca'):
        numero = st.text_input('Número do pedido do Atacado', max_chars=20, placeholder='Exemplo DEMO: 1001')
        pesquisar = st.form_submit_button('Consultar pedido DEMO')
    if pesquisar:
        numero = numero.strip()
        servico.consultar(numero)
        _reiniciar_escolhas()
        st.session_state['_fiscal_number'] = numero
    numero = st.session_state.get('_fiscal_number')
    if not numero:
        st.info('Cenários fictícios: 1001 autorizada; 1002 rejeitada; 1003 Em andamento; 1004 outra UF; '
                '1005 situação bloqueada; 1006 nota existente; 1007/1008 timeout; 1009 cadastro incompleto; 1010 denegada.')
        return
    pedido = servico.consultar(numero)
    with surface('fiscal_pedido'):
        st.subheader(f'Pedido {pedido["numero"]} · DEMO')
        st.write(f'Origem: Atacado · Emissora: Varejo · Destino: {pedido["uf"]}')
        st.write(f'Destinatário fictício: {pedido["cliente"]} · Situação: {pedido["situacao"]}')
        st.dataframe(pedido['itens'], hide_index=True, width='stretch', key='fiscal_itens')
        st.write(f'Valor total fictício: R$ {pedido["valor_total"]:.2f}')
        liberado, motivo = elegibilidade(pedido)
        st.caption(motivo)
        if pedido.get('nota_existente') or pedido.get('solicitacao_existente'):
            st.warning('Existe nota ou solicitação anterior. Não será criada outra nota automaticamente.')
            return
        if not liberado and pedido['situacao'] != 'Em andamento':
            st.warning('Pedido bloqueado para solicitação.')
            return
    if not contexto.can('fiscal.create_request'):
        st.info('Seu acesso permite consulta. Solicitar uma nota exige permissão fiscal específica.')
        return
    st.subheader('Dados fiscais manuais')
    frete = st.selectbox('Modalidade do frete', list(MODALIDADES_FRETE), index=None,
                        format_func=lambda codigo: f'{codigo} · {MODALIDADES_FRETE[codigo]}',
                        placeholder='Selecione uma modalidade', key='fiscal_frete', on_change=_invalidar_previa)
    contribuinte = st.selectbox('Tipo de contribuinte', list(TIPOS_CONTRIBUINTE), index=None,
                               format_func=lambda codigo: f'{codigo} · {TIPOS_CONTRIBUINTE[codigo]}',
                               placeholder='Selecione a situação fiscal validada', key='fiscal_contribuinte', on_change=_invalidar_previa)
    st.caption('FOB é uma escolha manual. Isento (2) e não contribuinte (9) são situações distintas. '
               'A ausência de inscrição estadual não define essa escolha.')
    if st.button('Gerar prévia DEMO', type='primary'):
        previa = servico.preparar(numero, frete, contribuinte)
        st.session_state['_fiscal_preview_id'] = previa['id']
    identificador = st.session_state.get('_fiscal_preview_id')
    if not identificador:
        return
    previa = servico.previa(identificador)
    if previa['choices'] != {'modalidade_frete': frete, 'tipo_contribuinte': contribuinte}:
        _invalidar_previa()
        st.info('As escolhas mudaram. Gere uma nova prévia.')
        return
    with surface('fiscal_previa'):
        st.subheader('Prévia da NF-e · DEMO')
        st.write(f'Pedido {numero}, Atacado → Varejo · Destinatário {pedido["cliente"]} · UF {pedido["uf"]}')
        st.write(f'Situação: {pedido["situacao"]} · Total fictício: R$ {pedido["valor_total"]:.2f}')
        st.write(f'Frete {frete}: {MODALIDADES_FRETE[frete]}')
        st.write(f'Contribuinte {contribuinte}: {TIPOS_CONTRIBUINTE[contribuinte]}')
        st.caption(f'Prévia versão {previa["version"]} · referência {previa["id"]}')
        st.info('Cadastro, tributação, natureza da operação, transporte e pagamento reais ainda não foram '
                'homologados. A prévia permite apenas simulação. Nenhum CFOP, imposto ou cadastro é inferido.')
        aprovacao = False
        if pedido['situacao'] == 'Em andamento':
            if contexto.can('fiscal.approve_pending'):
                aprovacao = st.checkbox('Autorizo explicitamente este pedido Em andamento para a simulação', key='fiscal_aprovacao')
            else:
                st.info('A solicitação ficará aguardando aprovação de um usuário habilitado.')
        confirmacao = st.checkbox('Conferi a prévia e confirmo somente a solicitação DEMO, sem emissão real', key='fiscal_confirmacao')
        if st.button('Confirmar solicitação DEMO', type='primary', disabled=not confirmacao):
            resultado = servico.confirmar(identificador, confirmacao, aprovacao)
            if resultado.get('duplicate'):
                st.warning('Solicitação já registrada. A repetição não iniciou outra nota.')
            elif resultado.get('state') == 'awaiting_approval':
                st.success('Solicitação registrada e aguardando aprovação identificada.')
            else:
                st.success('Solicitação DEMO registrada com identificação única.')
                _processar(servico, resultado['id'])
            st.session_state['_fiscal_reset_next'] = True


def _historico(servico, contexto):
    """Lista uma página por vez e oferece apenas transições explicitamente permitidas."""
    st.subheader('Solicitações e acompanhamento')
    pagina = int(st.session_state.get('_fiscal_history_page', 0))
    linhas = servico.listar(pagina)
    if not linhas:
        st.info('Nenhuma solicitação nesta página. O histórico respeita sua organização e suas permissões.')
    else:
        st.dataframe([{'Pedido': r['order_number'], 'Situação': ESTADOS.get(r['state'], r['state']),
                       'Fiscal': r.get('official_state') or 'Não confirmada', 'Ambiente': 'DEMO',
                       'Frete': r['freight'], 'Contribuinte': r['taxpayer'], 'Registrada em': r['created_at']}
                      for r in linhas], hide_index=True, width='stretch', key='fiscal_solicitacoes')
        por_id = {r['id']: r for r in linhas}
        selecionada = st.selectbox('Consultar solicitação', list(por_id), index=None,
                                  format_func=lambda i: f'{por_id[i]["order_number"]} · {ESTADOS[por_id[i]["state"]]}',
                                  key='fiscal_detalhe')
        if selecionada:
            item = por_id[selecionada]
            st.write(f'Frete {item["freight"]}: {MODALIDADES_FRETE[item["freight"]]} · '
                     f'Contribuinte {item["taxpayer"]}: {TIPOS_CONTRIBUINTE[item["taxpayer"]]}')
            st.caption('Origem: DEMO-ATACADO · Emissora: DEMO-VAREJO · Escolhas preservadas após a confirmação.')
            if item.get('invoice_number'):
                st.write('Número fictício: ' + item['invoice_number'])
            if item.get('invoice_key'):
                st.code(item['invoice_key'], language=None)
            if item.get('error_code'):
                st.warning('Ocorrência técnica: ' + item['error_code'] + '. Não crie outra solicitação para contornar o bloqueio.')
            if item['state'] == 'awaiting_approval' and contexto.can('fiscal.approve_pending'):
                confirma = st.checkbox('Confirmo a autorização identificada do pedido Em andamento')
                if st.button('Autorizar pedido DEMO', disabled=not confirma):
                    servico.aprovar(selecionada, confirma)
                    st.rerun()
            if contexto.can('fiscal.create_request') and item['state'] in {'confirmed', 'needs_reconciliation'}:
                reconciliar = item['state'] == 'needs_reconciliation'
                if st.button('Reconciliar DEMO' if reconciliar else 'Processar simulação'):
                    _processar(servico, selecionada, reconciliar)
            if contexto.can('fiscal.audit') and st.button('Consultar trilha de auditoria fiscal'):
                st.dataframe(servico.historico(selecionada), hide_index=True, width='stretch')
    colunas = st.columns(2)
    if colunas[0].button('Página anterior de solicitações', disabled=pagina == 0):
        st.session_state['_fiscal_history_page'] = pagina - 1
        st.rerun()
    if colunas[1].button('Próxima página de solicitações', disabled=len(linhas) < 25 or pagina >= 199):
        st.session_state['_fiscal_history_page'] = pagina + 1
        st.rerun()


def fiscal(servico, contexto):
    """Renderiza uma única área fiscal; nenhuma leitura da tela executa o emissor."""
    page_header('Automação / Notas Fiscais', 'Solicitações do Atacado para a conta Varejo, com conferência humana.')
    st.warning('DEMONSTRAÇÃO · Emissão real desabilitada. Nenhuma chamada ao GestãoClick. Sem armazenamento de XML/PDF.')
    if not contexto.can('fiscal.view'):
        st.error('Acesso fiscal não autorizado.')
        return
    if st.session_state.pop('_fiscal_reset_next', False):
        _reiniciar_escolhas()
    fiscal_servico = FiscalService(servico.auth)
    if fiscal_servico.estado().get("demo_enabled"):
        _pedido(fiscal_servico, contexto)
    else:
        st.info("Modo DEMO desativado pelo Master. O histórico continua disponível.")
    st.divider()
    _historico(fiscal_servico, contexto)
