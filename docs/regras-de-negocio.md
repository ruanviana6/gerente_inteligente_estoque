# Regras do Gerente Inteligente de Estoque

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
