# Revisão independente de segurança durante a implementação

Esta revisão foi realizada por um agente separado dos autores das correções,
na branch `evolucao/leitura-erp-inteligencia-vendas`, com dados fictícios.
Ela acompanha a implementação autorizada no pedido de execução final. Não
substitui a execução posterior e separada de `docs/REVISAO_FINAL_CODEX.md`
sobre o commit aprovado, nem valida a aplicação publicada com dados reais.

## Resultados executados

Ambiente local: Python 3.12.14, dependências fixadas de `requirements.txt` e
transporte fictício nos testes de autenticação e GestãoClick desta revisão.

```bash
.venv/bin/python -m pytest \
  tests/test_revisao_seguranca.py \
  tests/test_web_auth.py tests/test_web_api.py \
  tests/test_flatter_seguranca.py tests/test_importacao_seguranca.py \
  tests/test_exportacoes_permissoes.py tests/test_email_delivery.py \
  tests/test_web_reports.py -ra \
  --junitxml=/tmp/revisao-seguranca-final.xml
.venv/bin/ruff check tests/test_revisao_seguranca.py
```

Resultado observado: **280 testes e 6 subtestes aprovados**, em 7,94 segundos.
Ruff aprovado para o arquivo novo. Essa contagem é um recorte de segurança;
não deve ser somada à suíte completa, porque inclui testes que também fazem
parte dela. Os resultados gerais e do CI estão no registro final da entrega.

Os oito cenários novos em `tests/test_revisao_seguranca.py` verificam:

- Revogação de finanças e tendências durante o cálculo, incluindo textos
  indiretos de ABC e de custos na tabela devolvida à interface.
- Logout e novo login da mesma conta durante extração ou retorno de RPC.
- Negação da fonte pelo banco antes de utilizar o leitor externo.
- Integração desativada, domínio externo e rota não autorizada recusados na
  própria fronteira HTTP, antes de transmitir cabeçalhos de autenticação.

Os testes existentes executados verificam também permissões individuais,
mudança de usuário e organização durante exportação, sessão expirada,
desativação, recuperação de acesso, metadados de usuário sem autoridade,
isolamento entre instâncias, ausência de cache global de autorização,
limites e validação de CSV, relatórios por permissão e envio simulado.

## Problemas encontrados e correções conferidas

| Achado | Evidência | Correção e verificação |
| --- | --- | --- |
| Remover colunas financeiras não removia textos de ABC e custo após revogação no cálculo. | Dois casos novos falharam antes da correção. O painel ainda devolvia avisos financeiros sem a concessão final. | `web/exports.py` reconstrói avisos e revisão com flags autorizadas. `web/service.py` reaplica a filtragem com o contexto final. Ambos os casos passaram. |
| A fronteira interna `_get` podia transmitir tokens sem repetir a validação de origem e de integração habilitada. | Três testes com transporte fictício falharam: desativado, domínio externo e rota fora da allowlist. Não existia caminho correspondente na UI; era risco de uso interno futuro. | `integracoes/gestaoclick.py` passou a revalidar configuração, origem e rota antes de preparar os cabeçalhos. Os três testes passaram. |
| O coordenador inicialmente comparava identidade e organização sem distinguir nova vida da sessão. | Inspeção direta do coordenador. Os cenários independentes alteram somente `auth.generation`, preservando UUIDs e permissões. | O vínculo de autorização inclui a geração antes e depois da leitura e da RPC. Ambos os testes passaram. |
| A autorização de fonte no banco acontecia depois da extração externa. | Inspeção da ordem de chamadas: `read_all()` antecedia `sync_begin`. Isso permitia disparar uma leitura antes da negativa da organização. | `sync_authorize_source` confere Master, organização, fonte e loja antes de chamar o leitor. O teste independente comprovou ausência de leitura quando a RPC nega acesso. |

Nenhum teste desta revisão transmitiu credenciais ou consultas ao GestãoClick.
O cliente continua sem métodos de escrita, sem corpo nas consultas GET e sem
redirecionamentos. A configuração protegida é separada dos controles da tela.

## Revisão das migrações e da sincronização

A leitura das migrações conferiu as seguintes propriedades do código:

- Funções públicas novas usam `SECURITY INVOKER`; as operações privilegiadas
  ficam em `private`, com `search_path` vazio, grants explícitos e autorização.
- As tabelas analíticas novas têm RLS e não concedem leitura ou DML direto
  a usuários da aplicação. Custos e dados pessoais não pertencem ao contrato.
- O escopo inclui organização, fonte, loja, documento e linha; SKU não é
  considerado identificador global.
- A checagem de permissão e Master usa o perfil protegido no banco. Os helpers
  existentes verificam também a sessão em `auth.sessions`, sua titularidade,
  expiração, conta ativa e ausência de autenticação anônima.
- O conserto histórico cria fotografias com origem por lote e recupera as
  fotografias legadas a partir do catálogo imutável de cada lote. A tabela
  antiga e os dados existentes são preservados.

Foram apontados ao autor dois riscos adicionais de integridade e operação:

1. Reutilizar uma chave concluída com conteúdo diferente, mas com a mesma
   contagem e quantidade, precisava ser recusado. O coordenador agora calcula
   um hash canônico e `sync_begin` vincula esse hash à chave. O hash é um
   vínculo de idempotência do cliente confiável, não prova de abrangência
   comercial ou de cobertura completa do ERP.
2. Execuções expiradas ou interrompidas precisavam de cancelamento seguro pelo
   Master, para não bloquear o limite de staging indefinidamente. A função de
   cancelamento foi ajustada para esse fluxo, preservando as versões já
   consolidadas.

Os cenários SQL correspondentes estão em `tests/sql/sync.sql`. A leitura do
código não equivale à aprovação desses cenários. A evidência de PostgreSQL,
RLS, concorrência e restauração deve ser consultada no CI do commit entregue.
Esta revisão não executou alterações no banco produtivo.

## Conferência final da candidata ao PR #8

Foi conferido diretamente no GitHub que o [PR #8](https://github.com/ruanviana6/gerente_inteligente_estoque/pull/8)
foi aberto em rascunho para `implementacao/web-supabase`, inicialmente no commit
`8c262a7b8dea6f9432340583141f69bd8c3d0be8`. As verificações devem acompanhar o
commit final do PR; arquivos que mudaram depois daquela primeira publicação
precisam da rodada correspondente do CI.

Uma segunda rodada independente local executou os testes novos de segurança,
sincronização, conector, configuração protegida, exportações e as duas páginas
novas:

```bash
.venv/bin/python -m pytest tests/test_revisao_seguranca.py \
  tests/test_sync_service.py tests/test_gestaoclick.py \
  tests/test_gestaoclick_runtime.py tests/test_exportacoes_permissoes.py \
  tests/test_sales_interface.py tests/test_integration_interface.py \
  -ra --junitxml=/tmp/revisao-final-pr8.xml
```

Resultado observado: **243 testes aprovados**, em 14,88 segundos. Ruff do
arquivo independente e `git diff --check` também foram aprovados. Esses testes
se sobrepõem ao recorte anterior e à suíte geral; as contagens não são somadas.

A conferência das fotografias históricas verificou que:

- O fechamento usa o lote completo selecionado e fotografias cujo número de
  ingestão é menor ou igual ao desse lote. Uma foto importada depois não altera
  a disponibilidade utilizada anteriormente.
- O lote selecionado possui a própria foto de fechamento. O código preserva
  os catálogos e as versões anteriores, inclusive após outra importação no
  mesmo dia com saldo diferente.
- O backfill ordena o legado por `created_at` e UUID decrescente, preservando
  o desempate anterior. Para novas importações da mesma organização, o lock
  transacional existente serializa a atribuição da ordem de ingestão.

Foi inspecionado diretamente o registro local da execução SQL anterior,
`/tmp/estoque-sql-final.log`: 320 mensagens de aprovação, incluindo 84 de
sincronização, sem `FALHOU`, `ERROR` ou `FATAL`. O registro inclui a reprodução
do saldo legado 10 com foto sobrescrita 99, recuperação após migração,
concorrência de vínculos e snapshots e repetição dos cenários após restauração.
Isso é conferência de evidência da execução principal em banco descartável,
não uma nova execução SQL por este agente nem validação do Supabase produtivo.

Após esse registro, a migração de vendas recebeu transação explícita e
notificação de recarga do schema. A cobertura passou a reunir somente
intervalos confirmados contínuos do mesmo escopo, preservando lacunas; foram
adicionados casos e um script de concorrência de sincronização. Essas mudanças
exigem os resultados da rodada final de PostgreSQL e CI, sem reaproveitar os
320 resultados anteriores como prova da nova versão.

Também foi conferido que a fachada analítica recusa fonte ou loja divergente
na resposta e exige booleano real na declaração de abrangência. A configuração
do leitor permanece protegida no servidor, e as telas não recebem rotas,
tokens ou mapas de campos do navegador. Nenhum comentário do PR #5 foi marcado
como resolvido por esta revisão.

## Segredos e limites da inspeção

Foi feita uma busca local por padrões de chave privada, token GitHub, chave
AWS, chave secreta Supabase e JWT literal nos 106 arquivos de código,
documentação e testes presentes naquele momento. Não houve ocorrências dos
padrões buscados. A saída mostrou somente contagem e nomes de arquivos; nunca
valores de segredos. `.env` e `.streamlit/secrets.toml` estão ignorados pelo Git.

Isso não prova ausência de todo segredo possível e não substitui scanner do
histórico Git, revisão dos Secrets do ambiente, logs externos e permissões das
contas. Tokens fictícios nos testes não são credenciais produtivas. Não foram
solicitados nem consultados valores privados de configuração.

## Bloqueios para ativação real

O diagnóstico da execução principal encontrou o projeto Supabase existente
com status `INACTIVE`. A validação produtiva, incluindo Security Advisors e
Performance Advisors, permanece pendente de reativação e acesso ao banco.
Ausência de alertas com o banco indisponível não comprova segurança.

Antes da ativação real, continuam necessários: reativar o projeto existente,
revisar e aplicar somente as migrações pendentes, conferir os advisors,
executar testes autenticados na URL verdadeira e validar o OpenAPI da conta,
campos, status, usuário, fonte, loja e cobertura. A primeira sincronização
completa exige autorização explícita e reconciliação de amostra. A presença de
testes fictícios aprovados não autoriza usar dados empresariais ou habilitar
sincronização automática.

## Atualização de disponibilidade conferida pelo agente principal

Depois da revisão inicial, o projeto passou a ACTIVE_HEALTHY. Leituras remotas
confirmaram quatro migrações originais, RLS nas 16 tabelas públicas e zero funções
públicas SECURITY DEFINER. Security Advisor retornou um WARN de proteção contra
senhas vazadas desativada (recurso Pro, nenhum custo autorizado); Performance
Advisor retornou 13 INFO de índices não usados, sem remoção automática. As novas
migrações, fluxos produtivos autenticados e auditoria posterior continuam pendentes.
Essas observações não foram usadas para declarar a implantação validada.
