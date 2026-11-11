"""Verifica vendas efetivas, crescimento, sazonalidade e cestas sem ERP real."""

import copy
import json
from datetime import date, timedelta

import pytest

from analytics import sales
from analytics.demo import gerar_vendas_ficticias
from analytics.sales import analisar_vendas, normalizar_linhas


def linha(**alteracoes):
    """Produz uma linha fictícia mínima no contrato canônico autorizado."""
    return {
        "source_id": "fonte-ficticia", "store_id": "loja-ficticia", "document_id": "pedido-1",
        "line_id": "1", "product_id": "produto-a", "sku": "001", "product_name": "Produto fictício A",
        "category": "Categoria fictícia", "supplier": "Fornecedor fictício", "sale_date": "2026-09-10",
        "status": "completed", "quantity": 2, "returned_quantity": 0,
        "source_updated_at": "2026-09-10T18:00:00Z", **alteracoes,
    }


def analisar(linhas, **opcoes):
    """Usa mês fictício fechado com abrangência de dois períodos completos."""
    return analisar_vendas(linhas, "2026-09-01", "2026-09-30",
                           coverage_start="2026-08-01", coverage_end="2026-09-30", **opcoes)


def pedidos_cesta():
    """Cria 20 pedidos: A em dez, B em oito, ambos em seis e C nos demais."""
    linhas = []
    for numero in range(20):
        produtos = []
        if numero < 10:
            produtos.append("A")
        if numero < 6 or 10 <= numero < 12:
            produtos.append("B")
        if not produtos:
            produtos.append("C")
        for produto in produtos:
            linhas.append(linha(document_id=f"pedido-{numero}", line_id=produto, product_id=produto,
                                sku=produto, product_name=f"Produto {produto}"))
    return linhas


@pytest.mark.parametrize("filtro", [{"product_id": "A"}, {"category": "Categoria fictícia"}])
def test_cesta_com_status_desconhecido_fora_do_filtro_permanece_bloqueada(filtro):
    """Os filtros da tabela não tornam completo o denominador de todos os pedidos."""
    linhas = pedidos_cesta()
    categoria = linhas[0]["category"]
    if "category" in filtro:
        filtro["category"] = categoria
    linhas.append(linha(document_id="incerto", line_id="Z", product_id="Z", sku="Z",
                        product_name="Outro produto", category="Outra categoria", status="unknown"))
    resultado = analisar(linhas, **filtro)
    assert not resultado["cestas"]["disponivel"]
    assert resultado["cestas"]["pares"] == []
    assert not any("aparecem juntos" in insight["titulo"] for insight in resultado["insights"])


@pytest.mark.parametrize("nome", [None, "", "   "])
def test_produto_sem_nome_usa_rotulo_explicito_sem_descartar_vendas(nome):
    """Uma linha persistida sem nome utiliza o SKU, sem fingir que é o nome oficial."""
    dados = linha(product_name=nome, sku="001")
    resultado = analisar([dados])
    assert resultado["resumo"]["unidades"] == 2
    assert resultado["produtos"][0]["nome"] == "SKU 001 (nome indisponível)"


def serie_crescimento():
    """Cria quatro semanas com crescimento nas duas metades comparáveis."""
    inicio = date(2026, 9, 3)
    return [linha(document_id=f"pedido-{deslocamento}", sale_date=(inicio + timedelta(days=deslocamento)).isoformat(),
                  quantity=1 if deslocamento < 14 else 2) for deslocamento in range(28)]


def test_duplicacao_identica_nao_soma_vendas():
    """A mesma chave de linha é contada uma única vez."""
    resultado = analisar([linha(), linha()])
    assert resultado["resumo"]["unidades"] == 2
    assert resultado["resumo"]["duplicatas_descartadas"] == 1
    assert resultado["resumo"]["pedidos_concluidos"] == 1


def test_ultima_versao_cancelada_prevalece_independente_da_ordem():
    """Cancelamento posterior remove a venda analítica, sem alterar saldo."""
    antiga = linha()
    nova = linha(status="cancelled", source_updated_at="2026-09-11T12:00:00Z")
    assert analisar([antiga, nova])["resumo"]["unidades"] == 0
    assert analisar([nova, antiga])["resumo"]["unidades"] == 0
    assert analisar([nova, antiga])["resumo"]["linhas_canceladas"] == 1


@pytest.mark.parametrize("instante", [None, "2026-09-10T18:00:00Z"])
def test_versao_conflitante_sem_ordem_comprovavel_e_recusada(instante):
    """Não se escolhe versão pela posição acidental na lista recebida."""
    with pytest.raises(ValueError, match="conflitantes"):
        analisar([linha(), linha(quantity=9, source_updated_at=instante)])


def test_transicao_separating_para_completed_nao_duplica():
    """O pedido intermediário não representa venda nem uma baixa adicional."""
    antes = linha(status="separating")
    depois = linha(status="completed", source_updated_at="2026-09-11T12:00:00Z")
    assert analisar([antes])["resumo"]["unidades"] == 0
    assert analisar([antes, depois])["resumo"]["unidades"] == 2


@pytest.mark.parametrize("status", ["potential", "separating", "cancelled", "returned", "unknown"])
def test_estados_nao_concluidos_nao_sao_vendas(status):
    """Nem pedido potencial nem devolução integral são tratados como compra efetiva."""
    resultado = analisar([linha(status=status)])
    assert resultado["resumo"]["unidades"] == 0
    assert resultado["resumo"]["pedidos_concluidos"] == 0


def test_devolucao_parcial_e_integral():
    """A venda concluída fica líquida da devolução informada pela origem."""
    parcial = analisar([linha(quantity=5, returned_quantity=2)])
    integral = analisar([linha(quantity=5, returned_quantity=5)])
    assert parcial["resumo"]["unidades"] == 3
    assert parcial["resumo"]["unidades_devolvidas"] == 2
    assert integral["resumo"]["pedidos_concluidos"] == 0


@pytest.mark.parametrize("alteracoes", [
    {"quantity": -1}, {"quantity": float("nan")}, {"quantity": float("inf")}, {"quantity": True},
    {"quantity": None}, {"returned_quantity": 3}, {"sale_date": "10/09/2026"},
    {"source_updated_at": "2026-09-10T18:00:00"}, {"product_id": ""}, {"status": "Concretizada"},
    {"status": []}, {"status": {}},
])
def test_entradas_invalidas_sao_rejeitadas(alteracoes):
    """Campos inválidos não viram zeros, datas inferidas nem status adivinhados."""
    with pytest.raises(ValueError):
        analisar([linha(**alteracoes)])


def test_campo_pessoal_ou_financeiro_fora_do_contrato_e_rejeitado():
    """A fronteira analítica não aceita informações desnecessárias de clientes ou custos."""
    for campo in ("customer_name", "cpf", "email", "unit_cost", "revenue"):
        with pytest.raises(ValueError, match="fora do contrato"):
            analisar([linha(**{campo: "segredo-ficticio"})])


def test_isolamento_fontes_lojas_exige_escolha_explicita():
    """SKUs e IDs iguais de origens distintas não são consolidados por acidente."""
    linhas = [linha(), linha(source_id="outra-fonte", quantity=90), linha(store_id="outra-loja", quantity=40)]
    with pytest.raises(ValueError, match="única fonte e loja"):
        analisar(linhas)
    resultado = analisar(linhas, source_id="fonte-ficticia", store_id="loja-ficticia")
    assert resultado["resumo"]["unidades"] == 2
    assert resultado["origem"] == {"source_id": "fonte-ficticia", "store_id": "loja-ficticia"}


def test_series_diaria_semanal_mensal_respeitam_periodo():
    """Períodos completos admitem dias com zero sem misturar semanas ou meses."""
    resultado = analisar([linha(), linha(document_id="pedido-2", sale_date="2026-09-11", quantity=3)])
    assert len(resultado["series"]["diaria"]) == 30
    assert sum(item["unidades"] for item in resultado["series"]["semanal"]) == 5
    assert resultado["series"]["mensal"] == [{"data": "2026-09", "unidades": 5}]


def test_sem_abrangencia_nao_preenche_zeros_nem_exibe_crescimento():
    """Dias não documentados não viram dias de vendas iguais a zero."""
    resultado = analisar_vendas([linha()], "2026-09-01", "2026-09-30")
    assert len(resultado["series"]["diaria"]) == 1
    assert resultado["comparacao"]["variacao_percentual"] is None
    assert not resultado["comparacao"]["comparavel"]


def test_base_zero_nao_gera_percentual_infinito():
    """Produto sem venda anterior recebe diferença absoluta e limitação explícita."""
    resultado = analisar([linha()])
    assert resultado["comparacao"]["diferenca_absoluta"] == 2
    assert resultado["comparacao"]["variacao_percentual"] is None
    assert resultado["produtos"][0]["variacao_percentual"] is None
    assert not resultado["produtos"][0]["sustentado"]
    json.dumps(resultado, allow_nan=False)


def test_crescimento_sustentado_exige_duas_metades_e_amostra():
    """Crescimento em cada metade de dois períodos equivalentes é explicável."""
    resultado = analisar_vendas(serie_crescimento(), "2026-09-17", "2026-09-30",
                               coverage_start="2026-09-03", coverage_end="2026-09-30")
    produto = resultado["produtos"][0]
    assert produto["variacao_percentual"] == 100
    assert produto["diferenca_absoluta"] == 14
    assert produto["sustentado"]
    assert produto["classificacao"] == "Crescimento sustentado"
    assert resultado["insights"]


def test_alta_pontual_nao_vira_crescimento_sustentado():
    """Uma única concentração no final do período não prova alta persistente."""
    linhas = serie_crescimento()
    for item in linhas[14:21]:
        item["quantity"] = 0
    for item in linhas[21:]:
        item["quantity"] = 5
    resultado = analisar_vendas(linhas, "2026-09-17", "2026-09-30",
                               coverage_start="2026-09-03", coverage_end="2026-09-30")
    assert resultado["produtos"][0]["variacao_percentual"] > 0
    assert not resultado["produtos"][0]["sustentado"]


def test_snapshot_zero_sinaliza_demanda_censurada_e_nao_altera_unidades():
    """Saldo zero é limitação de demanda, sem retirar vendas efetivas do total."""
    snapshots = [{"source_id": "fonte-ficticia", "store_id": "loja-ficticia", "product_id": "produto-a",
                  "date": "2026-09-20", "stock": 0, "coverage_days": 2, "lead_time_days": 10}]
    resultado = analisar_vendas(serie_crescimento(), "2026-09-17", "2026-09-30",
                               coverage_start="2026-09-03", coverage_end="2026-09-30", snapshots=snapshots)
    produto = resultado["produtos"][0]
    assert resultado["resumo"]["unidades"] == 28
    assert produto["dias_snapshot_zero"] == 1
    assert not produto["sustentado"]
    assert any("cobertura abaixo do prazo" in insight["titulo"] for insight in resultado["insights"])


def test_snapshot_de_outra_fonte_nao_contamina_risco():
    """A associação de saldo exige a mesma fonte, loja e produto."""
    resultado = analisar([linha()], snapshots=[{"source_id": "outra-fonte", "store_id": "loja-ficticia",
                                               "product_id": "produto-a", "date": "2026-09-10", "stock": 0}])
    assert resultado["produtos"][0]["dias_snapshot_zero"] is None


def test_formulas_de_cesta_tem_denominador_de_pedidos():
    """Suporte, duas confianças e lift correspondem a pedidos independentes."""
    resultado = analisar(pedidos_cesta())
    cestas = resultado["cestas"]
    par = cestas["pares"][0]
    assert cestas["total_pedidos"] == 20
    assert (par["produto_a"], par["produto_b"]) == ("A", "B")
    assert par["pedidos_juntos"] == 6
    assert par["suporte"] == pytest.approx(0.3)
    assert par["confianca_a_b"] == pytest.approx(0.6)
    assert par["confianca_b_a"] == pytest.approx(0.75)
    assert par["lift"] == pytest.approx(1.5)


def test_produtos_no_mesmo_dia_em_pedidos_distintos_nao_formam_par():
    """A data não substitui identificador de documento para análise de cesta."""
    linhas = [linha(document_id=str(i), product_id="A" if i % 2 else "B") for i in range(20)]
    assert analisar(linhas)["cestas"]["pares"] == []


def test_linhas_repetidas_do_produto_no_pedido_nao_inflam_suporte():
    """Quantidade e número de linhas não são número de pedidos com o produto."""
    linhas = pedidos_cesta()
    linhas.append(linha(document_id="pedido-0", line_id="A-extra", product_id="A", sku="A"))
    assert analisar(linhas)["cestas"]["pares"][0]["suporte"] == pytest.approx(0.3)


def test_filtro_produto_nao_altera_denominador_da_cesta():
    """Selecionar A mostra seus pares com o total de pedidos do escopo original."""
    resultado = analisar(pedidos_cesta(), product_id="A")
    assert resultado["resumo"]["pedidos_concluidos"] == 10
    assert resultado["cestas"]["total_pedidos"] == 20
    assert resultado["cestas"]["pares"][0]["suporte"] == pytest.approx(0.3)


def test_amostra_pequena_nao_exibe_associacao():
    """Dois pedidos não sustentam recomendação de compras conjuntas."""
    resultado = analisar([linha(), linha(document_id="pedido-2")])
    assert not resultado["cestas"]["disponivel"]
    assert resultado["cestas"]["pares"] == []


def test_pares_raros_sao_ocultados():
    """Ao menos três coocorrências são necessárias mesmo com vinte pedidos."""
    linhas = pedidos_cesta()
    for item in linhas:
        if item["product_id"] == "B" and int(item["document_id"].split("-")[1]) >= 2:
            item["status"] = "potential"
    assert analisar(linhas)["cestas"]["pares"] == []


def test_guardrail_de_cesta_grande_recusa_resultado_parcial():
    """Cestas grandes retornam limite explícito em vez de truncar silenciosamente."""
    linhas = pedidos_cesta() + [linha(document_id="grande", line_id=str(i), product_id=str(i)) for i in range(101)]
    resultado = analisar(linhas)
    assert not resultado["cestas"]["disponivel"]
    assert "100 produtos" in resultado["cestas"]["motivo"]


def test_guardrail_total_de_pares(monkeypatch):
    """Limita o trabalho quadrático da associação sem alterar métricas de vendas."""
    monkeypatch.setattr(sales, "MAX_PARES_PROCESSADOS", 1)
    resultado = analisar(pedidos_cesta())
    assert not resultado["cestas"]["disponivel"]
    assert resultado["resumo"]["unidades"] > 0


def test_curto_historico_nao_afirma_sazonalidade():
    """Variação mensal de um único mês não demonstra recorrência."""
    sazonalidade = analisar(pedidos_cesta())["sazonalidade"]
    assert sazonalidade["classificacao"] == "Variação observada"
    assert sazonalidade["recorrencias"] == []
    assert sazonalidade["anos_completos"] == []
    assert set(sazonalidade) >= {"mes", "semana", "dia_semana", "trimestre"}


def test_recorrencia_so_com_dois_ciclos_civis_comparaveis():
    """Dois anos completos permitem observação recorrente, sem previsão de vendas."""
    inicio, fim = date(2024, 1, 1), date(2025, 12, 31)
    linhas = [linha(document_id=f"p-{i}", sale_date=(inicio + timedelta(days=i)).isoformat(),
                    quantity=4 if (inicio + timedelta(days=i)).month == 11 else 1)
              for i in range((fim - inicio).days + 1)]
    resultado = analisar_vendas(linhas, inicio, fim, coverage_start=inicio, coverage_end=fim)
    sazonalidade = resultado["sazonalidade"]
    assert sazonalidade["anos_completos"] == [2024, 2025]
    assert [item["mes"] for item in sazonalidade["recorrencias"]] == [11]
    assert "não é previsão" in sazonalidade["recorrencias"][0]["rotulo"]


def test_demonstracao_e_deterministica_explicitamente_ficticia():
    """A amostra ajuda a testar UI sem representar vendas empresariais."""
    argumentos = ("fonte-demo", "loja-demo", date(2026, 8, 1), date(2026, 9, 30))
    linhas = gerar_vendas_ficticias(*argumentos)
    assert linhas == gerar_vendas_ficticias(*argumentos)
    assert all(item["document_id"].startswith("DEMO-") for item in linhas)
    assert normalizar_linhas(linhas)[0]
    resultado = analisar(linhas)
    assert resultado["resumo"]["pedidos_concluidos"] == 90
    assert resultado["cestas"]["pares"]


def test_entrada_nao_e_modificada_e_saida_e_json_finito():
    """Não há mutação do payload sincronizado nem valores inválidos para apresentação."""
    linhas = pedidos_cesta()
    original = copy.deepcopy(linhas)
    resultado = analisar(linhas)
    assert linhas == original
    json.dumps(resultado, allow_nan=False)


def test_dados_vazios_tem_limites_claros():
    """Sem linhas, não surgem crescimento, cestas ou sazonalidade fictícios."""
    resultado = analisar_vendas([], "2026-09-01", "2026-09-30")
    assert resultado["resumo"]["unidades"] == 0
    assert resultado["produtos"] == []
    assert resultado["insights"] == []
    assert resultado["series"]["diaria"] == []
    assert not resultado["cestas"]["disponivel"]


@pytest.mark.parametrize("inicio,fim", [("2026-09-30", "2026-09-01"), ("2000-01-01", "2026-01-01")])
def test_periodo_invertido_ou_excessivo_e_recusado(inicio, fim):
    """O tamanho da análise permanece limitado e o período deve ser ordenado."""
    with pytest.raises(ValueError, match="3660"):
        analisar_vendas([], inicio, fim)


def test_limite_de_amostra_nao_pode_ser_afrouxado():
    """A UI não pode reduzir amostra mínima abaixo da regra definida."""
    with pytest.raises(ValueError, match="Amostra mínima"):
        analisar(pedidos_cesta(), min_pedidos=2)


def test_status_desconhecido_invalida_comparacao_e_associacao():
    """Status que não provam venda podem alterar numerador e denominador."""
    linhas = pedidos_cesta() + [linha(document_id="incerto", status="unknown")]
    resultado = analisar(linhas)
    assert not resultado["comparacao"]["comparavel"]
    assert resultado["comparacao"]["variacao_percentual"] is None
    assert not resultado["cestas"]["disponivel"]
    assert resultado["cestas"]["pares"] == []
    assert resultado["resumo"]["linhas_status_desconhecido"] == 1


def test_status_desconhecido_anterior_nao_prova_crescimento():
    """Dados incompletos na base de comparação impedem percentual enganoso."""
    linhas = [linha(), linha(document_id="incerto", sale_date="2026-08-10", status="unknown")]
    resultado = analisar(linhas)
    assert not resultado["comparacao"]["comparavel"]


def test_cestas_sem_abrangencia_nao_geram_insight_de_associacao():
    """Uma coleção parcial de pedidos não prova as frequências da origem."""
    resultado = analisar_vendas(pedidos_cesta(), "2026-09-01", "2026-09-30")
    assert not resultado["cestas"]["disponivel"]
    assert resultado["cestas"]["pares"] == []
    assert not any("aparecem juntos" in item["titulo"] for item in resultado["insights"])


def test_snapshots_faltantes_sao_contados_sem_inventar_disponibilidade():
    """Uma foto positiva não descreve o estoque durante os demais 29 dias."""
    snapshots = [{"source_id": "fonte-ficticia", "store_id": "loja-ficticia", "product_id": "produto-a",
                  "date": "2026-09-10", "stock": 9}]
    produto = analisar([linha()], snapshots=snapshots)["produtos"][0]
    assert produto["dias_snapshot_zero"] == 0
    assert produto["dias_snapshot_desconhecido"] == 29
    assert produto["snapshot_referencia"] == "2026-09-10"


def test_snapshots_conflitantes_exigem_versao_do_lote():
    """Fotos da mesma data não podem ser misturadas sem a associação histórica."""
    foto = {"source_id": "fonte-ficticia", "store_id": "loja-ficticia", "product_id": "produto-a",
            "date": "2026-09-10", "stock": 9}
    with pytest.raises(ValueError, match="Snapshots conflitantes"):
        analisar([linha()], snapshots=[foto, {**foto, "stock": 0}])


def test_devolucao_integral_informada_aparece_no_total_devolvido():
    """A devolução confirmada é observável mesmo sem venda líquida remanescente."""
    resultado = analisar([linha(status="returned", quantity=5, returned_quantity=5)])
    assert resultado["resumo"]["unidades"] == 0
    assert resultado["resumo"]["unidades_devolvidas"] == 5


def test_devolucao_sem_quantidade_confirmada_gera_limitacao():
    """Não se inventa a quantidade devolvida apenas a partir do status."""
    resultado = analisar([linha(status="returned", quantity=5, returned_quantity=0)])
    assert resultado["resumo"]["unidades"] == 0
    assert resultado["resumo"]["unidades_devolvidas"] == 0
    assert any("devoluções é parcial" in aviso for aviso in resultado["limitacoes"])
