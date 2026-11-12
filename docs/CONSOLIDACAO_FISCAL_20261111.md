# Consolidação fiscal DEMO Flatter

Registro atualizado em 12/11/2026, horário de Fortaleza. Escopo: Issues #20/#21,
PR #22 do Gerente e PR #1 da automação. O proprietário autorizou migrações
aditivas com backup, merges e publicação DEMO. Nenhuma API real foi ativada.

## Resultado implementado

A interface reúne seis áreas operacionais e Administração conforme permissões.
Visão Geral lê somente resumo compacto pré-calculado, com até seis KPIs,
cinco prioridades e dois gráficos. Não carrega histórico bruto, treina modelos
ou consulta ERP durante navegação. Estoque, reposição, vendas, ML, relatórios,
exportações e login persistente foram preservados.

A área Automação / Notas Fiscais tem pesquisa de pedido fictício, conferência,
duas escolhas manuais sem padrão, prévia imutável, confirmação, aprovação Auth
de Em andamento, solicitação única, resultado e histórico. Isento (2) é diferente
de não contribuinte (9). UF, situação inválida, cadastro incompleto e nota
existente impedem nova criação. Não há download ou armazenamento de XML/PDF.

Supabase é a única base operacional. A Edge Function `fiscal-demo`, versão 1,
está ACTIVE e com `verify_jwt=true`. Recebe somente UUID/ação DEMO. Verifica
Auth, chama o início com JWT individual e conclui uma concessão temporária pela
RPC restrita do executor. A chave nativa da plataforma nunca é enviada ao
navegador. Não foram alterados Secrets do Streamlit nem criados serviços pagos.

O repositório Python fornece regras, adaptadores bloqueados, contrato e testes.
Seu PR #1 foi integrado no commit
[`a3ca787`](https://github.com/ruanviana6/automacao_nf-e/commit/a3ca787d7354565abd5269a6230977c0ea9511d1).
O diário de referência precede criação, trata estados incertos e bloqueia
recriação. CLI operacional antigo, cliente HTTP genérico e limpeza automática
ficaram desativados. Documentos antigos estão identificados em `docs/historico/`.

## Verificações executadas

| Verificação | Resultado e alcance |
| --- | --- |
| Python do Gerente | 925 testes e 66 subtestes aprovados, sem skips |
| Ruff | Aprovado em todos os arquivos do Gerente |
| Python da automação | 64 testes aprovados, conexão de rede bloqueada na suíte |
| Deno 2.6.5 | `check` da entrada e 8 testes adversariais com checagem de tipos aprovados |
| Interface principal | 12 telas em Chromium, larguras 1.440/390 px, transporte fictício |
| Fluxos da interface | Download CSV, quatro perfis, logout isolado, revogação financeira, exportação negada e desativação aprovados |
| Interface fiscal | Quatro telas em 1.440/390 px, seleções vazias, prévia, confirmação e resultado DEMO aprovados |
| PostgreSQL 17 no CI anterior | As duas migrações aplicadas são idênticas às do commit `7b2cd62`; 50 verificações fiscais aprovadas antes e depois de pg_dump/pg_restore |

As verificações fiscais PostgreSQL incluem acesso anônimo/direto negado,
isolamento entre organizações e solicitantes, JWT expirado, inatividade,
aprovação forjada, revogação do aprovador, nonce inválido/reutilizado,
andamento pendente, códigos inválidos, repetição, rejeição, timeout de criação
e emissão e reconciliação explícita. Foram conferidas diretamente as linhas
do [job PostgreSQL](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/43067725029/job/130687040357).

As medições locais usaram Python 3.12.14, pandas 2.3.3, NumPy 2.4.6,
Streamlit 1.66.1, cryptography 50.0.0 e scikit-learn 1.8.0 disponível no ambiente.
O CI fixa scikit-learn 1.9.1; não se declara que esse pin foi executado localmente.
Os testes novos de Auth/PostgREST/Edge reais e concorrência/dashboard SQL estão
no pipeline atual, mas sua execução remota continua pendente.

## GitHub Actions: pendência externa

Os workflows do commit `0ecf0ba` ficaram em estado `queued`, com zero jobs,
desde 11/11/2026 às 17h24 de Fortaleza. Isso não é teste aprovado nem falha
diagnosticada de código. A correção de tipagem e o navegador foram executados
localmente enquanto a fila não avançou. Não foi desabilitado teste ou type check.

- [Testes Python/Supabase](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/43095890989).
- [Interface e desempenho](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/43095890961).
- [Automação Python](https://github.com/ruanviana6/automacao_nf-e/actions/runs/43095918093).

O ambiente local negou acesso ao socket Docker e não possui PostgreSQL CLI.
Não se alega nova execução local dos testes de Auth/PostgREST reais por isso.
O [registro anterior](INTEGRACAO_WEB_20261111.md) contém a validação Auth real
da versão anterior, que não substitui os testes novos ainda pendentes.

## Banco, backup e preservação

Aplicadas e alinhadas aos arquivos versionados:

- `20261111170000_fiscal_demo.sql`.
- `20261111170500_dashboard_compacto.sql`.

Antes das migrações, em 12/11, foi capturado um backup lógico consistente de
38 tabelas, 1.015 registros e dez migrações. Ele permanece fora do Git,
cifrado com AES-256-GCM, diretório 0700 e arquivos/chave 0600. A leitura de
todas as partes cifradas foi verificada e a reconstrução tipada JSON→registro
foi conferida para todas as tabelas. SHA256 do conteúdo:
`e0c209112e3c49150d5f4e12fe5a830844cbfe6e48760cd9cf72ff7e2ccd2d44`.

Esse backup cobre a camada da aplicação e migrações, não é um export integral
de Auth, Storage ou Secrets. A restauração integral com pg_dump/pg_restore foi
testada no PostgreSQL descartável, com dados fictícios.

Após as migrações, 37 tabelas antigas permaneceram idênticas. A única alteração
na comparação foi `permissions`, com cinco novas permissões. O fingerprint das
credenciais da conta Master permaneceu igual. Nenhum usuário foi recriado.

O resumo inicial foi calculado pelo motor Python em processo administrativo
controlado e inserido somente com organização, Master, lote e revisão coerentes.
O evento `dashboard.initialization` identifica essa inicialização autorizada;
não representa login ou clique do Master. Não foi fabricada sessão Auth.
A origem CSV existente foi mantida como não classificada, sem rotulá-la DEMO
por suposição. Próximas atualizações usam a ação explícita do Master.

## Segurança e limites restantes

O advisor já apontava proteção contra senhas vazadas desativada e tabelas com
RLS sem políticas. Nessas tabelas, o acesso direto é negado e as operações
passam por RPCs específicas, conforme o modelo existente. Não foram concedidas
políticas permissivas para eliminar avisos. Não se contratou recurso pago de
[proteção adicional de senhas](https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection).

Testes fictícios não homologam API, tributação, latência do ERP nem emissão real.
Uma solicitação que expire antes da execução fica bloqueada; nova confirmação
com reconciliação é uma evolução a revisar antes da integração real. A amostra
DEMO não é um gerador ilimitado: cada pedido só admite uma solicitação por
organização. Não apagar histórico para repetir emissão.

## Publicação e conferência autenticada

Branch de publicação: `implementacao/web-supabase`. URL:
https://estoqueinteligente-flatter.streamlit.app/.
Release esperada: `fiscal-demo-20261111.1`.

A conferência da nova release no domínio real será registrada após o merge.
Não se considera HTTP 200 ou a existência do PR prova de publicação. A validação
autenticada produtiva exige uma sessão legítima do proprietário, sem compartilhar
senha. O backend e o navegador fictício têm resultados separados acima.

Os resumos das evidências estão em `docs/evidencias/fiscal-demo-20261112.json`.
O comparativo completo e as condições de medição estão em [FISCAL_DEMO.md](FISCAL_DEMO.md).
O procedimento restante para a API está em
[CHECKLIST_ATIVACAO_API_GESTAOCLICK.md](CHECKLIST_ATIVACAO_API_GESTAOCLICK.md).
