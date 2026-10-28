# Aplicativo único na web: acesso por usuário e permissões

> Decisão de requisitos de 28/10/2026. Este documento é o escopo de segurança para o Codex; **não significa que os controles já foram implementados ou testados**.

## Objetivo e arquitetura

Um único Gerente Inteligente de Estoque, acessível no navegador por computador ou celular, para uso pessoal no trabalho e sem serviços pagos. A primeira tela após o login mostra um flash rotativo de **insights calculados** pelo motor: riscos, reposição, tendências e produtos parados.

- Interface: Streamlit Community Cloud.
- Login: Supabase Auth por **e-mail e senha** (com nome de usuário de exibição). Não inventar autenticação própria por apelido e senha nem gravar hashes/senhas em tabelas caseiras.
- Banco persistente: Supabase PostgreSQL Free, com Row Level Security (RLS) e privilégios mínimos.
- Repositório GitHub: apenas código e documentação. Dados reais, banco, chaves e backups nunca podem ser publicados.
- GestãoClick: integração futura e inicialmente só leitura autorizada, após validação do aplicativo. Automação NF-e é outro projeto para integração posterior.

O endereço do app pode exibir publicamente apenas a página de login, com toda consulta e operação bloqueada até autenticação e autorização. Privacidade adicional no Streamlit Cloud pode ser utilizada, mas o convite de viewers na plataforma não substitui as permissões internas. Não utilizar SQLite efêmero do host como banco oficial.

## Identidade Master e cadastro

- Criar **uma conta Master inicial** em procedimento administrativo protegido, identificada pelo UUID imutável do Supabase Auth. Não usar senha padrão, role editável no navegador ou cadastro automático de Master.
- Cadastro público deve ficar DESATIVADO. Somente contas autorizadas são criadas por fluxo administrativo.
- Master acessa a tela Administração / Usuários para cadastrar, ativar, desativar contas, definir perfis, marcar permissões e consultar auditoria.
- Perfis básicos: Master, Administrador, Operador e Visualizador. Perfil define opções iniciais; o Master pode ajustar cada permissão individualmente. Ninguém se torna Master pela própria interface.
- O Master pode gerenciar acesso a qualquer módulo, mas operações críticas permanecem auditáveis. Evitar conceder gerenciamento de Master ou configuração de credenciais.
- Criar usuário via API administrativa somente no servidor; chaves administrativas nunca vão para o navegador nem são usadas indiscriminadamente nas consultas normais.
- Recuperação e criação de senhas devem ser seguras, com troca de senha e tratamento correto das sessões. Atenção: o SMTP padrão do Supabase tem envio restrito aos endereços autorizados da equipe. Convites e recuperação por e-mail para outras pessoas exigem SMTP próprio ou outro fluxo seguro e validado. Não prometer que o envio está pronto ou automaticamente gratuito.

## Permissões por função

Padrão: **tudo negado até liberação explícita**. As permissões são verificadas no frontend e novamente no backend/banco. Ocultar botões não é um controle de segurança.

| Permissão | Funcionalidade |
| --- | --- |
| dashboard.view | Ver painel e flash de insights |
| inventory.view | Consultar saldos, produtos e histórico |
| replenishment.view | Consultar recomendações de reposição |
| replenishment.export | Exportar lista de compras |
| finance.view | Consultar custos e indicadores financeiros |
| trends.view | Consultar tendência e ABC |
| inventory.import | Importar ou atualizar dados de estoque/vendas |
| parameters.edit | Editar lead time, estoque de segurança e parâmetros |
| decisions.write | Registrar avaliações e decisões |
| audit.view | Ver trilha de auditoria |
| users.manage | Criar e desativar usuários autorizados |
| permissions.manage | Conceder ou retirar permissões |
| integrations.manage | Futuro controle de sincronização com GestãoClick |

Exemplos: um Operador pode consultar reposições sem enxergar custos ou exportar. Um Visualizador pode ter acesso apenas ao painel. Master controla concessões e revogações.

## Persistência e proteção técnica

- Supabase Auth gerencia senhas e tokens; nunca armazenar senhas no SQLite, Postgres de aplicação, Git ou logs.
- Estruturar tabelas de perfis, catálogo de permissões, permissões de perfis, concessões individuais e eventos de auditoria. Cada conta usa o UUID do Supabase Auth.
- Aplicar **RLS em todas as tabelas expostas** e grants mínimos para anon e authenticated. Nenhum dado interno fica acessível anonimamente.
- Usar identidade do usuário nas consultas e políticas do banco. A service key administrativa ignora RLS e só pode participar de ações administrativas pontuais com checagem de permissão.
- A desativação deve impedir NOVAS consultas/operações, mesmo com sessão antiga, com checagem de status/permissão atual no servidor/banco; planejar expiração e revogação de tokens.
- Não compartilhar tokens, sessões, caches ou resultados entre usuários do Streamlit. Testar duas sessões simultâneas.
- Registrar alterações relevantes com autor, data, função, alvo e resultado; nunca registrar segredos.
- Implementar logout, expiração/renovação segura de sessão, senhas fortes, limitação de tentativas, mensagens de erro genéricas, fluxos seguros de recuperação e testes de permissão negada.
- Guardar segredos apenas em Streamlit Secrets ou serviço equivalente. Não enviar credenciais em chat.
- Não hospedar informações reais da empresa sem autorização e avaliação de segurança/LGPD.

## Custos e limitações

Objetivo de custo zero dentro dos planos gratuitos. O Supabase Free oferece 500 MB e pode pausar por baixa atividade; também não possui os mesmos backups automáticos dos planos pagos. Projetar backups exportáveis e testes de restauração. Não prometer SLA, disponibilidade 24/7 ou recuperação de senha via e-mail antes de configurar o envio.

## Relatório mensal automático por e-mail (etapa futura)

Foi aprovado incluir no planejamento um **relatório mensal por e-mail** para
usuários autorizados, com envio padrão no dia 1 de cada mês e números do mês
anterior. O usuário Master decide quem recebe, o que pode ser incluído e se a
rotina está ativa. O relatório deve conter, conforme dados e permissões,
mais vendidos, volume vendido, variações, parados, rupturas, tendências e
recomendações. Custos e indicadores financeiros exigem `finance.view`.

Permissões adicionais: `reports.view`, `reports.export`, `reports.receive` e
`reports.manage`. O usuário só recebe conteúdo autorizado no momento da
geração **e novamente antes do envio**. Apenas o Master pode configurar
destinatários por padrão. Não confundir e-mail de login com SMTP de envio.

A rotina deve rodar fora do Streamlit e ter histórico, registro de falhas e
proteção contra duplicidade. Prioridade de custo zero, sujeita à verificação
dos limites de agendamento e da exigência de domínio/remetente do provedor.
**Escopo detalhado:** `docs/RELATORIOS_EMAIL.md`. Nada disso está ativo.
## Sequência para o Codex

1. Auditoria do MVP e execução efetiva dos testes existentes.
2. PostgreSQL, migrações, contratos e RLS; testes de segurança de dados.
3. Supabase Auth por e-mail/senha e bootstrap seguro do Master, sem cadastro público.
4. Painel Master com gestão de usuários e permissões por função.
5. Migração de todo fluxo operacional para o banco persistente e autorização por operação.
6. Flash de insights explicáveis na página inicial.
7. Testes de login, acesso negado, revogação, sessão expirada, isolamento de usuários e dados fictícios.
8. Publicação única no Streamlit Cloud. Dados empresariais somente com autorização.
9. Relatório mensal por e-mail somente depois de login, permissões, banco persistente, dados mensais validados e segurança; seguir `docs/RELATORIOS_EMAIL.md`.
10. Integração GestãoClick após a conclusão e validação. NF-e continua planejado para depois.

O estado atual da branch main ainda é um MVP local com SQLite sem login. A branch experimental feat/demo-publica-streamlit foi criada antes da decisão de aplicação única: não mesclar automaticamente.

## Documentação oficial

- https://supabase.com/docs/guides/auth/passwords
- https://supabase.com/docs/guides/auth/general-configuration
- https://supabase.com/docs/reference/python/auth-admin-createuser
- https://supabase.com/docs/guides/auth/auth-smtp
- https://supabase.com/docs/guides/api/custom-claims-and-role-based-access-control-rbac
- https://supabase.com/docs/guides/database/postgres/row-level-security
- https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app
- https://docs.streamlit.io/deploy/concepts/secrets
