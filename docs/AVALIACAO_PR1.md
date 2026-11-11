# Planejamento anterior e versão web

O PR #1 continua aberto, com head `0e3a6cd2e309a89a5afa457dac9d1c9fc17ba37d`.
A consulta ao GitHub retornou `mergeable=false` e `mergeable_state=dirty` contra
seu alvo `main`. Não foi feito merge, fechamento ou resolução automática.

Seus requisitos de aplicativo único, Auth por e-mail, Master por UUID, permissões
individuais, RLS, custos separados, relatórios controlados e limite de custo zero
foram mantidos. A regra de GestãoClick permanentemente somente leitura permanece
obrigatória, inclusive após a nova preparação do conector.

As proibições temporárias de implementar integração e análises futuras foram
superadas pelo pedido explícito de execução final de 10/11/2026. Registramos essa
precedência em AGENTS.md e nas páginas de planejamento. Não interpretamos a
autorização de desenvolvimento como autorização para dados empresariais, chamada
real, credenciais no Git, nova cobrança ou ativação automática.

Os documentos antigos descrevem tarefas planejadas; não comprovam o estado atual.
README, ENTREGA_EVOLUCAO.md e ATIVACAO_GESTAOCLICK.md documentam a candidata e seus
bloqueios. A `main` ainda contém o MVP e não é a base deste PR. Integrar o PR #1
automaticamente poderia reintroduzir instruções e descrições de estado anteriores;
qualquer incorporação deverá comparar conteúdo com a documentação da versão web.
