# Regras do Gerente Inteligente de Estoque

O GestãoClick será a fonte da verdade após a integração; o MVP só trabalha com dados simulados e CSV normalizado.

- Média diária: soma de unidades vendidas / dias elegíveis da janela.
- Dias comprovadamente sem estoque não entram na média; dias sem foto de estoque contam, mas são sinalizados como disponibilidade desconhecida.
- Cobertura: estoque / demanda média; quando média = 0, não há cobertura numérica.
- Ponto de pedido: teto(demanda diária × prazo + segurança).
- Posição: estoque físico + pendentes − reservas.
- Alvo: teto(demanda diária × máximo entre prazo e cobertura desejada + segurança).
- Pedido sugerido: máximo(0, alvo − posição).
- Risco no prazo: estoque disponível após reservas insuficiente para consumo durante o prazo. Pedidos pendentes não contam como entregues nessa verificação.
- Tendência: comparar 30 contra 90 dias; não equivale a sazonalidade.
- Curva ABC: custo aproximado dos itens consumidos nos últimos 90 dias, somente quando houver preço de custo.
- Semáforo: ruptura com estoque zero e demanda positiva; crítico até 7 dias; alto até 15; atenção até 30; excesso acima de 90; parado quando houve última venda pelo menos 60 dias atrás.
- Uma sugestão de compra nunca cria um pedido automaticamente.
- A importação é total; jamais importe CSV incremental como se fosse histórico completo.
- Produtos novos, dias de estoque desconhecido e dados incompletos precisam de revisão humana.

## Precisões validadas na auditoria da Issue #3

- Snapshot diário zero não descreve necessariamente todas as horas: se existe venda positiva no mesmo dia, preservamos essa venda e mantemos o dia elegível, com aviso de conflito. Sem venda positiva, o saldo zero exclui o dia segundo o contrato atual.
- Sem nenhum dia elegível, a demanda é **não estimável** e o status é **Dados insuficientes**. O zero interno mantido nas fórmulas não prova ausência de demanda ou de risco. A interface pede revisão; a exportação inclui a limitação.
- Quantidade zero não é uma venda positiva e não altera a data da última venda real.
- Uma primeira venda recente gera aviso de histórico curto. Não inferimos início da operação ou fechamento mensal a partir dela, nem inventamos demanda perdida.
- Curva ABC parcial é relativa aos produtos com custo conhecido. Empates usam SKU para classificação estável. Ela mede consumo a custo, não faturamento.
- Datas civis usam `America/Fortaleza`; CSV aceita datas ISO e brasileiras explicitamente, sem inferência de dia e mês.
- Importação completa remove SKUs ausentes e seus registros dependentes no SQLite. Dados mensais históricos completos exigirão uma política própria na implementação web.

