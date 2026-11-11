# Reposição Inteligente

Esta versão organiza as decisões internas da Flatter sem criar solicitações no GestãoClick. A integração com o ERP permanece somente leitura. As migrações devem ser revisadas e testadas em ambiente descartável antes de qualquer aplicação ao projeto existente.

## Ciclos e embalagem

Em **Regras de Reposição**, um usuário com `parameters.edit` informa prazo de produção e transporte, segurança, cobertura, unidades por caixa, lote mínimo, múltiplo e limite máximo por SKU. A embalagem não recebe valor automático: sem conversão cadastrada, a interface explica que não é possível calcular caixas. Cada alteração gera uma versão com vigência e auditoria; uma edição concorrente invalida a confirmação.

O CSV de embalagens aceita UTF-8, até 256 KiB e 500 produtos, com cabeçalhos `sku;unidades_por_caixa;vigencia` e, opcionalmente, `lote_minimo;multiplo;maximo;observacao`. O SKU deve existir no catálogo atual. A prévia é obrigatória, duplicidades são recusadas e a confirmação grava o lote em uma transação. Não são usados valores de exemplo como embalagem real.

Os ciclos iniciais de 14 e 21 dias e a referência habitual de 80 caixas podem ser alterados na aplicação. A referência não limita a carga, nem comprova disponibilidade de fábrica ou caminhão.

## Cálculo e limites

A projeção usa a média diária elegível existente no motor, saldo observado, reservas, prazo e segurança. A demanda de 30 dias é mostrada separadamente; a necessidade considera o ciclo selecionado e a posição projetada na chegada. Recebimentos só entram quando existe quantidade e data confirmadas. O campo antigo de pendências não contém data comprovada e não é somado nesta simulação.

A simulação compara solicitar hoje ou após o ciclo escolhido; se a ruptura ocorrer antes da chegada estimada, o risco é destacado. A data limite é o primeiro dia em que o saldo projetado alcança a segurança, menos o prazo informado. O calendário usa dias corridos; feriados e dias úteis não são presumidos. O estoque da importação é identificado pela sua data; se estiver antigo, atualize-o antes de tomar uma decisão.

A necessidade líquida é arredondada para o mínimo múltiplo comum entre embalagem e múltiplo de produção e respeita o lote mínimo. Se o resultado ultrapassar o limite informado, a tela exige revisão em vez de cortar silenciosamente. Dados insuficientes bloqueiam o cálculo; disponibilidade histórica incerta exige reconhecimento explícito. Uma previsão não representa venda garantida nem recupera demanda perdida em rupturas.

Propostas confirmadas são registros internos, com SKU, quantidade e versão da embalagem. A conversão usada fica preservada no próprio registro; a operação não envia pedidos. O histórico e as decisões humanas são auditados e não modificam o estoque oficial.

## Segurança e fases futuras

A leitura das regras exige `replenishment.view` ou `parameters.edit`; edição exige `parameters.edit`. A exportação de estoque exige `inventory.view` e a nova permissão explícita `inventory.export`; custos só aparecem com `finance.view`. Propostas exigem `decisions.write` e `replenishment.view`. Os downloads revalidam identidade e permissões ao gerar o arquivo.

O catálogo de estoque atual ainda não identifica loja e fonte por produto. As regras são vinculadas à organização e ao catálogo interno, com loja não informada; não são consolidadas lojas distintas. Antes de usar estoque por loja, confirme os campos e a semântica no OpenAPI autorizado e evolua o contrato e as chaves. Treinamento de ML, previsões probabilísticas e otimização por capacidade física dependem de dados, validação e limites reais; não estão ativos nesta fase.
