# Entrega da implementação web

Data: 28/10/2026. Trabalho da Issue #2 na branch `implementacao/web-supabase`, [PR #5](https://github.com/ruanviana6/gerente_inteligente_estoque/pull/5). As correções auditadas do PR #4 foram preservadas, e a `main` não foi modificada por esta implementação.

O código da aplicação web e o banco estão implementados. **A ativação da conta do proprietário e a publicação no Streamlit ainda não foram realizadas.** Portanto, este documento não declara uma URL em funcionamento nem autorização para dados reais.

## Funcionalidades e forma de validação

| Área | Entrega | Verificação |
| --- | --- | --- |
| Persistência | PostgreSQL, lotes completos versionados, snapshots, custos separados, parâmetros, decisões e auditoria | SQL no PostgreSQL 17, no Supabase existente e RPCs reais em Supabase descartável |
| Acesso | E-mail/senha, renovação, logout, Master administrativo e perfis com exceções individuais | Testes Python, Auth/PostgREST reais, RLS e testes negativos |
| Estoque | Motor auditado, demanda elegível, cobertura, risco durante prazo, reposição, tendências e ABC a custo | Regressões do MVP e integração da fachada com dados fictícios |
| Interface | Login, painel, flash rotativo, filtros, detalhes, histórico, importação e administração | Streamlit AppTest e inicialização HTTP em loopback |
| Relatórios | Mês fechado, lote histórico, comparação quando possível, exportação e blocos por permissão | Testes Python, fechamento via RPC real e regressões de revogação |
| Agendador | Fila, simulação, revalidação, idempotência, tentativas limitadas e histórico | Testes Python e SQL; nenhum e-mail externo enviado |
| Publicação | Entrada `app.py`, versões fixadas, configuração Streamlit e exemplo de Secrets | Inicialização no CI; URL pública e comportamento visual em celular ainda pendentes |

Os testes de interface utilizam serviços fictícios para controlar cenários. Os testes de Auth usam os serviços verdadeiros do Supabase em containers descartáveis. São camadas distintas de evidência; nenhuma delas equivale a testar uma aplicação publicada.

## Resultados comprovados

A [execução 40743605948](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/40743605948), commit `bc93aa119c954d7c1249f03add75dddaa66725bc`, concluiu todos os jobs com sucesso:

- **347 testes Python**, tanto em 3.11 quanto em 3.12, incluindo os cenários preservados do MVP, backend, relatórios, segurança e interface.
- **4 testes com Supabase Auth e PostgREST reais**: login/importação/consulta/relatório; senha incorreta e cadastro público bloqueado; desativação com JWT ainda válido; renovação e logout.
- **75 verificações SQL**, repetidas após dump e restauração em outro PostgreSQL descartável.
- **1 teste concorrente** com duas sessões de Masters de organizações distintas disputando o mesmo UUID.
- Ruff, `pip check` e `pip-audit` aprovados, sem vulnerabilidades conhecidas identificadas naquele ambiente.

Depois dessa execução, a migração de concorrência recebeu a versão efetivamente aplicada no projeto remoto. Os testes de Auth foram reforçados para conferir o motivo do bloqueio de cadastro e a chamada real de renovação. Foram acrescentados três cenários sobre precisão e ausência de indicadores nos relatórios. A versão final e seus resultados adicionais estão nos checks do PR #5; os números acima identificam explicitamente a execução já comprovada, sem atribuí-los a um commit diferente.

As dependências foram instaladas e as suítes completas executadas no GitHub Actions. O executor de edição não tinha Pytest, Ruff ou Streamlit instalados; nele foram usados compilação, verificações do diff e reproduções Python pontuais. Não há alegação de execução local dessas suítes.

## Supabase existente

Projeto `csiqpzoudruzppfbpqqx`, São Paulo (`sa-east-1`), plano Free consultado. Não foi criado outro projeto nem contratado serviço pago.

| Migração aplicada | Conteúdo |
| --- | --- |
| `20261028171810_web_privada.sql` | Schema, tabelas, constraints, funções, grants, RLS, bootstrap e fila |
| `20261028174209_ajustes_validacao.sql` | Índices de chaves estrangeiras e negações explícitas para tabelas privadas da rotina |
| `20261028181759_proteger_cadastro_concorrente.sql` | Serialização do vínculo de uma identidade entre organizações |

As **16 tabelas públicas da aplicação têm RLS habilitado**. As 75 verificações SQL foram executadas no projeto existente dentro de uma transação com rollback. A conferência posterior encontrou zero contas Auth, zero perfis, zero lotes e nenhum schema de teste remanescente. O proprietário ainda deve criar sua própria conta. O Security Advisor retornou `lints: []` após as três migrações. Isso comprova os controles verificados, não uma garantia de ausência de vulnerabilidades.

A migração de concorrência foi exercitada no PostgreSQL descartável antes da aplicação. O teste concorrente não roda no projeto real e recusa o schema Auth completo, para evitar alterações de teste persistentes nesse ambiente.

## Correções relevantes durante a integração

- Datas numéricas deixaram de ser aceitas silenciosamente como datas civis.
- A importação preserva custos pequenos, sem arredondá-los para zero ao serializar.
- Relatórios e CSVs preservam a precisão da cobertura calculada, inclusive para demandas grandes; ausência de indicador permanece nula.
- Formulários e backend recusam permissões reservadas antes da criação de identidades; a interface não envia essas chaves por engano.
- A edição de usuário preserva permissões herdadas e oferece escolhas explícitas de herdar, permitir ou negar.
- Exportações revalidam acesso, inclusive quando `reports.view` ou `finance.view` são revogados durante a operação.
- O vínculo concorrente de um UUID não permite que outro Master modifique uma conta de organização diferente.
- O ambiente de teste mantém o provedor de e-mail ativo enquanto bloqueia cadastro público; a falha anterior do job Auth foi diagnosticada e corrigida.
- A ativação do scheduler usa variável de repositório, e as oportunidades adicionais de execução respeitam os limites de tentativas e idempotência no banco.

A [revisão de integração](REVISAO_IMPLEMENTACAO_WEB.md) registra achados e evidências intermediárias, inclusive a falha de Auth que existia antes da correção.

## Ações externas do proprietário

1. **Auth:** no painel Supabase, manter o provedor de e-mail habilitado e desligar cadastro público e logins anônimos.
2. **Master:** criar sua identidade confirmada em Authentication → Users; executar `private.bootstrap_master(UUID, nome, organização)` como `postgres`. Não há senha padrão. O procedimento completo está em [CONFIGURACAO_WEB.md](CONFIGURACAO_WEB.md#2-ativar-a-conta-master).
3. **Streamlit:** conectar o repositório, selecionar a branch do PR e `app.py`, escolher Python 3.12 e configurar os Secrets protegidos conforme o exemplo. Publicar uma única aplicação e validar a URL com dados fictícios. Não enviar chaves ou senhas pelo chat.
4. **E-mail, opcional:** configurar segredo dedicado do worker e agendamento para simulação. Envio real exige remetente/provedor compatível, URL verificada, permissão dos destinatários e os dois controles de autorização. Recuperação de senha por e-mail tem configuração independente. Ver [ATIVAR_RELATORIOS.md](ATIVAR_RELATORIOS.md).

Não há conector autorizado para operar a conta do Streamlit, gravar seus Secrets ou configurar Auth no painel. Esses são bloqueios de ativação externos; não impedem testar o código e o banco. O merge continua sob revisão do proprietário. O schedule do GitHub só passa a funcionar na branch padrão.

## Limitações e riscos restantes

- Sem URL publicada verificada, ainda faltam a validação em navegador real, computador e celular, e o login do proprietário na hospedagem definitiva.
- O envio externo e a recuperação por SMTP não foram exercitados com remetente real. Permanecem desabilitados por padrão.
- Histórico versionado ocupa espaço. Limites e eventuais pausas dos serviços gratuitos exigem acompanhamento; o teste de backup descartável não substitui um procedimento operacional de recuperação de dados reais.
- O sistema confere formato e consistência, mas não prova que um CSV declarado completo contém todas as vendas da origem. Snapshot ausente continua sendo informação desconhecida.
- A revisão integrada e os testes de segurança não substituem a auditoria independente da versão web completa definida na Issue #3.

GestãoClick não foi conectado. NF-e, compras automáticas e modelos avançados permanecem fora do escopo. A regra de integração futura estritamente somente leitura foi preservada.
