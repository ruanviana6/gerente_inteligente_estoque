# Validação do cadastro fiscal por loja

Registro de 12/11/2026, horário de Fortaleza. A implementação teve apoio de IA
e Vibe Coding, com execução direta das verificações descritas abaixo.

## Regra e alterações

O [PR #23 do Gerente](https://github.com/ruanviana6/gerente_inteligente_estoque/pull/23)
substitui a política anterior pela regra atual do proprietário: **uma API com
lojas distintas**. FLT Comércio é a origem de consulta e Flagship - Flatter é
o destino exclusivo do cadastro da NF-e. A automação nunca emite a nota nem
cria venda ou pedido no varejo. A emissão será sempre manual no GestãoClick.

O [PR #2 da automação](https://github.com/ruanviana6/automacao_nf-e/pull/2)
foi integrado no commit
[`a38bea6`](https://github.com/ruanviana6/automacao_nf-e/commit/a38bea6e368958ce0477c062ba7835b6cf5dfced).
Ele compartilha o contrato v2 com o Gerente, bloqueia a emissão mesmo com as
flags antigas e mantém a referência Python separada do executor operacional.

A aba **Automação / Notas Fiscais** reúne pesquisa DEMO, seleção manual de
frete e contribuinte, prévia, confirmação, aprovação identificada e histórico.
Os dois campos são enviados juntos em formulário; não têm valor inicial.
Depois da prévia são exibidos como dados imutáveis do banco. A ação de editar
descarta o aceite e exige outra prévia. Isso corrigiu reinicialização de widgets
ao alternar `disabled` e fechamento do seletor durante rerenders.

O teste Chromium passou a abrir o seletor por clique real, em vez de depender
do estado de foco e de uma seta. Falhas preservam captura e árvore acessível
somente da fixture fictícia. Não há clique forçado, retry que esconda a falha,
remoção de teste ou desligamento da checagem de tipos.

## Testes verificados

| Verificação | Evidência e limite |
| --- | --- |
| Gerente em Python 3.11 e 3.12 | 925 testes e 66 subtestes, Ruff e `pip check` aprovados no [CI](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/43298913188) |
| PostgreSQL 17 | Migrações, autorização, RLS, histórico, reposição, comercial, sessões, fiscal e dashboard aprovados no mesmo CI |
| Restauração | `pg_dump`/`pg_restore` e nova execução das suítes SQL em banco descartável, com dados fictícios |
| Concorrência fiscal | Duas conexões reais preservam uma solicitação e uma única concessão de execução |
| Supabase descartável | 9 testes aprovados com Auth, PostgREST e Edge reais, sem credenciais produtivas |
| Executor Deno | Checagem de tipos da entrada e 8 testes adversariais aprovados |
| Automação Python | 73 testes e auditoria de dependências aprovados no [CI](https://github.com/ruanviana6/automacao_nf-e/actions/runs/43292881940) |
| Login persistente | 7 fluxos Chromium aprovados no [CI de interface](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/43298913319): F5, reabertura, outro navegador isolado, Sair, desativação, opção desmarcada e F5 no celular |
| Interface fiscal após correção do seletor | 4 telas aprovadas localmente e no mesmo CI, 1.440/390 px; escolhas vazias, prévia sem edição, confirmação bloqueada antes do aceite e um cadastro DEMO após confirmação |

Uma rodada anterior teve uma falha fiscal de foco no Chromium do CI,
investigada e corrigida. A nova rodada citada acima passou por completo.
O histórico das execuções fica disponível em
[Checks do PR #23](https://github.com/ruanviana6/gerente_inteligente_estoque/pull/23/checks).
Nenhum resultado local foi usado para substituir uma falha desse CI.

Os testes negativos cobrem acesso anônimo/direto, usuário de outra organização,
usuário sem permissão, sessão expirada, desativação, custo protegido, aprovação
forjada, revogação do aprovador, corpo HTTP excedente, códigos fiscais inválidos,
repetição, nonce inválido/reutilizado e legado sem escopo novo. Timeout antes ou
depois do cadastro exige reconciliação, sem nova tentativa automática de POST.

O ambiente local usou scikit-learn 1.8.0 e cryptography 50.0.0 já disponíveis;
o CI executou os pins 1.9.1 e 50.0.2 do projeto. Não se confunde essa diferença
com validação dos modelos em dados empresariais, que continuam não utilizados.

## Desempenho

Medição repetível do [CI](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/43295332269),
base `0add0fbf073781de4b7fcec080a8b331ba865ef2`, cinco repetições no mesmo
runner, AppTest e transporte fictício de **40 ms por chamada**:

| Produtos | Antes p50 / p95 | Depois p50 / p95 | Preparação fora da navegação |
| --- | --- | --- | --- |
| 100 | 294,18 / 320,51 ms | 254,09 / 256,67 ms | 77,85 ms |
| 1.200 | 610,66 / 675,31 ms | 253,68 / 253,96 ms | 424,55 ms |

A mediana caiu 58,5% no cenário maior. Visão Geral consulta o resumo compacto
e não carrega o histórico bruto nem executa o motor. O custo do cálculo não
desapareceu: está na preparação controlada. A medição não inclui rede real,
latência do ERP, Streamlit Cloud ou pintura do navegador. Os números de 20 ms
da primeira DEMO em FISCAL_DEMO.md são outra medição, não a mesma série.

## Migração, backup e executor reais

As 13 migrações do projeto estão aplicadas ao Supabase existente. A nova é
`20261112192003_cadastro_fiscal_por_loja.sql`, aditiva, sem exclusão de dados.
Registros legados ficam com escopo novo nulo e não são reexecutados. A chave
nova inclui organização, conexão, loja de origem, pedido, destino e ambiente.

Antes da terceira migração fiscal foram capturadas **44 tabelas, 1.024 registros
e 12 migrações** em backup lógico cifrado AES-256-GCM, fora do Git. Todas as
partes foram decifradas e a reconstrução tipada JSON para registro foi conferida.
SHA256 do conteúdo preservado:
`269200839bd6b9755628dbb549bb8da7696c7835b51646c4618e18de91e0e544`.
O hash das linhas das 44 tabelas e o fingerprint das credenciais Master
permaneceram iguais após a migração. Esse backup não exporta integralmente
Auth, Storage ou Secrets; a restauração integral descartável é teste separado.

A função **fiscal-demo v2** está ACTIVE, com `verify_jwt=true` e contrato
`operation=register_only`, `automatic_issuance=false`. A API real continua
bloqueada. Não houve troca de Secrets nem recriação de usuário. As consultas
comuns continuam com JWT individual. A credencial nativa do executor só conclui
a RPC restrita e revalida identidade, sessão, permissão e concessão.

Verificação direta: nenhuma tabela exposta sem RLS; nenhum grant direto de
anon/authenticated nas quatro tabelas fiscais. Os avisos informativos de RLS
sem política correspondem ao modelo RPC com acesso direto negado. A proteção
adicional contra senhas vazadas continua desativada, como antes; não foi
contratado plano pago para eliminar esse aviso.

## Publicação e ativação

O destino permanece https://estoqueinteligente-flatter.streamlit.app/ e a
branch **implementacao/web-supabase**. A release esperada é
**cadastro-nfe-lojas-20261112.1**. O relatório anterior e um HTTP 200 não provam
que essa versão chegou ao site. A última observação antes do merge desta revisão
ainda mostrava `web-integrada-20261111.2`. Não há sessão proprietária do Cloud
nem login Master produtivo disponível ao agente; não foram fabricados.

Depois do merge e da atualização do Cloud, a validação externa deve conferir
a nova release, login anônimo sem dados e negação de credencial fictícia.
O proprietário confere legitimamente a aba fiscal com pedido 1001 DEMO e o
login após F5/reabertura, sem enviar senha, cookie ou JWT. A opção de manter
acesso vem marcada e vale até 30 dias; Sair, desativação e revogação bloqueiam
a retomada. Navegação anônima ou limpeza de cookies não preservam esse acesso.

A API real depende de OpenAPI autorizado, IDs explícitos das lojas, mapeamento
fiscal, prova de cadastro direto sem venda/pedido/autoemissão e homologação dos
efeitos colaterais. Não basta preencher tokens. Use o
[checklist de ativação](CHECKLIST_ATIVACAO_API_GESTAOCLICK.md).

Solicitações cuja sessão original expire antes da execução ficam bloqueadas.
A reconfirmação explícita precisa ser homologada antes de operar a API real,
preservando idempotência e histórico. Não apagar dados para contornar bloqueios.
Esta entrega não autoriza API real, emissão, alterações adicionais no ERP,
serviço pago ou limpeza automática de dados.
