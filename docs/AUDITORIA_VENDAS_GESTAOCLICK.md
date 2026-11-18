# Auditoria integral de vendas x catálogo — GestãoClick

## Escopo (primeira entrega operacionalmente segura)

- Conta/flatter: selecionar loja pelo catálogo assinado no servidor, sem assumir atacado/varejo.
- Loja já conferida na conversa: `416023`. Status de **Em separação**: `6548068`; **Concretizada**: `6548070`. Confirmação explícita no formulário é obrigatória e os códigos **não são aplicados a outras lojas**.
- Sem operação de PDV: somente GET `/vendas?tipo=produto`.
- Janela máxima de sete dias, no máximo dez páginas e 1.000 documentos, com 20.000 itens. Para períodos maiores, dividir em consultas menores.
- A paginação integral valida contagens, total e limite estáveis, IDs de documento únicos, autenticação Master e loja fixa. A `proxima_url` nunca é seguida; cada GET é montado pelo aplicativo.
- O retorno elimina clientes, preços, financeiro e dados adicionais. Não mantém cliente nem token na sessão.

## Regras de demanda comercial observada

- `Em andamento`: pedido aberto, não entra na demanda fechada.
- `Em separação`: pedido comercialmente fechado, ainda não necessariamente expedido; entra na demanda **observada**.
- `Concretizada`: pedido expedido; entra na demanda **observada**.
- Um documento pertence a **um único estado no instantâneo consultado**. A mudança de Em separação para Concretizada não deve gerar nova venda no cálculo.
- A ausência de pedido anteriormente conhecido **não comprova exclusão/cancelamento**. Não apagar, não corrigir demanda nem deduzir devolução sem evidência.
- Sem comprovação de `source_line_id` e `source_updated_at`, não há versionamento/incremental seguro da linha de venda. Não gravar em `sales_lines_current`, nem marcar cobertura histórica verdadeira.
- `UN`: unidade explicitamente informada, pode ser somada como UN **observada**. `CX`, `KG`, unidades ausentes e outras exigem tabela de conversão confirmada.
- O estoque do catálogo já é retorno do ERP e **nunca é reduzido novamente** pela quantidade das vendas. Movimentação física depende das configurações de estoque de cada situação no ERP.
- A correspondência de `produto_id` com o catálogo é necessária. Linha sem produto correspondente fica pendente, sem inferir código/SKU ou ID de variação.
- O resultado é prévia de auditoria, não indicador operacional e **não alimenta reposição**, relatórios recorrentes ou sincronização automática.

## Para avançar para histórico e reposição

1. Confirmar visibilidade real de todas as lojas da chave e se a API retorna todos os documentos elegíveis.
2. Conferir **identificador estável de linha** em listagem/detalhes de vendas da conta e **instante de atualização** ou outra política de versionamento comprovável.
3. Definir política de pedidos excluídos: webhook de exclusão (se suportado) ou conciliação com origem auditável, nunca ausência inferida.
4. Conferir unidades operacionais e fatores caixa/unidade por produto.
5. Confirmar no GestãoClick as configurações `Lançar estoque` das situações: fechamento comercial não prova baixa física.
6. Somente depois: versionamento idempotente de vendas, carga controlada, reconciliada, publicação dos indicadores e agendamento.

## Segurança

A camada de auditoria não faz POST/PATCH/DELETE no GestãoClick, não grava vendas no Supabase e não modifica `inventory_current`. A nova RPC `catalog_stock_latest` devolve só a fotografia de produtos da fonte/loja para Master autenticado. As tabelas permanecem sem acesso direto no Data API.
