# Histórico real de observações comerciais — GestãoClick

## Objetivo

Usar a integração autenticada GET da empresa para guardar observações **agregadas** de demanda de produtos em `UN` no **Supabase já existente**, sem tratar os registros como histórico canônico de linhas de venda ou como baixa física de estoque.

A coleta é feita pelo Master em **Administração → Conferir lojas e situações da conta → Histórico de demanda observada**. Não há criação de outro banco, conta, usuário ou serviço pago.

## Fluxo seguro de operação

1. Consultar e confirmar as lojas e situações oficiais da mesma conta.
2. Escolher a loja e garantir que exista uma fotografia salva do catálogo nela.
3. Selecionar até sete dias e confirmar os IDs de **Em separação** e **Concretizada**.
4. Autorizar GET e preparar a observação; a API consulta todas as páginas até o limite de 1.000 documentos, sem armazenar dados pessoais.
5. Conferir no ERP as quantidades e o período mostrados; confirmar separadamente o salvamento **interno**.
6. A nova RPC só aceita dados agregados de produtos conhecidos da última fotografia do catálogo; valida integridade, duplicidade, Master, organização e loja. Reprocessamento idêntico reutiliza o mesmo registro.
7. Consultar as 12 observações recentes da própria loja.

## Limites inegociáveis

- **Não** atualiza `sales_lines_current`, `sales_line_versions`, `inventory_current`, `sync_runs`, Reposição Inteligente, cadastro de NF-e ou GestãoClick.
- **Não** presume loja efetiva só porque o filtro `loja_id` foi aceito, nem comprova exclusões, devoluções, `source_updated_at` ou ID de cada linha.
- **Não** converte `CX`, `KG`, `KIT` ou unidades ausentes em `UN`; estas linhas ficam contadas como exclusões de agregação.
- Snapshot adicional é append-only; uma segunda conferência alterada gera nova observação, não remove a anterior. Não somar snapshots de períodos sobrepostos como se fossem vendas distintas.
- Sem agenda, webhook ou importação operacional automática enquanto a cobertura e os eventos tardios não forem comprovados.
- A fotografia é uma informação histórica para diagnóstico e decisão humana, **não** uma projeção validada de demanda.

## Aceite técnico

- Validar Python 3.11/3.12, Ruff, banco PostgreSQL/RLS, Supabase descartável, executor fiscal, navegador e desempenho com dados fictícios.
- Aplicar a migração de modo aditivo no projeto Supabase atual após CI e revisão; validar funções, tabelas, RLS e nenhuma alteração nos dados existentes.
- O Master executará uma observação autenticada no Streamlit. Até isso ocorrer, **não afirmar** que o histórico real foi preenchido.

## Pendências para reposição automática

Para consolidar vendas de forma operacional, faltam identidade estável ou substituição atômica comprovada por documento, detecção auditável de alteração e exclusão, devoluções, cobertura efetiva por loja, unidades de embalagem e períodos reconciliados diretamente no ERP. Ainda não satisfeitos.

## Pendências para cadastro real de NF-e

O módulo fiscal permanece em DEMO. A API pública dispõe da família `notas_fiscais_produtos`, mas cadastrar uma nota real requer demonstrar todos os campos tributários, a loja efetiva da credencial, prevenção de duplicidade e o comportamento do campo `envio_automatico`. **Nenhum POST será executado durante esta integração de demanda.**

Fontes:

- https://central.gestaoclick.com.br/hc/pt-br/articles/1874-emitir-e-cancelar-nota-fiscal-pela-api-linha-fis
- https://central.gestaoclick.com.br/hc/pt-br/articles/5140-api-consultas-de-agenda-atendimentos-mdf-e-financeiro-estoque-fiscal-e-da-conta
