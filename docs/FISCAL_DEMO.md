# Automação fiscal DEMO vigente

A regra atual é cadastro por loja, sem emissão automática. Consulte
[CADASTRO_NFE_POR_LOJA.md](CADASTRO_NFE_POR_LOJA.md) e
[CHECKLIST_ATIVACAO_API_GESTAOCLICK.md](CHECKLIST_ATIVACAO_API_GESTAOCLICK.md).

A aplicação única usa Streamlit, Supabase existente e uma Edge Function DEMO.
FLT Comércio é a origem; Flagship - Flatter recebe somente o cadastro da nota.
Os dois campos manuais, confirmação, Auth, permissões, RLS, concorrência e
reconciliação permanecem obrigatórios. O login persistente é preservado.

A primeira DEMO foi registrada no PR #22 e nas migrações
20261111170000/20261111170500. A migração aditiva
20261112192003_cadastro_fiscal_por_loja atualiza a política sem alterar registros
legados nem apagar história. O contrato v2 tem escopo de conexão/lojas e estado
registered. O executor não emite, cria venda/pedido ou guarda XML/PDF.

## Interface e desempenho

Seis áreas: Visão Geral, Estoque, Reposição Inteligente, Inteligência Comercial,
Automação / Notas Fiscais e Relatórios. Administração agrupa operações conforme
permissões. Ações ficam em Reposição; Clientes/Cidades/Produtos Juntos/Vendas,
em Inteligência Comercial. Não há consulta ERP ou treino na navegação.

A Visão Geral lê um resumo de até 32 KiB. Até seis KPIs, cinco prioridades e
dois gráficos. O Master atualiza indicadores explicitamente após importação ou
mudança de regras; revisões sinalizam desatualização. A falta de embalagem não
vira uma conversão inventada. Custos e dados pessoais continuam protegidos.

No mesmo AppTest, cinco repetições e transporte fictício de 20 ms por chamada:

| Produtos | Antes p50/p95 | Depois p50/p95 | Preparação explícita |
| --- | --- | --- | --- |
| 100 | 607,54 / 1.984,71 ms | 228,66 / 277,97 ms | 233,24 ms |
| 1.200 | 1.050,77 / 1.054,72 ms | 288,31 / 302,53 ms | 880,25 ms |

Base: 0add0fbf073781de4b7fcec080a8b331ba865ef2. A mediana caiu 72,6% no cenário
maior. O JSON fictício bruto tinha 1.625.087 bytes e o resumo 1.863 bytes,
sem o envelope. O cálculo não desapareceu: passou para um processo controlado.
Isso não mede latência Cloud, transferência real ou pintura do navegador.
A revisão de cadastro por loja preserva o mesmo painel e motor.

O registro inicial e as condições completas foram preservados em
[histórico V1](historico/FISCAL_DEMO_V1.md). Não usar as instruções fiscais
antigas para operar a aplicação atual.
