"""Protege privacidade, abrangência e integridade das análises fictícias."""

from datetime import date

import pytest

from analytics.commercial import analisar_comercial, normalizar_linhas_comerciais, pseudonimizar_cliente


def linha(dia, cliente, pedido, sku="001", cidade="2304400", quantidade=2):
    return {"source_id": "origem-demo", "store_id": "loja-demo", "document_id": pedido, "line_id": sku,
            "customer_ref": pseudonimizar_cliente("origem-demo", "loja-demo", cliente, b"x" * 32),
            "city_code": cidade, "city_name": "Cidade fictícia" if cidade else None,
            "state": "CE" if cidade else None, "sale_date": dia, "sku": sku,
            "quantity": quantidade, "returned_quantity": 0, "status": "completed"}


def analisar(linhas, inicio=date(2026, 2, 1), fim=date(2026, 2, 28), **kwargs):
    return analisar_comercial(linhas, inicio, fim, source="origem-demo", store="loja-demo",
                              coverage_start=kwargs.get("inicio_cobertura", date(2026, 1, 4)),
                              coverage_end=date(2026, 2, 28))


def test_pseudonimo_isola_origem_e_loja_sem_nome():
    segredo = b"x" * 32
    assert pseudonimizar_cliente("a", "1", "mesmo", segredo) != pseudonimizar_cliente("a", "2", "mesmo", segredo)
    with pytest.raises(ValueError):
        pseudonimizar_cliente("a\0b", "c", "d", segredo)


def test_campos_pessoais_nao_entram_no_contrato():
    entrada = linha("2026-02-01", "pessoa", "01") | {"email": "ficticio@example.invalid"}
    with pytest.raises(ValueError, match="contrato"):
        normalizar_linhas_comerciais([entrada])


def test_duplicata_identica_e_idempotente_e_conflito_e_recusado():
    entrada = linha("2026-02-01", "pessoa", "01")
    linhas, repetidas = normalizar_linhas_comerciais([entrada, entrada.copy()])
    assert len(linhas) == 1 and repetidas == 1
    with pytest.raises(ValueError, match="conflitantes"):
        normalizar_linhas_comerciais([entrada, entrada | {"quantity": 9}])


def test_periodo_incompleto_nao_inventa_queda_ou_associacao():
    resultado = analisar([linha("2026-02-01", "pessoa", "01")], inicio_cobertura=date(2026, 2, 1))
    assert not resultado["clientes"] and not resultado["cidades"] and resultado["cestas"] is None
    assert resultado["avisos"]


def test_cliente_com_reducao_tem_evidencia_sem_inferir_abandono():
    linhas = [linha(dia, "pessoa", str(i), quantidade=q) for i, (dia, q) in enumerate([
        ("2026-01-05", 5), ("2026-01-12", 5), ("2026-01-19", 5),
        ("2026-01-26", 5), ("2026-02-02", 2)])]
    resultado = analisar(linhas)
    alerta = resultado["clientes"][0]
    assert "volume_reduzido" in alerta["alertas"]
    assert alerta["unidades_atuais"] == 2 and alerta["unidades_anteriores"] == 20
    assert "abandono" not in str(alerta).lower()


def test_cidade_so_aparece_com_minimo_de_clientes_nos_dois_periodos():
    linhas = [linha(dia, str(cliente), f"{dia}-{cliente}") for cliente in range(3)
              for dia in ("2026-01-15", "2026-02-15")]
    assert len(analisar(linhas)["cidades"]) == 1
    assert not analisar([r for r in linhas if r["document_id"] != "2026-02-15-2"])["cidades"]


def test_outro_estabelecimento_nao_contamina_resultados():
    linhas = [linha(dia, str(cliente), f"{dia}-{cliente}") for cliente in range(3)
              for dia in ("2026-01-15", "2026-02-15")]
    fora = linha("2026-02-01", "outro", "outro") | {"store_id": "loja-diferente", "quantity": 9000}
    assert analisar(linhas + [fora])["cidades"][0]["unidades_atuais"] == 6


def test_canceladas_e_devolvidas_nao_contam_como_venda_concluida():
    linhas = [linha(dia, str(cliente), f"{dia}-{cliente}") for cliente in range(3)
              for dia in ("2026-01-15", "2026-02-15")]
    linhas += [linha("2026-02-16", "pessoa", "devolvida", quantidade=100) |
               {"status": "returned", "returned_quantity": 20}]
    assert analisar(linhas)["cidades"][0]["unidades_atuais"] == 6
