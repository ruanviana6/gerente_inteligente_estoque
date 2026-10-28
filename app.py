"""Interface Streamlit do Gerente Inteligente de Estoque.

Execução: streamlit run app.py
Este arquivo só coordena carregamento, filtros e visualização. Regras de compra
estão em motor.py e persistência em storage.py. Não há acesso ao GestãoClick.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from dados import carregar_de_csv, exportar_csv_seguro, gerar_dados_exemplo, hoje_local
from motor import gerar_painel, somar_valores_financeiros
from storage import carregar_base, listar_decisoes, registrar_decisao, salvar_importacao

st.set_page_config(
    page_title="Gerente Inteligente de Estoque", page_icon="📦",
    layout="wide", initial_sidebar_state="expanded",
)


def moeda(valor: float) -> str:
    """Apresenta valores estimados em reais, sem inventar valores ausentes."""
    if pd.isna(valor):
        return "Não disponível"
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def exportar_csv(tabela: pd.DataFrame) -> bytes:
    """Exporta em formato brasileiro, neutralizando fórmulas vindas dos CSVs."""
    return exportar_csv_seguro(tabela)


def abrir_dados() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str, bool]:
    """Seleciona demo ou base importada; permite importar CSVs completos.

    Uma importação substitui as vendas locais anteriores e registra snapshot
    do dia. Arquivos de clientes e dados fiscais não são necessários no MVP.
    """
    st.sidebar.header("Origem dos dados")
    origem = st.sidebar.radio(
        "Modo", ["Demonstração fictícia", "CSV salvo localmente"],
        help="A demonstração nunca grava dados empresariais.",
    )
    if origem == "Demonstração fictícia":
        st.sidebar.info("Dados simulados e reproduzíveis; nenhum acesso externo.")
        p, v, s = gerar_dados_exemplo()
        return p, v, s, "Demonstração — dados fictícios", False

    st.sidebar.markdown("**Importação completa de CSVs**")
    arquivo_p = st.sidebar.file_uploader("produtos.csv", type=["csv"], key="produtos")
    arquivo_v = st.sidebar.file_uploader("vendas.csv", type=["csv"], key="vendas")
    sep = st.sidebar.selectbox("Separador", [";", ","])
    decimal = st.sidebar.selectbox("Separador decimal", [",", "."])
    if st.sidebar.button("Validar e importar", type="primary"):
        if arquivo_p is None or arquivo_v is None:
            st.sidebar.error("Selecione os dois arquivos para importar.")
        else:
            try:
                # Valida ambos antes de tocar no banco; operação posterior é atômica.
                p, v = carregar_de_csv(arquivo_p.getvalue(), arquivo_v.getvalue(), sep, decimal)
                salvar_importacao(p, v)
                st.sidebar.success("Importação concluída. Histórico diário registrado.")
            except ValueError as erro:
                st.sidebar.error(f"Importação rejeitada: {erro}")
            except (sqlite3.Error, OSError):
                st.sidebar.error("Não foi possível salvar a importação. Verifique o banco local e tente novamente.")

    try:
        p, v, s, atualizado = carregar_base()
    except (sqlite3.Error, pd.errors.DatabaseError, OSError):
        st.error("Não foi possível abrir o banco local. Verifique o arquivo e as permissões de acesso.")
        st.stop()
    if p.empty:
        st.warning("Nenhum CSV importado. Selecione dois arquivos no menu lateral.")
        st.stop()
    momento = atualizado or "Data não disponível"
    st.sidebar.caption("Última importação: " + momento)
    st.sidebar.warning(
        "Use apenas dados autorizados pela empresa. O banco fica no computador; "
        "o projeto público não deve conter arquivos reais."
    )
    return p, v, s, "CSV local — última importação: " + momento, True


def painel_geral(painel: pd.DataFrame, origem: str) -> None:
    """Resume disponibilidade, reposição e capital sem esconder dados faltantes."""
    st.subheader("Visão geral")
    st.caption(origem)
    contagem = painel["status"].value_counts()
    colunas = st.columns(6)
    for coluna, (nome, icone) in zip(
        colunas,
        [("Ruptura", "🔴"), ("Crítico", "🔴"), ("Alto", "🟠"),
         ("Atenção", "🟡"), ("OK", "🟢"), ("Excesso", "🔵")],
    ):
        coluna.metric(f"{icone} {nome}", int(contagem.get(nome, 0)))

    n_pedidos = int((painel["quantidade_sugerida"] > 0).sum())
    n_risco = int(painel["risco_prazo"].sum())
    com_custo = painel["capital_estoque"].notna()
    capital = somar_valores_financeiros(painel.loc[com_custo, "capital_estoque"])
    valores_parados = painel.loc[painel["status"].isin(["Parado", "Excesso"]), "capital_estoque"]
    parado = 0.0 if valores_parados.empty else somar_valores_financeiros(valores_parados)
    a, b, c, d = st.columns(4)
    a.metric("Produtos com sugestão", n_pedidos)
    b.metric("Risco antes da entrega", n_risco)
    c.metric("Capital conhecido em estoque", moeda(capital) if com_custo.any() else "Não informado")
    d.metric("Capital em parados/excesso", moeda(parado) if com_custo.any() else "Não informado")
    if not com_custo.all():
        st.info("Capital parcial: há custos ausentes ou valores não calculáveis.")
    if painel["financeiro_limite_excedido"].any():
        st.warning("Valores financeiros fora do limite de cálculo. Revise os custos importados; totais e ABC podem estar indisponíveis.")
    if painel["dias_disponibilidade_desconhecida"].gt(0).any():
        st.info(
            "Médias são estimativas: faltam snapshots de disponibilidade em parte "
            "do histórico. Dias sem venda só são excluídos quando houve ruptura comprovada."
        )
    if painel["requer_revisao"].any():
        st.warning("Há produtos com histórico insuficiente ou disponibilidade incerta. Confira os avisos antes de decidir.")
    if painel["abc_parcial"].any():
        st.caption("A Curva ABC considera somente os custos conhecidos e pode estar incompleta.")
    st.markdown("**Atenções prioritárias**")
    cols = ["icone", "status", "nome", "fornecedor", "estoque",
            "cobertura_dias", "prazo_entrega_dias", "risco_prazo", "quantidade_sugerida", "aviso_qualidade"]
    st.dataframe(
        painel.loc[painel["quantidade_sugerida"] > 0, cols].head(12),
        hide_index=True, use_container_width=True,
        column_config={
            "cobertura_dias": st.column_config.NumberColumn("Cobertura (dias)", format="%.1f"),
            "risco_prazo": "Risco no prazo",
            "quantidade_sugerida": "Pedir (un.)",
        },
    )


def lista_reposicao(painel: pd.DataFrame) -> None:
    """Exibe sugestões por fornecedor e permite exportar compras para conferência."""
    st.subheader("Lista de reposição")
    st.caption(
        "Sugestões são cálculos de apoio: NÃO criam pedidos. Confirme embalagens, "
        "mínimos do fornecedor e datas de chegada antes de comprar."
    )
    fornecedores = sorted(painel["fornecedor"].unique().tolist())
    escolhidos = st.multiselect("Fornecedores", fornecedores, default=fornecedores)
    somente = st.checkbox("Somente itens com sugestão de compra", value=True)
    vista = painel.loc[painel["fornecedor"].isin(escolhidos)].copy()
    if somente:
        vista = vista.loc[vista["quantidade_sugerida"] > 0]
    colunas = [
        "fornecedor", "codigo", "nome", "status", "estoque", "reservas",
        "pedidos_pendentes", "venda_media_diaria", "cobertura_dias",
        "prazo_entrega_dias", "estoque_seguranca", "ponto_pedido",
        "posicao_estoque", "estoque_alvo", "risco_prazo", "quantidade_sugerida",
        "demanda_estimavel", "requer_revisao", "aviso_qualidade",
    ]
    tabela = vista[colunas].rename(columns={
        "fornecedor": "Fornecedor", "codigo": "SKU", "nome": "Produto",
        "status": "Status", "estoque": "Estoque", "reservas": "Reservas",
        "pedidos_pendentes": "Pendente", "venda_media_diaria": "Venda/dia",
        "cobertura_dias": "Cobertura (dias)", "prazo_entrega_dias": "Prazo (dias)",
        "estoque_seguranca": "Segurança", "ponto_pedido": "Ponto de pedido",
        "posicao_estoque": "Posição", "estoque_alvo": "Alvo",
        "risco_prazo": "Risco no prazo", "quantidade_sugerida": "Pedir (un.)",
        "demanda_estimavel": "Demanda estimável", "requer_revisao": "Revisão necessária",
        "aviso_qualidade": "Limitações do cálculo",
    })
    st.dataframe(tabela, hide_index=True, use_container_width=True)
    st.download_button(
        "Baixar lista de compras (CSV)", exportar_csv(tabela),
        file_name="sugestao_reposicao.csv", mime="text/csv",
    )
    if not vista.empty:
        st.markdown("**Resumo por fornecedor**")
        resumo = (vista.groupby("fornecedor", as_index=False)
                       .agg(produtos=("codigo", "count"),
                            unidades_sugeridas=("quantidade_sugerida", "sum")))
        st.dataframe(resumo, hide_index=True, use_container_width=True)


def detalhe_produto(
    painel: pd.DataFrame, vendas: pd.DataFrame,
    snapshots: pd.DataFrame, base_local: bool,
) -> None:
    """Mostra o raciocínio da sugestão, histórico e feedback do operador."""
    st.subheader("Detalhe do produto")
    opcoes = painel.set_index("codigo")["nome"].to_dict()
    codigo = st.selectbox(
        "Produto", list(opcoes), format_func=lambda c: c + " · " + opcoes[c]
    )
    item = painel.loc[painel["codigo"] == codigo].iloc[0]
    a, b, c, d = st.columns(4)
    a.metric("Estoque", int(item["estoque"]))
    b.metric("Venda média/dia", f"{item['venda_media_diaria']:.2f}" if item["demanda_estimavel"] else "Não estimável")
    c.metric("Cobertura", f"{item['cobertura_dias']:.1f} dias"
             if pd.notna(item["cobertura_dias"]) else "Indefinida")
    d.metric("Sugestão (un.)", int(item["quantidade_sugerida"]) if item["demanda_estimavel"] else "Revisar dados")
    if item["requer_revisao"]:
        st.warning(str(item["aviso_qualidade"]))

    st.markdown("**Explicação auditável da sugestão**")
    st.write(
        f"Prazo de entrega: **{int(item['prazo_entrega_dias'])} dias**; "
        f"segurança: **{int(item['estoque_seguranca'])} un.**; "
        f"pendentes: **{int(item['pedidos_pendentes'])} un.**; "
        f"reservas: **{int(item['reservas'])} un.**."
    )
    st.write(
        f"Posição = {int(item['estoque'])} + {int(item['pedidos_pendentes'])} "
        f"− {int(item['reservas'])} = **{int(item['posicao_estoque'])} un.**"
    )
    st.write(
        f"Alvo (prazo ou cobertura desejada, o maior) + segurança = "
        f"**{int(item['estoque_alvo'])} un.**; comprar = "
        f"máximo(0, alvo − posição) = **{int(item['quantidade_sugerida'])} un.**"
    )
    st.write(
        f"**Status:** {item['icone']} {item['status']} · "
        f"**Tendência:** {item['tendencia']} · **Classe ABC:** {item['classe_abc']}."
    )
    if int(item["dias_ruptura_conhecidos"]) > 0:
        st.warning(
            f"{int(item['dias_ruptura_conhecidos'])} dia(s) com ruptura "
            "comprovada excluídos da média curta."
        )
    if int(item["dias_disponibilidade_desconhecida"]) > 0:
        st.caption("Há dias sem informação de disponibilidade. Previsão não é certeza.")

    fim = hoje_local()
    periodo = pd.date_range(fim - pd.Timedelta(days=89), fim)
    serie = (vendas.loc[vendas["codigo"] == codigo]
                  .groupby("data")["quantidade"].sum()
                  .reindex(periodo, fill_value=0))
    serie.index.name = "Data"
    st.markdown("**Unidades vendidas — últimos 90 dias**")
    st.line_chart(serie)
    foto = snapshots.loc[snapshots["codigo"] == codigo].sort_values("data")
    st.markdown("**Snapshots realmente registrados**")
    if foto.empty:
        st.info("Ainda não há snapshots deste produto.")
    else:
        st.line_chart(foto.set_index("data")["estoque"])
        st.caption("Pontos registrados, não um histórico reconstruído de entradas e saídas.")

    if base_local:
        st.markdown("**Feedback da recomendação (não gera pedido)**")
        with st.form("feedback_produto"):
            acao = st.selectbox("Decisão", ["Avaliar compra", "Ignorar"])
            observacao = st.text_input("Observação opcional", max_chars=500)
            if st.form_submit_button("Registrar decisão"):
                try:
                    registrar_decisao(codigo, int(item["quantidade_sugerida"]), acao, observacao)
                    st.success("Feedback registrado localmente.")
                except ValueError as erro:
                    st.error(str(erro))
                except (sqlite3.Error, OSError):
                    st.error("Não foi possível registrar a decisão. Verifique o banco local e tente novamente.")


def parados_excesso(painel: pd.DataFrame) -> None:
    """Separa capital potencialmente imobilizado de dados sem histórico."""
    st.subheader("Parados e excessos")
    st.caption(
        "Parado = última venda há pelo menos 60 dias; excesso = cobertura "
        "acima de 90 dias. Sem histórico não é automaticamente produto parado."
    )
    vista = painel.loc[painel["status"].isin(["Parado", "Excesso"])].copy()
    st.dataframe(
        vista[["nome", "fornecedor", "status", "estoque", "dias_sem_venda",
               "cobertura_dias", "capital_estoque"]],
        hide_index=True, use_container_width=True,
    )


def tendencias(painel: pd.DataFrame) -> None:
    """Distingue variação de demanda observada da análise sazonal futura."""
    st.subheader("Tendências e Curva ABC")
    st.caption(
        "Média curta de 30 dias comparada à de 90 dias. Isso NÃO comprova "
        "sazonalidade anual e não é um modelo de ML."
    )
    st.dataframe(
        painel[["nome", "venda_media_diaria", "media_longa",
                "tendencia", "classe_abc", "dias_ruptura_conhecidos",
                "dias_disponibilidade_desconhecida", "aviso_qualidade"]],
        hide_index=True, use_container_width=True,
    )


def main() -> None:
    """Monta a navegação do MVP; não executa operações no ERP."""
    st.title("📦 Gerente Inteligente de Estoque")
    st.caption("Entenda o risco. Saiba o que pedir. Decida com dados.")
    st.warning("MVP local sem login ou permissões por usuário. Não publique esta versão com dados empresariais.")
    produtos, vendas, snapshots, origem, base_local = abrir_dados()
    painel = gerar_painel(produtos, vendas, snapshots)
    abas = st.tabs([
        "📊 Painel", "🛒 Reposição", "🔎 Produto",
        "📦 Parados e excesso", "📈 Tendências", "🧾 Decisões",
    ])
    with abas[0]:
        painel_geral(painel, origem)
    with abas[1]:
        lista_reposicao(painel)
    with abas[2]:
        detalhe_produto(painel, vendas, snapshots, base_local)
    with abas[3]:
        parados_excesso(painel)
    with abas[4]:
        tendencias(painel)
    with abas[5]:
        st.subheader("Histórico de decisões")
        if base_local:
            try:
                st.dataframe(listar_decisoes(), hide_index=True, use_container_width=True)
            except (sqlite3.Error, pd.errors.DatabaseError, OSError):
                st.error("Não foi possível consultar as decisões. Verifique o banco local e tente novamente.")
        else:
            st.info("O registro de decisões fica disponível no modo CSV local.")


if __name__ == "__main__":
    main()

