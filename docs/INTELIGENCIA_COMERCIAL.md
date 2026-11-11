# Inteligência Comercial Flatter

## Implementado

Uma única área reúne Clientes, Cidades e Produtos Juntos. A análise de Vendas do
PR #8 continua acessível dentro dessa área. Cada visão comercial consulta dez
insights prontos por página, com comparação de períodos iguais, fonte, loja,
data de cálculo, método e limitações. A busca de cliente usa a referência pseudonimizada, sob permissão específica. Não há treino nem chamada ao ERP na navegação.

`analytics/commercial.py` calcula cadência mediana, recência, crescimento/redução
de unidades e alteração do mix. Crescimento exige pelo menos duas compras no
período e frequência que não caiu. Clientes precisam de quatro datas de compra
e ao menos dois pedidos anteriores. A comparação exige cobertura dos dois
períodos. Cancelamentos, pendências e linhas devolvidas não viram venda concluída.
Devoluções parciais de venda concluída reduzem as unidades líquidas.

Cidades exigem ao menos três clientes distintos em cada período. Cidade
desconhecida não recebe município inferido. O processo exige definir se a cidade
é de entrega, cadastro ou faturamento. Código e UF precisam ser consistentes;
a existência do município ainda depende do contrato/cadastro autorizado.
Os principais SKUs da cidade também exigem três compradores distintos por produto em cada período em que são exibidos. Produtos Juntos reutiliza os pedidos reais e as métricas do PR #8: ao menos vinte
pedidos no escopo e três coocorrências, com suporte, confiança e lift explicados.

## Privacidade e permissões

`commercial.view` não dá acesso individual a clientes: também é necessário
`commercial.customer_detail`. As duas permissões são negadas por padrão fora do
Master. `commercial.manage`, gestão de modelos e ocupação são exclusivas do Master.
O banco valida sessão, organização, fonte e loja em cada operação. A fachada
revalida também mudanças de sessão e concessões depois da resposta.

O contrato analítico só aceita pseudônimo HMAC de cliente e município/UF. Não
aceita nome pessoal, CPF, e-mail, telefone, endereço, custo ou payload livre. O
segredo HMAC deve ser estável, exclusivo do backend, protegido e ter 32 bytes ou
mais. Rotação exige planejamento para não romper a identidade histórica. Não há
exportação nominal. Pseudonimização reduz exposição, mas continua exigindo
finalidade e autorização empresarial, avaliação LGPD e acesso restrito.

## Processo controlado

`analytics/commercial_pipeline.py` valida o arquivo minimizado e prepara até 500
indicadores. `scripts/processar_comercial.py` faz simulação por padrão. Para
publicar, exige opção explícita, confirmação de contrato e dados conferidos, e
login Master por variáveis protegidas `COMMERCIAL_MASTER_EMAIL` e
`COMMERCIAL_MASTER_PASSWORD`. Usa chave comum e JWT, nunca `service_role`.
O logout do processo é local, preservando as outras sessões do proprietário.

Os parâmetros `--entrada`, `--fonte`, `--loja`, `--inicio`, `--fim`,
`--cobertura-inicio`, `--cobertura-fim` e `--cidade` são obrigatórios. A entrada é
uma lista JSON no contrato `CAMPOS` de `analytics/commercial.py`. Mantenha arquivos
reais fora do repositório. A rotina não consulta o GestãoClick. Sem
`--publicar --contrato-validado --dados-confirmados`, nenhuma escrita é realizada.

O banco publica lotes atomicamente e calcula seu hash. Repetição idêntica não
cria outra versão. Reversão troca o apontador ativo, preservando o histórico.
Há limite conservador de doze versões por fonte/loja; atingido esse limite, a
publicação é recusada para revisão humana de retenção e backup. Não há purge.

## Modelos e aprendizado contínuo

`analytics/commercial_models.py` mantém as regras como baseline e implementa:

- HistGradientBoostingRegressor leve para séries regionais/SKU, comparado à média
  de sete dias em três cortes temporais e horizontes 7/14/21/30. Exige 180 a 1.825
  dias contíguos e disponibilidade comprovada. Só seleciona árvore se o MAE cair
  pelo menos 5% em todos os horizontes. WAPE é indisponível quando o volume é zero.
- IsolationForest para desvios de comportamento, comparado à regra de cadência
  e queda de unidades. Exige ao menos cinquenta amostras de treino, vinte de
  validação, clientes separados e rótulos amadurecidos antes da validação.
  A seleção exige ganho de precisão sem piorar a sensibilidade e ao menos cinco
  eventos e cinco não eventos. A inferência usa somente atributos posteriores à
  validação. Não calcula probabilidade de abandono nem atribui causa.
- Monitoramento de MAE, WAPE e viés com vendas realizadas, sem realimentar treino
  com previsões. `atualizar_previsao` separa backtest, treino final e inferência.

`preparar_serie_regional` exige cobertura e disponibilidade em todos os dias,
além de três clientes em cada janela de trinta dias. Sem isso, recusa a previsão.
Não há promessa de sazonalidade anual nem calendário de feriados inventado.

Manifestos regionais e trinta previsões numéricas podem ser registrados pela
fachada Master. O banco também recusa ativar árvore que perdeu para o baseline.
Guarda hash, features, versão da biblioteca, janela e métricas, até 16 KiB por
manifesto e doze versões por escopo. Não serializa pickle/joblib nem replica o
dataset. O estimador existe só em memória durante o processo controlado. É
possível reativar a versão anterior sem apagar dados. A tela consulta métricas
somente após pedido explícito. Treino produtivo e alertas ML de clientes
permanecem condicionados a rótulos/histórico reais autorizados e à auditoria.

## Armazenamento eficiente

O Master pode medir PostgreSQL, maiores tabelas, metadados de objetos Storage e
registrar uma amostra por dia. Tamanho desconhecido do Storage aparece como
indisponível, nunca como zero. Em projeto com mais de uma organização, a métrica
global é recusada para não revelar a ocupação de terceiros. Avisos de 60%, 75% e
85% usam referências Free de 500 MB/1 GB, que precisam ser conferidas no painel.

`refresh_commercial_daily` só agrega sincronização concluída. Recalcula datas
afetadas, inclusive a data antiga de correções tardias. O checkpoint torna a
operação idempotente. Detalhes e versões de pedidos ficam preservados; um resumo
SKU/dia não permite reconstruir clientes ou cestas sozinho.

Medição local de 11/11/2026, apenas dados fictícios, 100 mil linhas, mil clientes
e trinta cidades, três repetições de preparo/arquivos e trinta de leitura:

| Operação | Mediana | Bytes |
| --- | ---: | ---: |
| Cálculo comercial completo | 9.758,92 ms | 36.366.661 de JSON detalhado |
| Desserialização de dez insights prontos | 0,040 ms | 5.121 |
| CSV sem compressão | não medido | 18.366.786 |
| CSV/GZIP, escrita / leitura | 479,84 / 467,85 ms | 2.816.874 |
| Parquet/ZSTD, escrita / leitura | 69,50 / 438,38 ms | 263.720 |

Os dois arquivos reconstruíram exatamente os dados, inclusive documento/linha,
cliente e cidade. São dados altamente repetidos: o ganho não prevê a compressão
real. A leitura de página mede CPU/JSON, sem autenticação, SQL ou rede. Não é um
antes/depois da latência produtiva. Reproduza com
`python scripts/benchmark_comercial.py --saida /tmp/benchmark-comercial.json`.

Com cerca de 13 MB medidos no projeto existente, não compensa remodelar o banco
ou comprimir linhas SQL. Arquivos frios, bucket privado, retenção e exclusão não
foram ativados. Exigem volume medido, autorização, backup verificável e teste de
recuperação antes de qualquer remoção. Nenhum dado real deve ir ao GitHub Actions.

## Bloqueios de dados reais

O conector atual continua excluindo cliente e município. Nenhuma allowlist de
endpoint foi ampliada. Faltam OpenAPI autorizado da conta, identificação estável,
definição de cidade, finalidade LGPD, reconciliação e auditoria do histórico.
Depois disso, a extensão deve preservar chaves e versões de documento/linha e
gravar dimensões minimizadas, sem inferir nomes ou copiar PII. Até lá, estados
vazios são intencionais. Os cenários fictícios exercitam a fase A; não comprovam
ganho de ML em vendas empresariais. A Reposição Inteligente não usa sinais
comportamentais para alterar pedidos automaticamente. GestãoClick permanece
permanentemente somente leitura.
