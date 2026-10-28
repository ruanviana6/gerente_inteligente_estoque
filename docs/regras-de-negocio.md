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
