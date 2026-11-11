# Inteligência de Vendas

A área lê a cópia analítica já autorizada no Supabase. Navegar ou trocar filtros
nunca consulta o GestãoClick. O módulo `analytics/sales.py` é puro, determinístico
e independente de interface, credenciais, persistência e motor de estoque.

## Contrato interno

Este contrato é do Gerente, não uma descrição presumida do retorno do ERP.
O conector deve mapear apenas os campos comprovados no OpenAPI da conta.

| Campo | Regra |
| --- | --- |
| source_id, store_id | Identificam fonte/conta e loja autorizadas |
| document_id, line_id | Identificadores estáveis do documento e da linha |
| product_id, sku, product_name | Identidade da origem, código textual e nome |
| category, supplier | Texto opcional, sem inferência a partir do nome |
| sale_date | Data civil ISO `AAAA-MM-DD` |
| status | `potential`, `separating`, `completed`, `cancelled`, `returned` ou `unknown` |
| quantity, returned_quantity | Quantidades finitas, não negativas; devolução até a quantidade original |
| source_updated_at | Horário com fuso, opcional; necessário para resolver versões conflitantes |

A chave de linha é `(source_id, store_id, document_id, line_id)`. Uma duplicata
idêntica não altera o total. Entre versões diferentes prevalece o horário mais
recente da origem. Sem ordem comprovável, a análise recusa o conflito; a posição
da linha no arquivo não escolhe a versão vigente. A persistência deve fornecer a
visão vigente consolidada pelo mesmo critério e preservar versões históricas.

Campos fora da allowlist são recusados. Nomes, CPF/CNPJ, contatos, endereços de
clientes e dados financeiros não integram o contrato. A primeira entrega não
calcula faturamento, receita ou ticket médio. A quantidade não permite inferir
valor de venda, e custo de estoque não é faturamento.

## Vendas, devoluções e status

Somente `completed` com quantidade líquida positiva contribui para vendas e
cestas. A quantidade líquida é `quantity - returned_quantity`. Cancelamentos,
devoluções integrais e status desconhecidos ficam fora das vendas concluídas.
Um status desconhecido gera aviso. `potential` e `separating` não são compras
concluídas. A transição de separação para conclusão não soma duas vendas.

O saldo oficial nunca é alterado pelo analisador. A correspondência entre esses
estados canônicos e os códigos reais precisa ser conferida no ERP autorizado.
Devoluções parciais são atribuídas à data original da venda nesta visão, sem
inventar uma data de devolução quando a fonte não a disponibiliza.

## Escopo e abrangência

Uma chamada de `analisar_vendas` aceita somente uma fonte e uma loja. Se houver
mais de um escopo e não existir uma seleção suficiente, a análise recusa a
consolidação implícita. IDs ou SKUs iguais entre contas diferentes não são uma
identidade global de produto.

O período é inclusivo. A comparação anterior tem exatamente o mesmo número de
dias e termina na véspera do período selecionado. `coverage_start` e
`coverage_end` são a abrangência declarada e reconciliada pela sincronização.
O recebimento de HTTP 200, uma lista vazia ou a primeira venda não provam essa
abrangência. Sem cobertura declarada dos dois períodos, não há percentual de
crescimento. Sem cobertura do período atual, os dias ausentes não viram zeros.

## Indicadores e crescimento

A visão geral mostra unidades líquidas, pedidos distintos com venda líquida,
produtos vendidos, linhas canceladas e devoluções parciais informadas. As séries
agrupam por data, semana iniciada na segunda-feira e mês civil. Categoria,
fornecedor e loja mostram unidades e pedidos, sem valores monetários.

Para cada produto, o sistema apresenta volume atual e anterior, diferença
absoluta, percentual quando definido e amostra de pedidos. Se a base anterior
for zero, o percentual é indisponível; não aparece um crescimento infinito.

O rótulo **crescimento ou queda sustentados** exige:

- Abrangência declarada dos dois períodos equivalentes.
- Pelo menos 14 dias em cada período, cinco unidades e cinco pedidos por período.
- A mesma direção de mudança nas duas metades correspondentes dos períodos.
- Nenhum snapshot associado com saldo zero no período atual.

Quando a disponibilidade não é conhecida, isso permanece explícito. Uma venda
observada em período de ruptura pode subestimar demanda. O sistema não inventa
vendas perdidas. Se houver cobertura e prazo do motor de estoque associados à
mesma fonte, loja e produto, um insight pode relacionar aumento observado e
cobertura abaixo do prazo. Isso não muda a recomendação validada do motor.

## Calendário e sazonalidade

Mês do ano, semana ISO, dia da semana e trimestre têm distribuição **observada**.
Um trimestre de dados não comprova sazonalidade. A sinalização conservadora de
recorrência mensal exige ao menos dois anos civis completos, 20 pedidos em cada ano e uma
taxa mensal por dia pelo menos 15% acima da média das taxas mensais em cada ano.
O índice por dia evita comparar fevereiro curto diretamente com meses longos.

Mesmo com esse critério, o resultado é uma recorrência observada, nunca previsão
confiável de demanda. Feriados, promoções, rupturas e mudanças de catálogo não
são controlados automaticamente. Datas comerciais não são inferidas sem um
calendário autorizado. Não há Machine Learning nem previsão sazonal avançada.

## Cestas de compras

A unidade de análise é o pedido concluído, identificado pela fonte, loja e
documento. Repetir o produto em várias linhas ou comprar mais unidades não
multiplica a presença dele no pedido. Produtos vendidos no mesmo dia em pedidos
distintos não formam uma cesta.

Para um par A e B:

- **Pedidos juntos:** número de pedidos distintos contendo A e B.
- **Suporte/coocorrência:** pedidos com A e B / todos os pedidos concluídos.
- **Confiança A → B:** pedidos com A e B / pedidos com A.
- **Confiança B → A:** pedidos com A e B / pedidos com B.
- **Lift:** suporte / (frequência de A × frequência de B).

Amostra mínima: 20 pedidos concluídos e três coocorrências, com abrangência
completa declarada para o período e sem status desconhecidos. Sem isso, a
associação fica indisponível, pois a ausência de pedidos altera o denominador.
Filtros de produto
ou categoria selecionam os pares exibidos, preservando o denominador de todos
os pedidos da fonte/loja no período. Coocorrência não prova causalidade, e lift
alto em uma amostra pequena continua sujeito a incerteza.

Para proteger a execução gratuita, uma cesta de mais de 100 produtos ou mais de
dois milhões de combinações processadas recusa a associação inteira com aviso.
As métricas de vendas continuam disponíveis. A tabela apresenta até 500 pares
e informa quantos ficaram fora da exibição. O usuário pode reduzir o período.

## Limites e demonstração

O analisador puro aceita até 200 mil linhas e período de até 3660 dias. A fachada
web limita cada análise a 1826 dias e o RPC a 20 mil linhas, incluindo o período
anterior consultado. A janela ampliada permite avaliar dois anos civis quando
existir histórico suficiente; o limite de linhas continua valendo. O gerador
`analytics/demo.py` cria transações identificadas como DEMO, sem clientes, por
solicitação explícita. Não é fallback para erro de banco nem preenche uma fonte
real. Executar testes com esses dados não valida contratos nem cobertura do ERP.

Os testes `tests/test_sales_analytics.py` cobrem versões, cancelamento,
devolução, isolamento por fonte/loja, disponibilidade, crescimento, calendário,
fórmulas de cesta, limites e dados insuficientes. Resultados de execução devem
ser consultados nas evidências da entrega, não inferidos desta documentação.
