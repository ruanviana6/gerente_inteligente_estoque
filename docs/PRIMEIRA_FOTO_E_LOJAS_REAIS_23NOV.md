# Duas lojas reais e primeiro cadastro de fotografia — 23/11/2026

## Corrigido

Na aba Estoque, a ausência de uma fotografia não impede mais a consulta e o salvamento da **primeira** fotografia pelo Master. A conexão com o GestãoClick é autorizada explicitamente; o catálogo completo é validado e salvo somente no Supabase. A API do ERP continua GET-only.

Para organizações com mais de uma fonte real ativa, o Master escolhe a **loja por ID**, antes de ver dados reais. Uma seleção antiga que deixe de estar na lista autorizada é descartada. A tela inicial, o Estoque e a triagem de Reposição utilizam a mesma seleção e nunca somam lojas ou assumem que um produto possui o mesmo saldo em ambas.

Se a loja tiver fotografia, mas ainda não houver demanda observada, a Visão Geral mostra a **quantidade de produtos** e a **soma dos saldos da fotografia** com data e aviso de que o saldo é histórico. Ausência de vendas consolidadas não é confundida com ausência de movimento. Sem fotografia nem observação, orienta fazer a primeira leitura na aba Estoque.

## Como usar

1. Entre como Master e abra **Estoque e histórico**.
2. Se houver mais de uma loja real, escolha o ID da loja já conferido com o GestãoClick.
3. Clique em **Conferir conexão e loja do estoque** (com a autorização marcada).
4. Dentro da conferência integral do catálogo, autorize a leitura, confira os nomes, categorias e saldos retornados e confirme explicitamente o salvamento da fotografia.
5. A nova fotografia aparecerá no Estoque e, onde aplicável, na Visão Geral. A Reposição continua uma conferência histórica até que as linhas de vendas e embalagens sejam homologadas.

## Restrições

- Não modifica vendas, pedidos, produtos, saldos ou notas no GestãoClick.
- Não inventa nome, categoria, demanda ou saldo atual.
- Não libera compra automática ou cadastro fiscal real.
- Somente Master com `integrations.manage` visualiza essas fontes; cada RPC verifica a sessão e a origem da loja.
- A persistência de login após F5 continua pendente separadamente na Issue #63. Esta entrega não muda a autenticação.
