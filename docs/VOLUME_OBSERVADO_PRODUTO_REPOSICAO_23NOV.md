# Volume histórico por produto na Reposição — 23/11/2026

## Objetivo

Após o proprietário confirmar que os nomes/categorias do catálogo real estão corretos na versão `20261123.5`, permitir conferir quanto cada produto **apareceu em vendas fechadas na última observação salva**, sem confundir esse volume com previsão confiável ou venda consolidada.

## Implementação

Na aba **Reposição Inteligente → Planejar reposição com produto real e dados conferidos**, o Master escolhe sua loja real e um produto com nome. Um botão separado consulta o Supabase (`demand_observation_review`) e mostra, se disponível:
- Quantidade UN observada em vendas fechadas para aquele **ID de produto e loja**;
- Datas inicial/final da observação;
- Aviso explícito de que o valor não é média validada, não cobre necessariamente exclusões e devoluções e não será usado automaticamente no cálculo.

Se não existir observação ou o produto não aparecer naquela janela, o sistema explica que **ausência de registro não significa zero vendas**. Uma observação de outra loja é rejeitada.

Foi também retirado o segundo seletor de loja da triagem real: a loja escolhida na página Reposição agora é reutilizada integralmente, sem misturar atacado/varejo ou sobrescrever a escolha do usuário.

## Limites preservados

- Nenhum POST no GestãoClick, nenhuma escrita de NF-e nem alteração de saldos.
- Não faz recomendação de reposição a partir de agregados comerciais possivelmente incompletos.
- Saldo atual, reservas, embalagem, prazos e média diária seguem exigindo conferência manual. A automação dependerá de eventos de venda reconciliados e cobertura validada.
- O Master deve autorizar a consulta dos dados reais; a RPC revalida escopo e sessão.
- Não usa webhooks (adiados conforme solicitação) nem altera login/F5.
