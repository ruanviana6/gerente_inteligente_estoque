"""Dados fictícios explícitos para validar a área de vendas sem consultar ERP."""

from datetime import date, timedelta

from sincronizacao.periodos import validar_periodo


def gerar_vendas_ficticias(source_id: str, store_id: str, inicio: date | str, fim: date | str) -> list[dict]:
    """Gera pedidos DEMO determinísticos, sem cadastro de clientes nem dinheiro.

    A função não persiste nem é chamada automaticamente. Cabe à ação Master
    registrar uma fonte identificada como fictícia e conferir a abrangência.
    Cada linha depende da data civil, nunca dos limites da consulta. O produto
    A segue um ciclo anual fictício de 1 a 12 unidades, com reinício em janeiro,
    para demonstrar variação mensal sem alterar versões em janelas sobrepostas.
    """
    inicio, fim = validar_periodo(inicio, fim)
    if not source_id or not store_id:
        raise ValueError("Informe a fonte e a loja fictícias.")
    produtos = {
        "DEMO-A": ("Produto fictício A", "Categoria fictícia 1", "Fornecedor fictício 1"),
        "DEMO-B": ("Produto fictício B", "Categoria fictícia 1", "Fornecedor fictício 1"),
        "DEMO-C": ("Produto fictício C", "Categoria fictícia 2", "Fornecedor fictício 2"),
    }
    linhas = []
    for deslocamento in range((fim - inicio).days + 1):
        dia = inicio + timedelta(days=deslocamento)
        for numero in range(1, 4):
            cesta = ("DEMO-A", "DEMO-B") if numero < 3 else ("DEMO-C",)
            for indice, produto in enumerate(cesta, start=1):
                nome, categoria, fornecedor = produtos[produto]
                linhas.append({
                    "source_id": source_id, "store_id": store_id,
                    "document_id": f"DEMO-{dia.isoformat()}-{numero}", "line_id": str(indice),
                    "product_id": produto, "sku": produto, "product_name": nome,
                    "category": categoria, "supplier": fornecedor, "sale_date": dia.isoformat(),
                    "status": "completed", "quantity": dia.month if produto == "DEMO-A" else 1,
                    "returned_quantity": 0,
                    # Meia-noite em Fortaleza já passou para todo dia aceito
                    # pelo validador, inclusive quando a consulta inclui hoje.
                    "source_updated_at": f"{dia.isoformat()}T03:00:00+00:00",
                })
    return linhas
