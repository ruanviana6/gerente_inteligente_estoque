# Consulta do saldo atual no GestãoClick dentro da Reposição — 23/11/2026

## Necessidade

O usuário confirmou que a consulta de vendas observadas no aplicativo já exibe dados reais da loja 416023, mas o campo **Saldo atual conferido no GestãoClick (UN)** permanecia manual. A fotografia do catálogo no Supabase é histórica; não deve ser usada automaticamente como saldo atualizado.

## Como funciona

Na **Reposição Inteligente**, depois de selecionar a loja e um produto identificado por nome e código, o Master poderá abrir **Consultar saldo do produto diretamente no GestãoClick**. A operação exige:
1. Marcar a autorização explícita da consulta GET integral da loja.
2. Clicar em **Consultar saldo no GestãoClick**. O aplicativo revalida conta/organização Master, faz GET das lojas/situações e de todas as páginas da mesma loja. Não persiste nem altera os dados no GestãoClick ou Supabase.
3. Validar que o produto existe exatamente uma vez, com o mesmo nome, ID e código interno da fotografia selecionada.
4. Conferir o saldo lido, loja e data/hora. Se inteiro, clicar **Copiar saldo consultado para a simulação**. Só essa confirmação preenche o campo do saldo. O valor copiado expira em até cinco minutos e não pode ser reaproveitado de outra loja/produto/sessão.
5. Preencher/confirmar **reservas, demanda média diária, unidades por caixa, prazo e estoque de segurança** antes de simular. Os 75 UN observados durante sete dias em um exemplo real não são promovidos automaticamente a média diária ou previsão de compras.

A consulta completa pode demorar alguns segundos, pois o limite vigente é de até 1.000 produtos / 10 páginas e requer reconciliação. Não faz polling, não é webhook e só acontece após o clique explícito. Um retorno parcial ou inconsistente cancela a consulta.

## Bloqueios de segurança

- Sem alteração de saldos, cadastro, pedido, venda ou notas no ERP.
- Produto com variações precisa de conferência específica: não usar o saldo agregado sem validação da unidade.
- Se mudou nome/código, se a loja não confere, se o saldo é ausente, negativo ou fora de limite, não preencher o campo.
- Quantidade fracionária é mostrada para diagnóstico, mas não pode ser copiada para um motor em unidades inteiras.
- Nunca apresentar o valor consultado como estoque automaticamente atualizado. É o saldo no instante da consulta, com possibilidade de alteração posterior.
- Sem novos Secrets, tabelas, plugins pagos ou mudanças de autorização.
- A homologação de vendas/devoluções e o cadastro fiscal real continuam pendentes; webhooks são a última prioridade.
