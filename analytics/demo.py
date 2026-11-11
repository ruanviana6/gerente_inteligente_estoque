"""Dados fictícios explícitos para validar a área de vendas sem consultar ERP."""

from datetime import date, timedelta


def gerar_vendas_ficticias(source_id: str, store_id: str, inicio: date, fim: date) -> list[dict]:
    """Gera pedidos DEMO determinísticos, sem cadastro de clientes nem dinheiro.

    A função não persiste nem é chamada automaticamente. Cabe à ação Master
    registrar uma fonte identificada como fictícia e conferir a abrangência.
    """
    if not isinstance(inicio, date) or not isinstance(fim, date) or not 1 <= (fim - inicio).days + 1 <= 366:
        raise ValueError("A demonstração aceita um período ordenado de até 366 dias.")
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
                    "status": "completed", "quantity": 1 + (deslocamento // 30 if produto == "DEMO-A" else 0),
                    "returned_quantity": 0, "source_updated_at": f"{dia.isoformat()}T18:00:00+00:00",
                })
    return linhas
