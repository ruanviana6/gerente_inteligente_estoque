# Evolução para revisão, sem ativação produtiva

## Complemento de identidade visual e revisão

A interface recebeu superfícies claras e detalhes inspirados no terracota da
Flatter, no vinho do Êxtase e no ameixa do Êxtase Elixir. A marca agora é uma
imagem local, com descrição acessível; a origem e a diferença em relação à
imagem mostrada na conversa estão registradas em `UX_FLATTER.md`.

Três observações posteriores da revisão do PR foram reproduzidas e corrigidas:
as cestas exigem status completos de todos os produtos do período, a abrangência
do período atual é preservada mesmo sem cobertura do período anterior, e itens
sem nome utilizam identificação explícita pelo SKU. Há testes de regressão para
os três casos e para declarações inválidas de abrangência.

Verificações locais deste complemento: Python 3.12 com 777 testes e 66 subtestes
aprovados; Ruff aprovado; PostgreSQL 17 descartável com 342 verificações
`APROVADO`; e 20 telas verificadas com Chromium em 390 e 1.440 px usando dados e
transporte fictícios. Essas verificações não comprovam autenticação ou migração
no Supabase produtivo. Consulte o GitHub Actions para os resultados do commit
final nas duas versões do Python.

## Estado inicial conferido

Base `implementacao/web-supabase`, commit `a5157eef89da159db7182c85c132ca907c9b6e50`.
O PR #7 está incorporado; o PR #5 e as Issues #3/#6 continuam abertos. A `main`
permanece no MVP (`741068d6f2783dc424a6f022a5205b7a5c83bc8c`). Nenhuma dessas
branches foi modificada diretamente. A base é a branch indicada pelo proprietário
como publicada; não há acesso às configurações administrativas do Streamlit para
comprovar qual commit seu processo remoto executa.

O Supabase existente estava `INACTIVE` no início e passou a `ACTIVE_HEALTHY`
na conferência final. A consulta remota de leitura confirmou as quatro migrações
originais, 16 tabelas públicas com RLS e nenhuma função pública SECURITY DEFINER.
O banco respondeu e tinha aproximadamente 13 MB. Não se aplicaram migrações,
reativação, restauração, alteração de usuários ou Secrets produtivos.

Security Advisor: um WARN por proteção contra senhas vazadas desativada. Segundo
[a documentação oficial](https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection),
ela exige Pro ou superior. Não foi contratado plano pago; permanece uma limitação
registrada, com senhas fortes e exclusivas orientadas, sem afirmar equivalência
entre as proteções. Performance Advisor: 13 INFO de índices não usados. Não foram
removidos: baixa atividade/retomada não prova que sejam desnecessários. A indicação
não é uma aprovação de desempenho ou segurança de toda a aplicação.

A URL pública respondeu com redirecionamento para autenticação da plataforma;
o acesso seguinte expirou. Isso não valida login interno, permissões ou
persistência produtiva. As novas migrações seguem pendentes de aprovação e aplicação.

Baseline anterior às alterações: Python 3.12, 465 testes e 66 subtestes aprovados;
única falha em smoke HTTP por sockets bloqueados no sandbox. Repetição do teste
com acesso ao loopback aprovada. Ruff e pip check aprovados.

## Correções verificadas do PR #5

| Apontamento original | Causa | Correção e reprodução |
| --- | --- | --- |
| [Snapshot histórico](https://github.com/ruanviana6/gerente_inteligente_estoque/pull/5#discussion_r4405293536) | Fotografia compartilhada por data era sobrescrita | Tabela versionada por lote, backfill de catálogo imutável e consulta com corte por ingestão. Teste legado prova saldo 10/foto 99 antes e foto 10 após a migração. |
| [CSV sem autorização](https://github.com/ruanviana6/gerente_inteligente_estoque/pull/5#discussion_r4405293539) | Exportava o painel inteiro, removendo apenas finanças | Allowlist de reposição; tendências exigem trends.view e ABC também finance.view. Revalida contexto e geração após serializar. Regressões em test_exportacoes_permissoes.py. |

As duas threads continuam abertas para conferência humana. Estar `outdated` não
comprova correção. Não foram resolvidas automaticamente.

A revisão interna independente também reproduziu e corrigiu avisos financeiros
na UI após revogação, validação da origem no método HTTP interno, mudança da vida
da sessão e checagem da fonte antes de consultar o ERP. Consulte
`AUDITORIA_EXECUCAO_FINAL.md`. Essa revisão não substitui a auditoria independente
pós-implementação e a validação produtiva solicitadas na Issue #3.

## Implementação

- `integracoes/gestaoclick.py` e `integracoes/runtime.py`: conector GET, TLS,
  destino/rotas fechados, sem redirects, quotas, paginação completa e mapas
  declarativos a partir do contrato conferido. Nenhum endpoint de escrita.
- `sincronizacao/service.py` e migração de vendas: prévia Master, staging por
  páginas, reconciliação, hash de conteúdo, checkpoints, concessões, confirmação
  transacional e versões imutáveis. Nenhum dado é apagado por ausência em consulta.
- `analytics/`: unidades líquidas, pedidos concluídos, agrupamentos, períodos
  comparáveis, crescimento, calendário, recorrência com limites e cestas reais.
- `web/sales_service.py`, `web/sales_page.py`, `web/integration_page.py`: navegação
  Flatter com autorização backend, dados sincronizados e ações sob demanda.
- `web/exports.py`, `web/service.py` e `web/reports.py`: contratos explícitos e
  proteção de colunas, avisos e contexto final de exportação.
- Duas migrações geradas pela CLI v2.122.0. Quatro migrações antigas preservadas.
  A nova tabela de fotos guarda lote/data/SKU/estoque; a tabela antiga permanece.
  Empates legados de timestamp usam desempate determinístico, sem inventar a
  ordem real de eventos que não foi registrada.

O motor de estoque não foi alterado. Catálogo e saldos continuam por CSV até
conferir os campos operacionais reais da API. A nova cópia analítica de vendas é
separada; não transforma uma extração incremental em substituição completa.
A sincronização recusa declarar período completo quando a cópia final não reconcilia com a origem. Quotas conjuntas de staging também são serializadas entre lojas.

Financeiro, emissão fiscal, compras automáticas, previsão avançada e sincronização
automática não foram acrescentados.

## Verificações locais efetivamente executadas

| Verificação | Resultado |
| --- | --- |
| Suíte completa Python 3.11.17 | 764 testes e 66 subtestes aprovados |
| Suíte completa Python 3.12.14 | 764 testes e 66 subtestes aprovados |
| Ruff e git diff --check | Aprovados |
| pip check | Sem dependências incompatíveis |
| pip-audit | Sem vulnerabilidades conhecidas após atualizar pip do ambiente para 26.3 |
| PostgreSQL 17 descartável | 338 mensagens APROVADO no conjunto incluindo restauração e concorrência |
| Migração de legado | Bug reproduzido antes, backfill recuperado depois, dados preservados |
| Concorrência | Vínculo de usuário, importações simultâneas de estoque e quatro disputas reais de sincronização |
| Chromium 1440/390 px | 12 capturas Flatter existentes e fluxos de revogação/download/logout aprovados |
| Novas telas | AppTest e Chromium aprovados: oito capturas adicionais em 1440/390 px, análise, prévia, cancelamento e confirmação explícita |

Os números acima pertencem à rodada local registrada. Acréscimos posteriores e os
resultados finais do CI devem ser conferidos nos links e artefatos do PR #8.
Primeira rodada integral do PR: [Testes Python/Auth/RLS](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/42936208547) e [interface/desempenho](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/42936208575), ambos aprovados no commit `8c262a7`. Auth/PostgREST descartáveis: cinco testes aprovados.

JUnit local: `/tmp/estoque-final311.xml` e `/tmp/estoque-final312.xml`. O ambiente
local não equivale ao Supabase remoto ou Streamlit Community Cloud.

O primeiro pip-audit encontrou seis avisos na ferramenta pip 25.0.1 do ambiente,
sem achados nas dependências da aplicação. Atualização para 26.3 eliminou esses
avisos; o CI fixa essa versão na preparação dos jobs relevantes.

## Desempenho

Arquivos sanitizados em `evidencias_evolucao/antes.json`, `depois.json` e
`interface.json`. AppTest verdadeiro, transporte fictício, 40 ms por requisição,
100/1.200 SKUs, três iterações, mesma máquina Python 3.12. Não mede pintura do
navegador nem latência remota. A amostra pequena está sujeita à disputa por CPU;
não comprova melhora estatística nem promete disponibilidade produtiva.

| Operação, 1.200 SKUs | Antes p50/p95 (s) | Depois p50/p95 (s) |
| --- | --- | --- |
| Painel | 0,801 / 0,840 | 0,912 / 1,009 |
| Estoque | 1,065 / 1,130 | 0,851 / 0,944 |
| Reposição | 0,939 / 1,043 | 0,820 / 0,911 |
| Abrir relatórios | 0,095 / 0,095 | 0,091 / 0,091 |
| Gerar relatório | 2,066 / 2,146 | 1,360 / 1,471 |

Na primeira rodada do CI, com cinco repetições, Painel p50/p95 foi 0,609/0,652 → 0,614/0,651 s; Estoque 0,634/0,645 → 0,611/0,645 s; Reposição 0,595/0,635 → 0,620/0,649 s. As diferenças são pequenas e não sustentam afirmar ganho estatístico.

O painel teve aumento nesta rodada local. Monitorar novamente no CI e no ambiente
publicado após autorização; não remover revalidação de acesso para reduzir esse
custo. A otimização Flatter anterior e suas evidências históricas foram preservadas.
O workflow agora compara a candidata com `a5157ee`, não com a UI anterior ao Flatter.

## Ativação e bloqueios

1. Projeto já voltou a ACTIVE_HEALTHY. Retomar por Resume project somente se houver nova pausa.
2. Após banco acessível, revisar backup, aplicar migrações pendentes sob aprovação,
   verificar Auth/RLS/advisors e contas de teste autorizadas.
3. Aprovar correções e conferir OpenAPI/loja/status/retornos/permissões reais.
4. Inserir credenciais e mapas somente nos Secrets protegidos, mantendo integração
   desligada até as condições de `ATIVACAO_GESTAOCLICK.md` serem atendidas.
5. Autorizar GET e amostra limitada, reconciliar com ERP, depois autorizar carga
   completa. Não existe scheduler de ERP nem operação mutável no conector.
6. Executar auditoria independente da versão atual e validar URL autenticada antes
   de dados empresariais. Não houve merge nem publicação automática.
