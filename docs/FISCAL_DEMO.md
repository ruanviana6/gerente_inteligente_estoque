# Módulo fiscal DEMO e consolidação da interface

Escopo autorizado em 11/11/2026: Issues #20/#21 do Gerente e PR #1 da automação.
Aplicação única: https://estoqueinteligente-flatter.streamlit.app/.
Branch publicada preservada: `implementacao/web-supabase`.

## Arquitetura

Streamlit cuida da interface, sessão e autorização. PostgreSQL Supabase guarda
prévia, escolhas, solicitante/aprovador, estados, resultados e auditoria.
`supabase/functions/fiscal-demo` é o único executor operacional, Deno, acionado
sob demanda. Não há worker permanente, cron, Redis, outro serviço ou banco local.
Python da automação mantém regras, adaptadores bloqueados e referência de teste.

Edge Functions foram preferidas porque o DEMO realiza operações curtas de I/O,
sem treinar modelos nem processar arquivos. Os limites Free documentados são
150 s de wall time, 2 s de CPU e 256 MB. Cada chamada interna usa timeout de 15 s;
o máximo esperado de três chamadas cabe na concessão de 60 s. A adequação ao
ERP real depende de medições e do checklist, não do sucesso desta simulação.

Não se alteram Secrets atuais do Streamlit. A função usa as variáveis nativas
da plataforma Supabase. O JWT individual consulta Auth e chama `fiscal_begin`.
A credencial `service_role` fica apenas na Edge Function e conclui exclusivamente
`fiscal_worker_finish`, após nonce/prazo/estado/identidade validados. Não é usada
para leitura comum nem enviada ao navegador. O deploy exige `verify_jwt=true`.

## Dados, concorrência e falhas

Migrações aditivas:

- `20261111170000_fiscal_demo.sql`: configurações, prévias, solicitações, eventos,
  cinco permissões e RPCs protegidas.
- `20261111170500_dashboard_compacto.sql`: um resumo recomputável por organização
  e totais comerciais atualizados na publicação/reversão de resultados.

RLS habilitada; tabelas fiscais e caches não têm grants diretos para anon,
authenticated ou service_role. Wrappers públicos SECURITY INVOKER chamam funções
privadas de escopo restrito com search_path vazio. Organização vem do usuário
ativo, nunca do JSON da UI. Usuários nãoMaster começam sem permissões fiscais.

Permissões: `fiscal.view`, `fiscal.create_request`, `fiscal.approve_pending`,
`fiscal.audit` e `fiscal.manage`. A última é exclusiva Master e não delegável.
O operador vê seus pedidos; auditor vê a organização; aprovador vê pendências.
Não se reutiliza `finance.view` para conceder acesso fiscal. O resumo fiscal só
aparece com permissão e dados, sem destinatário, chave ou documento.

Prévia dura 15 minutos, é imutável e vinculada ao autor. Confirmar revalida a
impressão dos dados e as escolhas. Unicidade inclui organização, origem, ID
do pedido, destino e ambiente. Locks serializam cliques concorrentes. O diário
é registrado antes da concessão, não depois do resultado. Nenhum retry de POST.

Estados internos: awaiting_approval, confirmed, processing, authorized, rejected,
needs_reconciliation e blocked. Rótulos oficiais ficam em coluna separada,
apenas como resultados fictícios. Processamento interrompido/vencido exige
conferência; não volta a criar. Reconciliação DEMO é uma ação explícita e local.

Solicitante e aprovador têm sessão, conta ativa, primeiro acesso e permissões
revalidados no início e na conclusão. A validade do JWT original do solicitante
também limita a solicitação: se expirar antes de processar, o pedido é bloqueado.
Não há reautorização silenciosa para contornar isso; um fluxo futuro de nova
confirmação após reconciliação precisará ser revisado antes da API real.

Limites de proteção: 10 prévias novas/minuto/autor, 5 solicitações/minuto/autor,
10 mil prévias e 5 mil solicitações por organização. Prévias repetidas dentro da
validade são reutilizadas. Não há purga automática: ao atingir limite, o Master
precisa avaliar retenção e backup, sem apagar histórico para reemitir.

## Como usar a DEMO

O Master existente já compreende as novas permissões; não é recriado.
Pode conceder os quatro acessos delegáveis na Central de Acessos. Nenhum perfil
recebe acesso automaticamente. Na área Automação / Notas Fiscais:

1. Consultar 1001, escolher manualmente frete e contribuinte.
2. Gerar prévia, conferir pedido, responsável, produtos e valores fictícios.
3. Marcar a confirmação explícita e confirmar a solicitação DEMO.
4. Acompanhar resultado e histórico. Recarregar não gera outra nota.

| Pedido | Cenário fictício |
| --- | --- |
| 1001 | Autorizada |
| 1002 | Rejeitada, sem recriação automática |
| 1003 | Em andamento, exige aprovação Auth |
| 1004 | Outra UF, bloqueado |
| 1005 | Situação bloqueada |
| 1006 / 1010 | Nota existente / denegada, bloqueado |
| 1007 | Timeout na criação, identificador desconhecido |
| 1008 | Timeout na emissão, identificador preservado |
| 1009 | Cadastro incompleto, bloqueado |

Cada pedido DEMO admite apenas uma solicitação por organização. Não existe
botão de apagar/resetar para permitir emissão repetida. Use bancos descartáveis
nos testes repetitivos. Nenhum XML/PDF é gerado, baixado ou armazenado.

## Navegação e Visão Geral

Seis áreas operacionais: Visão Geral, Estoque, Reposição Inteligente,
Inteligência Comercial, Automação / Notas Fiscais e Relatórios.
Acompanhamento de Ações fica em Reposição. Clientes/Cidades/Produtos Juntos e
Vendas ficam em Inteligência Comercial. Administração reúne usuários, regras,
importação, integrações e auditoria conforme permissões, inclusive de nãoMaster.

A Visão Geral tem até seis KPIs, cinco prioridades e dois gráficos leves.
`dashboard_payload` lê somente resumo, metadados e totais pré-calculados.
Financeiro, vendas e reposição são removidos no banco quando não autorizados.
O resumo é um cache de até 32 KiB, substituído por organização, sem PII/SKUs.
Os detalhes permanecem nos módulos, sem reduzir o histórico analítico.

O Master usa **Atualizar indicadores** após importações ou mudança de parâmetros,
embalagens, ciclos e sincronizações. É um processo explícito fora da navegação;
mudanças na revisão sinalizam desatualização. Sem resumo, a tela explica a
pendência e não lê automaticamente todo o histórico. Origem não declarada DEMO
é mostrada como não classificada, sem supor que dados existentes são fictícios.

## Desempenho medido

Mesmo ambiente Python 3.12.14, pandas 2.3.3, NumPy 2.4.6, Streamlit 1.66.1,
AppTest, cinco repetições e transporte fictício com 20 ms por chamada.
Base: `0add0fbf073781de4b7fcec080a8b331ba865ef2`.

| Produtos | Antes p50/p95 | Depois p50/p95 | Cálculo explícito do resumo |
| --- | --- | --- | --- |
| 100 | 607,54 / 1.984,71 ms | 228,66 / 277,97 ms | 233,24 ms |
| 1.200 | 1.050,77 / 1.054,72 ms | 288,31 / 302,53 ms | 880,25 ms |

O cálculo não desapareceu: foi deslocado para o processo controlado. Para 1.200
produtos, a mediana caiu 72,6%. JSON bruto fictício: 1.625.087 bytes; resumo:
1.863 bytes, sem incluir o pequeno envelope/metadata. Isso não é uma medição
do tráfego real ou da pintura do navegador, nem garante a latência do Cloud.
Não foi feita compressão de histórico nem alteração das fórmulas do motor.

Reprodução: `scripts/benchmark_flatter.py --mode ui --sizes 100 1200
--iterations 5 --latency-ms 20 --output evidencias/depois.json`.
O CI mede novamente a base imutável e a candidata nas mesmas condições.

## Verificação e ativação

O registro final de testes, migrações, backup e URL fica em
`docs/CONSOLIDACAO_FISCAL_20261111.md`. Não usar documentos da V1 como
instruções de operação. A API real permanece bloqueada por construção.
Consulte [o checklist](CHECKLIST_ATIVACAO_API_GESTAOCLICK.md).
