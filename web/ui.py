"""Interface web única; cálculos e autorização ficam nos serviços e no banco.

O estado pertence exclusivamente à sessão Streamlit. Não há cache de clientes,
tokens, tabelas ou relatórios compartilhado entre pessoas.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import pandas as pd
import streamlit as st

from dados import hoje_local
from motor import somar_valores_financeiros
from web.errors import AppError, AuthenticationError, AuthorizationError, ConfigurationError
from web.insights import gerar_insights

PERMISSOES = {
    "dashboard.view": "Painel e insights",
    "inventory.view": "Estoque e histórico",
    "replenishment.view": "Sugestões de reposição",
    "replenishment.export": "Exportar reposição",
    "finance.view": "Custos e indicadores financeiros",
    "trends.view": "Tendências e Curva ABC",
    "inventory.import": "Importar dados",
    "parameters.edit": "Editar parâmetros",
    "decisions.write": "Registrar decisões",
    "audit.view": "Consultar auditoria",
    "reports.view": "Consultar relatórios mensais",
    "reports.export": "Exportar relatórios",
    "reports.receive": "Receber relatórios autorizados",
}

COLUNAS_ESTOQUE = [
    "codigo", "nome", "fornecedor", "status", "estoque", "venda_media_diaria",
    "cobertura_dias", "prazo_entrega_dias", "risco_prazo", "aviso_qualidade",
]
COLUNAS_REPOSICAO = [
    "fornecedor", "codigo", "nome", "status", "estoque", "reservas",
    "pedidos_pendentes", "venda_media_diaria", "cobertura_dias",
    "prazo_entrega_dias", "estoque_seguranca", "ponto_pedido",
    "posicao_estoque", "estoque_alvo", "risco_prazo", "quantidade_sugerida",
    "demanda_estimavel", "requer_revisao", "aviso_qualidade",
]


def _perfil(contexto: Any, campo: str) -> Any:
    """Lê o perfil validado sem interpretar metadados editáveis do usuário."""
    perfil = contexto.profile
    return perfil.get(campo) if isinstance(perfil, Mapping) else getattr(perfil, campo)


def _assinatura(contexto: Any) -> tuple:
    """Identifica troca de pessoa, organização, papel ou permissões na sessão."""
    return (
        _perfil(contexto, "user_id"), _perfil(contexto, "organization_id"),
        _perfil(contexto, "role"), tuple(sorted(contexto.permissions)),
    )


def _moeda(valor: float) -> str:
    """Formata somente valores conhecidos, sem transformar ausência em zero."""
    if pd.isna(valor):
        return "Não disponível"
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _tabela(tabela: pd.DataFrame, colunas: list[str] | None = None) -> None:
    """Exibe apenas colunas explicitamente autorizadas para o bloco atual."""
    exibida = tabela if colunas is None else tabela.loc[:, [c for c in colunas if c in tabela]]
    st.dataframe(exibida, hide_index=True, use_container_width=True)


def _encerrar_sessao(servico: Any) -> None:
    """Revoga a sessão remota e sempre remove dados e widgets locais."""
    try:
        servico.auth.logout()
    except AppError:
        # O serviço mantém a limpeza local mesmo se o servidor estiver indisponível.
        pass
    finally:
        st.session_state.clear()


@st.fragment(run_every="20s")
def _vigiar_autorizacao(servico: Any, assinatura: tuple) -> None:
    """Revalida acesso em tela aberta e remove conteúdo após revogação."""
    try:
        contexto = servico.context()
    except (AuthenticationError, AuthorizationError):
        _encerrar_sessao(servico)
        st.rerun(scope="app")
    except AppError:
        st.info("Não foi possível confirmar a sessão. Atualize a página para continuar.")
        return
    if _assinatura(contexto) != assinatura:
        st.rerun(scope="app")


def _login(servico: Any) -> None:
    """Solicita credenciais ao Supabase sem disponibilizar cadastro público."""
    st.subheader("Acesse sua conta")
    st.caption("Somente usuários autorizados pelo Master podem acessar os dados.")
    with st.form("login", clear_on_submit=True):
        email = st.text_input("E-mail", max_chars=254, autocomplete="email")
        senha = st.text_input("Senha", type="password", autocomplete="current-password")
        entrar = st.form_submit_button("Entrar", type="primary")
    if entrar:
        try:
            servico.auth.login(email, senha)
            st.rerun()
        except AppError:
            st.error("Não foi possível entrar. Confira os dados e se sua conta está autorizada.")
    with st.expander("Recuperar acesso"):
        st.caption("A entrega de recuperação depende do e-mail do Supabase configurado pelo proprietário.")
        with st.form("solicitar_recuperacao", clear_on_submit=True):
            email_recuperacao = st.text_input("E-mail da conta", max_chars=254)
            solicitar = st.form_submit_button("Solicitar código de recuperação")
        if solicitar:
            try:
                servico.auth.request_password_reset(email_recuperacao)
                st.info("Se a conta e o envio estiverem habilitados, você receberá as instruções.")
            except AppError:
                st.info("A recuperação não está disponível. Solicite orientação ao Master.")
        with st.form("confirmar_recuperacao", clear_on_submit=True):
            email_codigo = st.text_input("E-mail para validar o código", max_chars=254)
            codigo = st.text_input("Código recebido", type="password", max_chars=128)
            senha_nova = st.text_input("Nova senha", type="password", autocomplete="new-password")
            confirmar = st.form_submit_button("Definir nova senha")
        if confirmar:
            try:
                servico.auth.recover_password(email_codigo, codigo, senha_nova)
                st.success("Senha atualizada. Entre novamente com a nova senha.")
            except AppError:
                st.error("Não foi possível recuperar o acesso. Confira o código e a nova senha.")


def _conta(servico: Any, contexto: Any) -> None:
    """Mostra identidade, saída e troca de senha sem revelar tokens."""
    st.sidebar.subheader(str(_perfil(contexto, "display_name")))
    st.sidebar.caption(str(_perfil(contexto, "role")))
    if st.sidebar.button("Sair", use_container_width=True):
        _encerrar_sessao(servico)
        st.rerun()
    with st.sidebar.expander("Alterar senha"):
        with st.form("alterar_senha", clear_on_submit=True):
            nova = st.text_input("Nova senha da conta", type="password", autocomplete="new-password")
            alterar = st.form_submit_button("Atualizar senha")
        if alterar:
            try:
                servico.auth.change_password(nova)
                st.success("Senha atualizada.")
            except AppError:
                st.error("Não foi possível alterar a senha. Verifique os requisitos e tente novamente.")


def _origem(dados: dict) -> None:
    """Mantém data da fotografia e abrangência visíveis junto aos indicadores."""
    meta = dados.get("metadata") or {}
    st.caption(
        f"Fonte: {meta.get('source', 'Importação autorizada')} · "
        f"Estoque observado em {meta.get('snapshot_date', 'data não informada')} · "
        f"Histórico: {meta.get('period_start', 'não informado')} a {meta.get('period_end', 'não informado')}"
    )


def _carregar(servico: Any, modulo: str) -> dict | None:
    """Carrega novamente pela permissão da operação, sem cache entre usuários."""
    dados = servico.load_inventory(modulo)
    _origem(dados)
    if dados["panel"].empty:
        st.info("Ainda não há produtos. Um usuário com permissão de importação pode carregar os CSVs.")
        return None
    return dados


@st.fragment(run_every="10s")
def _flash(servico: Any) -> None:
    """Alterna insights determinísticos e verifica a permissão a cada execução."""
    try:
        contexto = servico.context()
        if not contexto.can("dashboard.view"):
            st.rerun(scope="app")
        dados = servico.load_inventory("dashboard.view")
    except (AuthenticationError, AuthorizationError):
        _encerrar_sessao(servico)
        st.rerun(scope="app")
    except AppError:
        st.info("Os insights estão temporariamente indisponíveis. Tente atualizar a página.")
        return
    insights = gerar_insights(dados["panel"], contexto.permissions)
    if not insights:
        return
    indice = int(st.session_state.get("_flash_indice", 0)) % len(insights)
    insight = insights[indice]
    st.info(f"{insight.titulo}\n\n{insight.texto}")
    st.caption(insight.explicacao)
    st.session_state["_flash_indice"] = indice + 1


def _painel(servico: Any, contexto: Any) -> None:
    """Apresenta disponibilidade e KPIs permitidos usando o motor já validado."""
    st.subheader("Visão geral")
    dados = _carregar(servico, "dashboard.view")
    if dados is None:
        return
    painel = dados["panel"]
    a, b, c = st.columns(3)
    a.metric("Produtos", len(painel))
    b.metric("Em ruptura", int(painel["status"].eq("Ruptura").sum()))
    c.metric("Risco antes da entrega", int(painel["risco_prazo"].sum()))
    if contexto.can("replenishment.view"):
        st.metric("Produtos com sugestão", int(painel["quantidade_sugerida"].gt(0).sum()))
    if contexto.can("finance.view"):
        a, b = st.columns(2)
        a.metric("Capital conhecido em estoque", _moeda(somar_valores_financeiros(painel["capital_estoque"])))
        parados = painel.loc[painel["status"].isin(["Parado", "Excesso"]), "capital_estoque"]
        b.metric("Capital em parados/excesso", _moeda(0.0 if parados.empty else somar_valores_financeiros(parados)))
        if painel["capital_estoque"].isna().any():
            st.caption("Capital parcial: há custos ausentes ou não calculáveis.")
    _flash(servico)
    st.markdown("**Situação do estoque**")
    st.bar_chart(painel["status"].value_counts().rename("Produtos"))
    if painel["requer_revisao"].any():
        st.warning("Há histórico insuficiente ou disponibilidade incerta. Confira as limitações antes de decidir.")
    if contexto.can("inventory.view"):
        st.markdown("**Atenções prioritárias**")
        _tabela(painel.head(12), COLUNAS_ESTOQUE)


def _filtros(painel: pd.DataFrame, prefixo: str) -> tuple[pd.DataFrame, dict]:
    """Filtra fornecedor, status e identificação sem alterar o conjunto original."""
    fornecedores = sorted(painel["fornecedor"].fillna("").unique().tolist())
    a, b = st.columns(2)
    selecionados = a.multiselect("Fornecedores", fornecedores, default=fornecedores, key=prefixo + "_fornecedores")
    estados = sorted(painel["status"].unique().tolist())
    status = b.multiselect("Situações", estados, default=estados, key=prefixo + "_status")
    busca = st.text_input("Buscar por nome ou SKU", key=prefixo + "_busca", max_chars=120).strip()
    vista = painel.loc[painel["fornecedor"].isin(selecionados) & painel["status"].isin(status)].copy()
    if busca:
        vista = vista.loc[
            vista["codigo"].str.contains(busca, case=False, regex=False)
            | vista["nome"].str.contains(busca, case=False, regex=False)
        ]
    return vista, {"fornecedor": selecionados, "status": status, "busca": busca}


def _estoque(servico: Any, contexto: Any) -> None:
    """Mostra catálogo, dados de qualidade e histórico de um SKU selecionado."""
    st.subheader("Estoque e histórico")
    dados = _carregar(servico, "inventory.view")
    if dados is None:
        return
    vista, _ = _filtros(dados["panel"], "estoque")
    colunas = COLUNAS_ESTOQUE + (["custo_unitario", "capital_estoque"] if contexto.can("finance.view") else [])
    _tabela(vista, colunas)
    if vista.empty:
        st.info("Nenhum produto corresponde aos filtros.")
        return
    opcoes = vista.set_index("codigo")["nome"].to_dict()
    codigo = st.selectbox("Produto", list(opcoes), format_func=lambda sku: f"{sku} · {opcoes[sku]}")
    item = vista.loc[vista["codigo"].eq(codigo)].iloc[0]
    a, b, c = st.columns(3)
    a.metric("Estoque", int(item["estoque"]))
    b.metric("Venda média/dia", f"{item['venda_media_diaria']:.2f}" if item["demanda_estimavel"] else "Não estimável")
    c.metric("Cobertura", f"{item['cobertura_dias']:.1f} dias" if pd.notna(item["cobertura_dias"]) else "Indefinida")
    if item["requer_revisao"]:
        st.warning(str(item["aviso_qualidade"]))
    if contexto.can("replenishment.view"):
        _explicar_reposicao(item)
    if contexto.can("trends.view"):
        st.write("Tendência:", item["tendencia"])
        if contexto.can("finance.view"):
            st.write("Classe ABC a custo:", item["classe_abc"])
    vendas = dados["sales"]
    historico = vendas.loc[vendas["codigo"].eq(codigo)].groupby("data")["quantidade"].sum()
    st.markdown("**Unidades vendidas no histórico importado**")
    if historico.empty:
        st.info("Não há venda registrada para este produto.")
    else:
        st.line_chart(historico)
    fotos = dados["snapshots"]
    fotos = fotos.loc[fotos["codigo"].eq(codigo)].sort_values("data")
    st.markdown("**Snapshots registrados**")
    if fotos.empty:
        st.info("Ainda não há snapshots deste produto.")
    else:
        st.line_chart(fotos.set_index("data")["estoque"])
    st.caption("Os snapshots são observações. Intervalos sem foto não comprovam disponibilidade.")


def _explicar_reposicao(item: pd.Series) -> None:
    """Apresenta a memória de cálculo sem transformá-la em compra automática."""
    st.markdown("**Memória do cálculo de reposição**")
    st.metric("Sugestão (un.)", int(item["quantidade_sugerida"]) if item["demanda_estimavel"] else "Revisar dados")
    st.write(
        f"Posição = estoque {int(item['estoque'])} + pendentes {int(item['pedidos_pendentes'])} "
        f"− reservas {int(item['reservas'])} = {int(item['posicao_estoque'])} unidades."
    )
    st.write(
        f"Prazo: {int(item['prazo_entrega_dias'])} dias; segurança: {int(item['estoque_seguranca'])} unidades. "
        f"Ponto de pedido: {int(item['ponto_pedido'])}; alvo: {int(item['estoque_alvo'])}. "
        "Sugestão = máximo(0, alvo − posição)."
    )
    st.caption("Pendentes ainda não são entregas confirmadas. A sugestão exige conferência humana e não cria pedidos.")


def _reposicao(servico: Any, contexto: Any) -> None:
    """Disponibiliza lista por fornecedor e exportação novamente autorizada."""
    st.subheader("Reposição por fornecedor")
    dados = _carregar(servico, "replenishment.view")
    if dados is None:
        return
    vista, filtros = _filtros(dados["panel"], "reposicao")
    somente = st.checkbox("Somente itens com sugestão", value=True)
    filtros["somente_sugestao"] = somente
    if somente:
        vista = vista.loc[vista["quantidade_sugerida"].gt(0)]
    _tabela(vista, COLUNAS_REPOSICAO)
    st.caption("Não há compra automática nem alterações no ERP. Confira as limitações e embalagens do fornecedor.")
    if not vista.empty:
        resumo = vista.groupby("fornecedor", as_index=False).agg(
            produtos=("codigo", "count"), unidades_sugeridas=("quantidade_sugerida", "sum"),
        )
        _tabela(resumo)
    if contexto.can("replenishment.export"):
        # O conteúdo é produzido novamente no serviço com permissões atuais.
        st.download_button(
            "Baixar lista de reposição (CSV)", servico.export_replenishment(filters=filtros),
            file_name="reposicao.csv", mime="text/csv", on_click="rerun",
        )


def _tendencias(servico: Any, contexto: Any) -> None:
    """Separa tendência observada, estoque parado e ABC financeira autorizada."""
    st.subheader("Tendências e Curva ABC")
    dados = _carregar(servico, "trends.view")
    if dados is None:
        return
    painel = dados["panel"]
    st.caption("Médias de 30 e 90 dias. A comparação não comprova sazonalidade nem usa Machine Learning.")
    colunas = ["codigo", "nome", "venda_media_diaria", "media_longa", "tendencia", "aviso_qualidade"]
    if contexto.can("finance.view"):
        colunas += ["classe_abc"]
        st.caption("A Curva ABC mede consumo a custo, não faturamento. Custos ausentes tornam a classificação parcial.")
    else:
        st.info("A Curva ABC a custo exige permissão financeira.")
    _tabela(painel, colunas)
    st.markdown("**Parados e excesso**")
    st.caption("Ausência de histórico não é prova de produto parado.")
    colunas = ["codigo", "nome", "status", "estoque", "dias_sem_venda", "cobertura_dias", "aviso_qualidade"]
    if contexto.can("finance.view"):
        colunas += ["capital_estoque"]
    _tabela(painel.loc[painel["status"].isin(["Parado", "Excesso"])], colunas)


def _relatorios(servico: Any, contexto: Any) -> None:
    """Gera o mês fechado e só exporta após nova autorização no serviço."""
    st.subheader("Relatório mensal")
    anterior = hoje_local().replace(day=1) - pd.Timedelta(days=1)
    mes = st.date_input("Mês de referência", value=anterior.date(), max_value=anterior.date()).replace(day=1)
    st.caption("Exige histórico completo do período e fotografia de estoque no último dia do mês.")
    relatorio = servico.monthly_report(mes)
    periodo = relatorio["periodo"]
    st.write(f"Período: {periodo['inicio']} a {periodo['fim']}")
    resumo = relatorio["resumo"]
    a, b = st.columns(2)
    a.metric("Unidades vendidas", resumo["unidades_vendidas"])
    b.metric("Produtos com venda", resumo["produtos_com_venda"])
    variacao = resumo.get("variacao_unidades_pct")
    if variacao is not None:
        st.metric("Variação de unidades frente ao mês anterior", f"{variacao:.1f}%")
    for aviso in relatorio.get("cautelas", []):
        st.warning(aviso)
    fonte = relatorio.get("fonte", {})
    st.caption(f"Fonte: {fonte.get('origem', 'Importação')} · Atualização: {fonte.get('atualizado_em', 'não informada')}")
    titulos = {"mais_vendidos": "Mais vendidos por quantidade", "estoque": "Estoque e risco",
               "reposicao": "Oportunidades de reposição", "tendencias": "Tendências"}
    for chave, titulo in titulos.items():
        if chave in relatorio:
            st.markdown(f"**{titulo}**")
            _tabela(pd.DataFrame(relatorio[chave]))
    if contexto.can("finance.view") and "financeiro" in relatorio:
        st.markdown("**Indicadores a custo, quando conhecidos**")
        st.write(relatorio["financeiro"])
    if contexto.can("reports.export"):
        st.download_button("Baixar relatório mensal (CSV)", servico.export_monthly_report(mes),
                           file_name=f"relatorio_{mes:%Y_%m}.csv", mime="text/csv", on_click="rerun")


def _importacao(servico: Any, contexto: Any) -> None:
    """Exige abrangência explícita antes de substituir a versão operacional."""
    st.subheader("Importar catálogo e vendas")
    st.info("A importação é completa. O novo lote vira a referência operacional e os lotes anteriores permanecem no histórico.")
    st.caption("Arquivos UTF-8, separador ; e decimal ,. Datas AAAA-MM-DD ou DD/MM/AAAA. Limite de 10 MiB por arquivo.")
    hoje = hoje_local().date()
    with st.form("importar_csv", clear_on_submit=True):
        produtos = st.file_uploader("produtos.csv", type=["csv"])
        vendas = st.file_uploader("vendas.csv", type=["csv"])
        foto = st.date_input("Data observada do estoque", value=hoje, max_value=hoje)
        a, b = st.columns(2)
        inicio = a.date_input("Início do histórico completo", value=hoje.replace(day=1), max_value=hoje)
        fim = b.date_input("Fim do histórico completo", value=hoje, max_value=hoje)
        confirmado = st.checkbox("Conferi que os dois arquivos e o período informado estão completos.")
        importar = st.form_submit_button("Validar e importar", type="primary")
    if importar:
        if produtos is None or vendas is None or not confirmado:
            st.error("Selecione os dois arquivos e confirme a abrangência do histórico.")
        else:
            servico.import_csv(produtos.getvalue(), vendas.getvalue(), foto, inicio, fim)
            st.success("Importação concluída no Supabase. O histórico foi preservado.")
    if _perfil(contexto, "role") != "Master":
        return
    with st.expander("Carregar dados fictícios para validação"):
        st.caption("Cria um lote demonstrativo no banco desta organização. Não consulta o ERP.")
        referencia = st.date_input("Data da demonstração", value=hoje, max_value=hoje)
        confirmar = st.checkbox("Autorizo substituir a referência operacional por este lote fictício.")
        if st.button("Importar demonstração fictícia", disabled=not confirmar):
            inicio = referencia - pd.Timedelta(days=119)
            servico.import_demo(referencia, inicio, referencia)
            st.success("Lote fictício importado. Abra o painel para analisar os resultados.")


def _parametros(servico: Any) -> None:
    """Edita apenas parâmetros do gerente, nunca saldos ou objetos do ERP."""
    st.subheader("Parâmetros de reposição")
    dados = _carregar(servico, "parameters.edit")
    if dados is None:
        return
    produtos = dados["products"].set_index("codigo", drop=False)
    codigo = st.selectbox("Produto para parametrizar", list(produtos.index),
                          format_func=lambda sku: f"{sku} · {produtos.loc[sku, 'nome']}")
    item = produtos.loc[codigo]
    with st.form("parametros_" + codigo):
        prazo = st.number_input("Prazo de entrega (dias)", min_value=0, max_value=2147483647,
                                value=int(item["prazo_entrega_dias"]), step=1)
        seguranca = st.number_input("Estoque de segurança (unidades)", min_value=0, max_value=2147483647,
                                    value=int(item["estoque_seguranca"]), step=1)
        alvo = st.number_input("Cobertura-alvo (dias)", min_value=1, max_value=2147483647,
                              value=int(item["cobertura_alvo_dias"]), step=1)
        if st.form_submit_button("Salvar parâmetros"):
            servico.update_parameters(codigo, {"prazo_entrega_dias": prazo,
                                               "estoque_seguranca": seguranca, "cobertura_alvo_dias": alvo})
            st.success("Parâmetros salvos e alteração registrada na auditoria.")


def _decisoes(servico: Any) -> None:
    """Registra uma avaliação humana e apresenta o histórico autorizado."""
    st.subheader("Decisões de reposição")
    dados = _carregar(servico, "decisions.write")
    if dados is None:
        return
    painel = dados["panel"].set_index("codigo", drop=False)
    with st.form("decisao", clear_on_submit=True):
        codigo = st.selectbox("Produto da decisão", list(painel.index),
                              format_func=lambda sku: f"{sku} · {painel.loc[sku, 'nome']}")
        acao = st.selectbox("Decisão", ["Avaliar compra", "Ignorar"])
        quantidade = st.number_input("Quantidade avaliada", min_value=0, max_value=2147483647, value=0, step=1)
        observacao = st.text_area("Observação opcional", max_chars=500)
        if st.form_submit_button("Registrar decisão"):
            servico.save_decision(codigo, acao, quantidade, observacao)
            st.success("Decisão registrada. Nenhum pedido foi criado.")
    _tabela(pd.DataFrame(servico.list_decisions()))


def _permissoes_formulario(atuais: Mapping[str, bool] | None = None) -> dict[str, bool]:
    """Usa o perfil como ponto inicial ou permite concessões individuais explícitas."""
    atuais = atuais or {}
    herdar = st.checkbox("Usar permissões iniciais do perfil", value=not bool(atuais))
    selecionadas = st.multiselect("Permissões concedidas", list(PERMISSOES),
                                 default=[p for p in PERMISSOES if atuais.get(p, False)],
                                 format_func=lambda p: f"{PERMISSOES[p]} ({p})")
    st.caption("Desmarque a opção acima para aplicar a seleção individual. Administração permanece exclusiva do Master.")
    return {} if herdar else {permissao: permissao in selecionadas for permissao in PERMISSOES}


def _administracao(servico: Any, contexto: Any) -> None:
    """Gerencia contas sem oferecer promoção ou edição de uma conta Master."""
    st.subheader("Administração")
    if _perfil(contexto, "role") != "Master":
        st.error("A administração de usuários é restrita ao Master.")
        return
    usuarios = servico.admin_list_users()
    _tabela(pd.DataFrame(usuarios), ["user_id", "display_name", "role", "active"])
    editaveis = [u for u in usuarios if u.get("role") != "Master"]
    if editaveis and contexto.can("users.manage") and contexto.can("permissions.manage"):
        opcoes = {u["user_id"]: u for u in editaveis}
        alvo = st.selectbox("Usuário para editar", list(opcoes),
                            format_func=lambda uid: str(opcoes[uid].get("display_name", uid)))
        usuario = opcoes[alvo]
        with st.form("editar_usuario_" + alvo):
            nome = st.text_input("Nome de exibição", value=usuario.get("display_name", ""), max_chars=100)
            perfis = ["Administrador", "Operador", "Visualizador"]
            perfil = st.selectbox("Perfil", perfis, index=perfis.index(usuario["role"]))
            ativo = st.checkbox("Conta ativa", value=bool(usuario.get("active")))
            permissoes = usuario.get("permissions", {})
            if isinstance(permissoes, list):
                permissoes = dict.fromkeys(permissoes, True)
            concedidas = _permissoes_formulario(permissoes)
            if st.form_submit_button("Salvar acesso"):
                servico.admin_set_user(alvo, nome, perfil, ativo, concedidas)
                st.success("Acesso atualizado. As próximas operações usarão as novas permissões.")
    if contexto.can("users.manage") and contexto.can("permissions.manage"):
        with st.expander("Autorizar conta existente no Supabase Auth"):
            st.caption("Use o UUID da conta criada no painel Supabase. Um usuário sem autorização não acessa os dados.")
            with st.form("autorizar_usuario", clear_on_submit=True):
                identificador = st.text_input("UUID do usuário", max_chars=36)
                nome = st.text_input("Nome do usuário", max_chars=100)
                perfil = st.selectbox("Perfil inicial", ["Visualizador", "Operador", "Administrador"])
                concedidas = _permissoes_formulario()
                if st.form_submit_button("Autorizar usuário"):
                    servico.admin_set_user(identificador, nome, perfil, True, concedidas)
                    st.success("Usuário autorizado com as permissões selecionadas.")
        with st.expander("Criar conta por operação administrativa protegida"):
            st.caption("Requer chave administrativa configurada somente no servidor. Não envia convite por e-mail.")
            with st.form("criar_usuario", clear_on_submit=True):
                email = st.text_input("E-mail do novo usuário", max_chars=254)
                nome = st.text_input("Nome da nova conta", max_chars=100)
                senha = st.text_input("Senha inicial forte", type="password", autocomplete="new-password")
                perfil = st.selectbox("Perfil da nova conta", ["Visualizador", "Operador", "Administrador"])
                concedidas = _permissoes_formulario()
                if st.form_submit_button("Criar usuário autorizado"):
                    servico.auth.create_user(email, senha, nome, perfil, concedidas)
                    st.success("Conta criada. Entregue a senha por canal seguro e solicite sua alteração no primeiro acesso.")
    if contexto.can("reports.manage"):
        _configurar_relatorios(servico, usuarios)


def _configurar_relatorios(servico: Any, usuarios: list[dict]) -> None:
    """Configura destinatários e mantém envios reais dependentes de autorização."""
    st.markdown("**Relatórios mensais por e-mail**")
    configuracao = servico.reports_config()
    with st.form("configurar_relatorios"):
        habilitado = st.checkbox("Habilitar rotina mensal", value=bool(configuracao.get("enabled", False)))
        simulado = st.checkbox("Modo de simulação, sem envio de e-mail", value=bool(configuracao.get("dry_run", True)))
        candidatos = {u["user_id"]: u for u in usuarios if u.get("active")}
        selecionados = st.multiselect("Destinatários autorizados", list(candidatos),
                                      default=[u for u in configuracao.get("recipients", []) if u in candidatos],
                                      format_func=lambda uid: str(candidatos[uid].get("display_name", uid)))
        dia = st.number_input("Dia do envio mensal", min_value=1, max_value=28,
                              value=int(configuracao.get("send_day", 1)), step=1)
        blocos = st.multiselect("Blocos autorizados do relatório", ["estoque", "reposicao", "tendencias", "financeiro"],
                               default=configuracao.get("blocks", ["estoque", "reposicao", "tendencias"]))
        st.caption("A execução revalida conta ativa e reports.receive. Valores financeiros não são enviados por e-mail.")
        confirmado = st.checkbox("Autorizo estes destinatários para os envios reais quando o provedor estiver configurado.")
        if st.form_submit_button("Salvar rotina de relatórios"):
            if not simulado and not confirmado:
                st.error("Confirme os destinatários antes de habilitar envios reais.")
            else:
                servico.save_reports_config(enabled=habilitado, dry_run=simulado, recipients=selecionados,
                                            blocks=blocos, send_day=int(dia))
                st.success("Configuração salva. O agendador externo executa a rotina mensal.")
    st.caption("Padrão: dia 1, às 6h de America/Fortaleza. Provedor e remetente devem ser configurados fora do código.")
    _tabela(pd.DataFrame(servico.report_runs()))


def run_app(service_factory: Callable | None = None) -> None:
    """Monta a aplicação autenticada; a fábrica injetável permite testes isolados."""
    st.set_page_config(page_title="Gerente Inteligente de Estoque", page_icon="📦", layout="wide")
    st.title("📦 Gerente Inteligente de Estoque")
    st.caption("Acompanhe o estoque, entenda as sugestões e decida com dados.")
    if service_factory is None:
        from web.service import create_service

        service_factory = create_service
    try:
        servico = service_factory(st.session_state)
    except ConfigurationError:
        st.info("Configuração pendente. Defina SUPABASE_URL e SUPABASE_PUBLISHABLE_KEY nos Secrets do aplicativo.")
        st.caption("Consulte o guia de publicação do repositório. Não informe credenciais no chat ou no GitHub.")
        return
    if servico.auth.session is None:
        _login(servico)
        return
    try:
        contexto = servico.context()
    except (AuthenticationError, AuthorizationError):
        _encerrar_sessao(servico)
        st.warning("Sua sessão não está autorizada. Entre novamente ou procure o Master.")
        _login(servico)
        return
    except AppError:
        st.error("Não foi possível confirmar seu acesso. Tente novamente em instantes.")
        return
    _conta(servico, contexto)
    _vigiar_autorizacao(servico, _assinatura(contexto))
    paginas = {
        "Painel": ("dashboard.view", _painel),
        "Estoque": ("inventory.view", _estoque),
        "Reposição": ("replenishment.view", _reposicao),
        "Tendências": ("trends.view", _tendencias),
        "Relatórios": ("reports.view", _relatorios),
        "Importação": ("inventory.import", _importacao),
        "Parâmetros": ("parameters.edit", lambda s, c: _parametros(s)),
        "Decisões": ("decisions.write", lambda s, c: _decisoes(s)),
        "Auditoria": ("audit.view", lambda s, c: _tabela(pd.DataFrame(s.admin_audit()))),
    }
    permitidas = {nome: handler for nome, (permissao, handler) in paginas.items() if contexto.can(permissao)}
    if _perfil(contexto, "role") == "Master":
        permitidas["Administração"] = _administracao
    if not permitidas:
        st.info("Sua conta está ativa, mas nenhuma funcionalidade foi liberada. Solicite acesso ao Master.")
        return
    pagina = st.sidebar.radio("Navegação", list(permitidas))
    try:
        permitidas[pagina](servico, contexto)
    except (AuthenticationError, AuthorizationError):
        st.error("Acesso negado. Sua sessão ou suas permissões podem ter mudado. Atualize a página.")
    except ValueError as erro:
        st.warning(str(erro))
    except AppError:
        st.error("Não foi possível concluir a operação. Confira a configuração e tente novamente.")
