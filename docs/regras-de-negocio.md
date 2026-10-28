# Regras do Gerente Inteligente de Estoque

## Persistência e relatórios na versão web

As fórmulas do motor auditado no PR #4 foram preservadas. A web consulta um lote
do PostgreSQL autorizado para o usuário, usando a data observada do saldo como
referência de análise. Alterações em parâmetros atuais não reescrevem os valores
guardados nos lotes de fechamento anteriores.

A importação continua completa, mas cada lote tem sua própria versão de catálogo,
vendas e custos. Uma importação idêntica é reutilizada. SKUs ausentes em um novo
lote deixam de aparecer na referência atual, sem apagar os lotes históricos.
Uma fotografia antiga não substitui a referência operacional de data posterior.

Relatórios exigem mês civil encerrado em America/Fortaleza, abrangência declarada
de vendas cobrindo todo o mês e snapshot do último dia coerente com o catálogo.
Sem fechamento confiável, a geração é recusada. Sem período anterior comparável,
a variação percentual fica indisponível. A abrangência declarada depende da
conferência humana da origem, e não prova que o arquivo recebido esteja completo.

As permissões de estoque, reposição, tendências e finanças restringem os blocos
do relatório. Custos são protegidos em tabela própria e removidos também antes
do cálculo quando não autorizados. E-mails preparados contêm aviso e link
autenticado, sem indicadores ou anexos financeiros.

O scheduler revalida conta ativa, destinatário e permissões na geração e antes
do envio. Repetições são identificadas por organização, mês, destinatário e modo
de simulação/envio; falhas claras têm até três tentativas. Entrega incerta não
é repetida automaticamente. Não há operações de escrita no GestãoClick.

O GestãoClick será a fonte da verdade após a integração; o MVP só trabalha com dados simulados e CSV normalizado.

A integração futura será sempre somente leitura. Nenhum componente pode criar, editar ou excluir objetos ou movimentos no GestãoClick; consultas futuras também dependem de autorização. A regra permanente está no `AGENTS.md`.

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

