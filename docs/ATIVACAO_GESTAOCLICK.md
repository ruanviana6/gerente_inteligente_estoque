# Ativação protegida da cópia analítica

O conector está desativado por padrão (`GESTAOCLICK_ENABLED=false`). A entrega
não autoriza consultas empresariais, sincronização automática, custos ou escrita
no GestãoClick. A configuração fica exclusivamente no servidor Streamlit.

## Recuperar o projeto existente

O MCP de gerenciamento confirmou `INACTIVE` em `csiqpzoudruzppfbpqqx`, São Paulo.
O proprietário deve abrir https://supabase.com/dashboard/project/csiqpzoudruzppfbpqqx,
selecionar **Resume project** e confirmar. A documentação oficial permite retomar
projetos pausados por até 90 dias. Não crie outro projeto, exclua tabelas ou
restaure um dump sobre os dados existentes. Se o painel não permitir retomar,
registre a mensagem e consulte o suporte antes de qualquer recuperação.

Depois de `ACTIVE_HEALTHY`, validar Auth, conexão, migrações registradas, RLS e
advisors de Security/Performance. Ausência de alertas com banco inacessível não
comprova segurança. A pausa por baixa atividade pode voltar no plano Free;
não foi contratado plano pago nem criado mecanismo artificial de atividade.

## Preparar banco e revisão

1. Revisar o PR e as novas migrações, com backup privado testado. Os quatro
   arquivos já aplicados não são alterados ou reexecutados.
2. Aplicar somente as migrações pendentes na ordem, após aprovação administrativa.
   O CI e os testes locais usam bancos descartáveis. Esta entrega não aplica SQL
   produtivo automaticamente.
3. A migração de snapshots acrescenta versões por lote; conserva tabelas e dados
   anteriores. O backfill recupera saldos de `inventory_products` imutáveis.
4. A migração de vendas acrescenta fontes, execuções, páginas, staging e versões.
   Não modifica catálogo, saldo ou relatórios mensais existentes. Para reverter
   o aplicativo, mantenha as tabelas e desligue a integração. Não faça `DROP` de
   dados para voltar código. Uma reversão de esquema exige migração corretiva.
5. Confirmar o Master por UUID conforme `CONFIGURACAO_WEB.md`, cadastro público
   desligado e segredo de backend configurado. Nenhum segredo deve ir ao chat.

## Conferir o contrato real

No GestãoClick, **Meus aplicativos → API → Documentação**, obter o OpenAPI da
conta autorizada. Conferir habilitação sem contratação não aprovada, endereço
HTTPS, GETs, campos, paginação, quotas e filtros disponíveis. Confirmar a conta,
loja e usuário vinculados à chave, permissões mínimas de leitura e eventuais
restrições de IP/horário. Um HTTP 200 não prova acesso a todo o catálogo.

Conferir status reais, IDs estáveis de documento/item/produto/loja, devoluções,
cancelamentos e data com fuso de alteração. `potential` nunca é venda concluída;
`separating` não é contado como venda concluída nesta análise; `completed`
considera quantidade líquida. Nunca efetuar segunda baixa entre estados.

Sem campo obrigatório, desabilitar a análise dependente. Não preencher dados
ausentes com suposições. Filtros de modificação só podem ser usados quando seu
significado estiver comprovado; a implementação inicial consulta períodos
explícitos e não trata ausência na resposta como exclusão ou cancelamento.

## Secrets e primeira carga

1. Registrar a fonte e suas lojas com uma sessão Master via
   `integration_register_source`; nunca enviar tokens nessa RPC.
2. Em **Streamlit → App settings → Secrets**, inserir o modelo de `.env.example`
   adaptado a TOML: URL, dois tokens, UUID da fonte e loja autorizada. Inserir
   também os JSONs de OpenAPI, campos, status e filtros descritos em
   `CONTRATO_GESTAOCLICK.md`. Não versionar o arquivo real nem fornecer valores
   por formulário do navegador.
3. Manter `GESTAOCLICK_ENABLED=false` até banco validado, correções aprovadas,
   autorização empresarial e contrato conferido. Não alterar os Secrets da
   aplicação publicada durante a revisão deste PR.
4. Depois dessas condições, autorizar explicitamente um GET de conectividade e
   uma amostra limitada. A tela **Integrações**, exclusiva Master, exige seleção
   explícita de consulta real e confirmação de autorização. O backend comprova
   fonte/loja/organização antes de executar o leitor.
5. Conferir prévia, quantidades e totais contra o ERP, inclusive registros que o
   usuário/chave pode não enxergar. Confirmar abrangência apenas após essa
   conferência. Confirmar a cópia interna é uma ação separada.
6. Autorizar expressamente a primeira carga completa. Dividir períodos para
   respeitar os limites. Não foi criado scheduler para GestãoClick.

## Operação e limites

Até 20 mil linhas por execução, páginas internas de cem, até dez fontes por
organização; versões e execuções têm limites conservadores no banco. Acompanhar
ocupação real do Free (500 MB), inclusive índices e histórico existente. Um
limite de linhas não garante ocupação em bytes. Não excluir história para liberar
espaço automaticamente. Exportar backup privado e discutir retenção antes do limite.

Repetir a chave de execução retoma checkpoints, conferindo conteúdo e sem duplicar
vendas. O leitor repete a extração completa e validada antes de retomar páginas
internas; não há garantia de snapshot transacional do ERP entre páginas. Mudança
de contagens causa recusa. Execuções interrompidas não se tornam dados analíticos.
Cancelamento de prévia preserva o consolidado. Atualizações incrementais não
substituem catálogo ou história completa. Versões de linhas ficam preservadas.

A visão atual de vendas reflete cancelamentos/devoluções mais recentes conhecidos;
não é um fechamento fiscal imutável. Os relatórios de estoque continuam vinculados
aos lotes próprios. Custos, receita e ticket não são inferidos das quantidades.

## Referências oficiais consultadas

- https://supabase.com/docs/guides/platform/free-project-pausing
- https://supabase.com/changelog.md (CLI v3 tem mudanças; CI mantém v2.122.0)
- https://gestaoclick.com.br/integracoes/api/
- https://central.gestaoclick.com.br/hc/pt-br/sections/132-api

A auditoria independente da Issue #3 após esta implementação continua necessária
antes do uso empresarial. Testes descartáveis não equivalem à validação produtiva.
