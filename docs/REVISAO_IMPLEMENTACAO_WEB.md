# Revisão de integração durante a implementação web

Data: 28/10/2026, calendário de America/Fortaleza.

Esta revisão acompanha a implementação da Issue #2 no [PR #5](https://github.com/ruanviana6/gerente_inteligente_estoque/pull/5). Ela confronta contratos entre interface, autenticação, cálculo, relatórios e PostgreSQL. **Não substitui nem declara concluída a auditoria independente da Issue #3.**

## Escopo examinado

- `web/service.py`: identidade por sessão, permissões antes das RPCs, normalização de dados, importação, exportação e relatórios históricos.
- `web/ui.py`: navegação autorizada, formulários do Master, configuração de relatórios e proteção de campos financeiros.
- `web/auth.py`, `web/api.py` e `web/settings.py`: contratos usados pela fachada, segregação de chaves, revalidação e tratamento de falhas.
- `web/reports.py` e `web/email_delivery.py`: fechamento mensal, seleção de blocos, permissões atuais e simulação.
- Migrações SQL: retorno de usuários, herança de permissões, seleção de lotes mensais e configuração/fila de relatórios.

Os testes da fachada usam um transporte fictício para variar permissões durante a mesma operação. Isso exercita a integração Python, mas não comprova por si só Auth remoto ou RLS. A suíte SQL e o ambiente Supabase descartável do CI são verificações separadas.

## Problemas identificados e regressões

| Problema reproduzido | Comportamento corrigido | Regressão |
| --- | --- | --- |
| `pd.Timestamp(0)` aceitava um número como 01/01/1970 na fachada | Datas exigem tipo civil e texto ISO explícito, sem horário nem fuso | `test_datas_civis_rejeitam_numeros_horario_e_fuso` |
| `DataFrame.to_json()` arredondava custo `1e-12` para zero antes da importação | JSON padrão preserva o float e representa ausência como `null` | `test_serializacao_preserva_custos_pequenos_sem_arredondar_para_zero` |
| O formulário administrativo enviava permissões reservadas ao Master, mesmo com valor `false`; o banco recusava a atualização | Permissões não delegáveis não participam do formulário nem do payload | `test_master_edita_usuario_sem_enviar_permissoes_reservadas` |
| Salvar um usuário com exceção parcial, como negar custos, convertia permissões herdadas não exibidas em negativas explícitas | Cada permissão pode herdar o perfil, permitir ou negar; editar nome não altera a herança | `test_master_preserva_heranca_ao_editar_usuario_com_override_parcial` e `test_master_pode_restaurar_heranca_de_uma_permissao` |
| Revogar `reports.view` após gerar um relatório podia deixar apenas `reports.export` vigente e produzir uma exceção de permissão fora do contrato da UI | A fachada confere as duas permissões no contexto final e retorna erro seguro | `test_revogacao_de_visualizacao_bloqueia_exportacao_com_erro_seguro` |

A revisão também identificou que a variável que habilita o scheduler precisa estar disponível antes de o job iniciar. A configuração de `REPORT_SCHEDULER_ENABLED` deve ser feita como variável do repositório; os segredos do worker continuam no environment protegido. Os horários da rotina e as tentativas adicionais estão descritos em `docs/ATIVAR_RELATORIOS.md`.

## Verificações de integração acrescentadas

- Custos são removidos antes do motor e da saída para quem não tem `finance.view`, mesmo se o transporte fictício retornar colunas financeiras indevidas.
- Uma revogação financeira entre consulta e resposta elimina os valores já carregados.
- Exportação de reposição exige nova autorização, respeita fornecedor e preserva a neutralização de fórmulas do PR #4.
- A importação conserva SKU textual e datas brasileiras normalizadas; rejeita abrangência invertida, venda fora do período e custos enviados sem acesso financeiro.
- A demonstração exige Master, além de permissão de importação.
- O relatório consulta o mês histórico solicitado, recusa fechamento incompleto e não inventa comparação quando o mês anterior está indisponível.
- A configuração de relatórios preserva destinatários, dia mensal e blocos escolhidos, com simulação refletida corretamente na interface.
- Duas instâncias de estado Streamlit não compartilham cliente nem identidade.

## Evidências de execução e limites

Na [execução 40741191119](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/40741191119), commit `045c12cedcb94c02f00a0da535850f17e415d1c6`, os logs foram consultados diretamente durante esta revisão:

| Etapa | Evidência naquele commit |
| --- | --- |
| Python 3.11 | 334 testes aprovados, 13,60 s; Ruff e `pip check` aprovados |
| Python 3.12 | 334 testes aprovados, 15,77 s; Ruff e `pip check` aprovados |
| Dependências | `pip-audit` não identificou vulnerabilidades conhecidas |
| PostgreSQL 17 | Job de RLS/autorização e repetição após dump/restauração aprovados |
| Supabase Auth e PostgREST reais descartáveis | Job falhou naquele commit; diagnóstico e reexecução são obrigatórios antes da entrega |

Esses números pertencem ao commit indicado, anterior às últimas regressões de formulários e exportação. **O resultado final e o commit efetivamente aprovado devem ser conferidos no PR #5.** Um job Python aprovado não transforma uma falha do job Auth em validação completa.

No executor local foram realizadas reproduções Python de normalização, cálculo e serialização, compilação dos testes e verificação do diff. Pytest não estava instalado nesse executor; nenhuma execução local da suíte foi presumida.

Esta revisão não efetuou envio real de e-mail, não usou dados empresariais e não conectou o GestãoClick. Testes de UI com AppTest e HTTP em loopback não validam uma URL publicada nem uma tela móvel real. Publicação, bootstrap do Master, configuração protegida de Secrets e remetentes permanecem sujeitos aos procedimentos em `docs/CONFIGURACAO_WEB.md` e `docs/ATIVAR_RELATORIOS.md`.

O histórico preservado por lote ocupa espaço no plano gratuito. A declaração de abrangência completa do CSV exige conferência humana na origem; o aplicativo não pode provar que uma venda ausente nunca ocorreu. Esses limites devem continuar visíveis na auditoria independente posterior.
