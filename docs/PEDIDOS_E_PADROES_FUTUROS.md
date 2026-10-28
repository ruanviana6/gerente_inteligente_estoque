# Evolução futura: pedidos em andamento, risco de estoque e padrões de venda

## Regra permanente: GestãoClick SEMPRE somente leitura

**Regra do proprietário, inclusive depois da integração com a API:** o Gerente Inteligente de Estoque **nunca pode modificar nada no GestãoClick**. A integração será **unidirecional**: GestãoClick → Gerente (consulta/sincronização de dados), sem envio de comandos operacionais de volta ao ERP.

- Permitido: autenticar-se na API autorizada, consultar produtos, saldo, vendas, pedidos, fornecedores e histórico que a API realmente disponibilizar; armazenar cópia analítica e snapshots no **próprio banco Supabase** do gerente.
- Proibido em qualquer fase: criar, editar ou excluir produtos, preços, clientes, vendas, pedidos, status, reservas, movimentações, saldos, compras, notas fiscais ou qualquer outro objeto do GestãoClick. Não realizar baixa nem estorno no ERP.
- Não implementar endpoints de escrita, rotinas de sincronização reversa, callbacks/webhooks que alterem o ERP, nem botões administrativos de "aplicar sugestão" no GestãoClick. Recomendações e simulações só geram informações e ações **internas ao gerente**.
- Usar credencial de API com escopo somente leitura **se o GestãoClick disponibilizar**; em qualquer caso restringir o cliente de integração aos métodos/endpoints comprovadamente de consulta. Operações de escrita HTTP (POST/PUT/PATCH/DELETE para mutação) ficam bloqueadas na camada de integração.
- Os status `em andamento`, `em separação` e `concretizada` são **observados**, nunca modificados pelo gerente. Quando a operação do ERP já descontou o estoque, o gerente não realiza segunda baixa.
- Criar testes que verifiquem que as rotinas de importação/sincronização/relatórios/simulações **não disparam mutações na API**. Registrar erros e divergências no banco interno, para conferência humana no sistema oficial.
- O sistema pode criar/alterar **seus próprios** usuários, permissões, indicadores, relatórios e histórico no Supabase. A proibição de escrita aplica-se ao **GestãoClick**, não ao banco interno do gerente.
- Não interpretar "integração futura" como autorização futura para escrita. Nenhum componente deve prever escrita no GestãoClick como requisito ou atalho.


> **Somente planejamento. Não implementar agora.**
> Executar apenas após a versão web com login, Master, permissões, Supabase
> PostgreSQL e cálculo básico de estoque estar funcionando, testada e
> validada. Integração com GestãoClick deverá ser autorizada, com leitura
> estritamente e permanentemente, e seus campos reais verificados.

## 1. Por que incluir

Além de mostrar o saldo e as vendas já realizadas, o gerente deverá destacar
o impacto **potencial** de pedidos que ainda não foram fechados e, mais
adiante, reconhecer produtos que costumam ser comprados juntos e padrões
sazonais. O objetivo é antecipar riscos e orientar compras sem misturar
estimativas com movimentações reais.

## 2. Regras dos status informados pelo usuário

| Status do pedido | Efeito no estoque operacional no GestãoClick | Interpretação no gerente |
| --- | --- | --- |
| **em andamento** | **Não baixa estoque**; o cliente ainda não fechou a compra | Demanda potencial, nunca venda confirmada |
| **em separação** | **Já baixa estoque** | Pedido confirmado/comprometido, cujo impacto pode já estar refletido no saldo oficial |
| **concretizada** | **Já baixa estoque** | Pedido concretizado, sem nova baixa ao migrar de 'em separação' |

Estas são regras do fluxo de trabalho descrito pelo usuário e devem ser
conferidas com a integração real antes da implementação. Não presumir que
as nomenclaturas são as mesmas nos endpoints da API ou que todos os casos
de cancelamento/devolução seguem esse fluxo.

### Invariantes obrigatórias

- **Nunca** diminuir o estoque físico por um pedido 'em andamento'.
- **Nunca** contar 'em andamento' como venda realizada ou receita concluída.
- Não descontar novamente pedidos 'em separação' e 'concretizada' se a
  baixa já estiver refletida no saldo retornado pelo GestãoClick.
- Não gerar dupla baixa no movimento 'em separação' -> 'concretizada'.
- Evitar contar uma venda duas vezes se o pedido aparece em diferentes
  relatórios, estados ou datas. Identificar pedido e item por chaves estáveis.
- Usar saldo oficial do ERP como origem operacional, reconciliando eventos
  e datas; o sistema de análise não fará baixas ou mudanças de status.
- Cancelamentos, devoluções, alterações nas quantidades e eventuais
  reaberturas exigem regras explícitas e tratamento idempotente.
- Separar **saldo físico informado**, **quantidade já comprometida no ERP**
  (quando a fonte disponibilizar) e **demanda potencial de negociação**.

## 3. Simulação de risco dos pedidos em andamento

O gerente deverá construir um resumo explicável de pedidos não concluídos:

- SKU, quantidade total em pedidos 'em andamento', quantidade de pedidos
  distintos, idade dos pedidos e tendência recente do funil.
- Cobertura com base no saldo oficial e demanda **confirmada/observada**.
- Cenário de consumo adicional caso parte dos pedidos em andamento seja
  concluída; **não modificar o saldo oficial** em função desses cenários.
- Sugestões de alerta: 'se 50% dos pedidos em andamento forem concluídos,
  o saldo projetado poderá cair de 40 para 25 unidades'; comparar com
  o prazo de reposição e estoque de segurança.
- Os percentuais de 25%, 50% e 100% podem ser usados como **cenários de
  simulação claramente rotulados**, não como probabilidades estimadas.
- Quando existir histórico suficiente de conversão por pedido e status,
  calcular taxa observada de conversão e, futuramente, previsão com
  intervalos/incerteza e validação histórica. Evitar confundir produtos
  presentes em pedidos com pedidos independentes: a taxa deve seguir
  unidade de análise clara (pedido, item ou unidade).
- Destacar risco comercial potencial em seção separada dos alertas
  **confirmados** de estoque e das recomendações oficiais.
- Não realizar pedido de compra automaticamente nem reservar saldo real
  só por existir pedido em andamento.

### Exemplo ilustrativo

Saldo oficial de 40 unidades, 30 unidades em pedidos 'em andamento':

| Cenário hipotético de conclusão | Consumo adicional simulado | Saldo hipotético |
| --- | ---: | ---: |
| 0% | 0 | 40 |
| 50% | 15 | 25 |
| 100% | 30 | 10 |

Esse quadro é uma **análise de sensibilidade**. Os 30 itens em andamento
não são baixados na base oficial. O cenário não prova que os pedidos
serão fechados. Quantidades devem ser avaliadas por SKU, e pedidos
com atraso ou duplicidade precisam de tratamento.

## 4. Dados mínimos e qualidade

Antes de codificar, verificar a disponibilidade na exportação/API autorizada:

- Identificador estável do pedido e identificador do item/linha.
- SKU, quantidade por linha e unidade de medida.
- Status atual, histórico de transições e datas relevantes quando disponíveis.
- Dados de cancelamento, devolução, reservas e alterações da quantidade.
- Saldo de estoque e instante da fotografia do ERP; divergências de horários.
- Histórico suficiente para inferir conversão, quando solicitado.

Se o GestãoClick não disponibilizar histórico de transição, não inventar
tempos de separação, taxas de conversão ou padrão de fechamento.
Faltas e vieses devem ser explicitamente sinalizados na interface.

## 5. Produtos vendidos juntos (etapa posterior)

Investigar **análise de cesta de compras**, baseada nas linhas dos pedidos
realmente concluídos e na data da transação. Distinguir coocorrência real
de pedidos 'em andamento' (potenciais).

- Encontrar pares/trios de SKUs frequentemente comprados no mesmo pedido.
- Medir frequência, suporte, confiança e lift quando o volume permitir.
- Aplicar tamanho mínimo de amostra e filtros de produtos raros; evitar
  transformar coocorrência em causalidade.
- Exemplos de saída: 'em 28% dos pedidos com A, também aparece B',
  sempre com período analisado e tamanho da amostra.
- Usos futuros: apoiar compras conjuntas, organização, vendas complementares
  e alertas de ruptura combinada. Não criar vendas/pedidos automaticamente.
- Respeitar as permissões de análise do usuário; dados pessoais de clientes
  não são necessários para descobrir padrões por pedido.

## 6. Sazonalidade (etapa posterior)

Investigar variações recorrentes por mês, dia da semana, períodos do ano,
datas especiais e categorias, **somente quando houver histórico adequado**.

- Comparar padrões de períodos equivalentes e distinguir tendência,
  promoções pontuais, mudança de preço e sazonalidade verdadeira.
- Registrar rupturas e dias indisponíveis; venda observada pode subestimar
  demanda em períodos de falta de estoque.
- Em séries curtas, apresentar tendências e avisar que **não é possível
  confirmar sazonalidade**; preferir métodos simples e explicáveis.
- Só adotar previsão estatística avançada ou ML mediante ganho testado
  contra baseline e validação temporal (sem vazamento de futuro).

## 7. Acesso e experiência

- No painel principal, adicionar futuramente um flash de **demanda potencial**
  em categoria distinta dos riscos confirmados, com explicação do cenário.
- Criar visão de 'Pedidos em andamento' com filtros por produto, fornecedor,
  quantidade potencial, risco e idade do pedido, apenas quando os dados
  estiverem disponíveis de forma autorizada.
- O Master define permissões. Propor, sem ativar ainda, as chaves
  `orders.view`, `orders.pipeline.view`, `orders.risk.view`,
  `analytics.basket.view` e `analytics.seasonality.view`.
- Aplicar autenticação, autorização no banco e em cada ação. Evitar expor
  nome, contato ou dados pessoais do cliente sem necessidade.

## 8. Ordem e critérios de aceite

1. Concluir **primeiro** o MVP web com login, Master, permissões e dados
   persistentes, com testes e segurança validados.
2. Somente após isso, confirmar formato de pedidos/status na fonte
   autorizada (API ou exportação), com leitura e dados fictícios/anônimos
   para testes.
3. Implementar a visão dos três status com reconciliação de estoque,
   sem interferir no fluxo operacional do ERP.
4. Adicionar cenários de conversão de pedidos em andamento e alertas de
   risco potencial, rotulados como simulação e testados.
5. Após histórico suficiente, estudar taxas de conversão observadas e
   análise de cesta por pedidos fechados.
6. Após histórico sazonal suficiente, estudar sazonalidade e previsões
   validadas, sem prometer modelos preditivos prematuros.

**Teste obrigatório:** em andamento -> em separação -> concretizada não
gera duas baixas. Além disso, simular conversão de pedidos em andamento
não altera a quantidade oficial no banco/ERP.

**Estado:** somente escopo futuro. Nenhuma funcionalidade desta página
está implementada nem autorizada para execução imediata.

