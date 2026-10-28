# Plano de continuidade: aplicativo único na web com acesso por usuário

## Decisão de produto (28/10/2026)

O usuário quer **uma única aplicação oficial acessível pela web**, tanto no
computador quanto no celular. **Não** quer manter uma instalação local de uso
paralela a uma demo pública. O software permanece uma ferramenta pessoal,
gratuita, não comercial, para apoiar decisões de estoque no trabalho.

A página principal terá um **flash/carrossel de insights explicáveis** baseado
nos indicadores reais do motor: falta antes da entrega, reposição, crescimento
e estoque parado/excesso. Não são textos inventados por um modelo generativo.

## Arquitetura-alvo de custo zero

- **GitHub:** código e documentação. O repositório pode continuar público,
  mas NENHUM arquivo comercial, senha ou banco real deve ser versionado.
- **Streamlit Community Cloud:** aplicativo único. Configurar privacidade
  **Only specific people can view this app** e convidar usuários autorizados.
- **Autenticação na aplicação:** para controle efetivo por usuário/roles,
  usar OIDC via `st.login()` com provedor verificado (ex.: Google) e uma
  allowlist de identidades autorizadas, ou mecanismo equivalente com
  autenticação e autorização reais. Não confiar apenas em `st.user` sem
  configuração OIDC. O acesso aos dados deve ser verificado em cada operação.
- **Supabase Postgres (Free):** persistência dos produtos, vendas importadas,
  snapshots, configurações e histórico de decisões. Conectar somente no
  servidor com credenciais em Secrets do Streamlit. Não usar SQLite no
  Community Cloud como banco oficial, pois armazenamento local não é
  persistente.
- **Backend:** manter Python/Streamlit e módulos separados. Não adicionar
  React/FastAPI/microsserviços sem necessidade.
- **GestãoClick:** integração oficial SOMENTE após concluir e validar o
  aplicativo web, começando com acesso de leitura autorizado.
- **Automação NF-e:** permanece um sistema separado, integração futura.

## Acesso por usuário: questões a considerar

1. O Streamlit Cloud permite app privada com convidados identificados por
   e-mail, mas **viewers podem convidar outras pessoas** no workspace.
   Para restrições administradas exclusivamente pelo responsável, adotar
   autorização própria por identidade/role, além da privacidade da plataforma.
2. Login via OIDC exige criar/configurar um cliente junto ao provedor, colocar
   `client_id`, `client_secret`, `redirect_uri` e `cookie_secret`
   nos Secrets, nunca no Git. A aplicação deve negar acesso por padrão
   se faltarem configurações.
3. Não expor dados em consultas, uploads, caches ou logs antes de autenticar;
   as consultas devem ter filtros/autorização adequados.
4. Definir permissões simples: administrador (gerenciar acessos e parâmetros)
   e usuário (consultar/analisar). Permissões de escrita devem ser explícitas.
5. Autenticação é diferente de autorização. Um login bem-sucedido não implica
   automaticamente direito de consultar dados internos.
6. O Streamlit Community Cloud permite uma aplicação privada por vez no
   plano gratuito, e há limites operacionais. O Supabase Free pode pausar por
   inatividade. Não prometer disponibilidade 24 horas nem backup empresarial.
7. Dados reais da empresa no serviço cloud exigem autorização expressa,
   avaliação de segurança e LGPD quando aplicável.

## Fluxo de execução previsto

Navegador → página de login → verificação de identidade e autorização →
Streamlit → motor de cálculo → PostgreSQL Supabase.

A entrada da aplicação deve bloquear usuários não autorizados **antes**
de montar painéis e abrir conexões que consultem dados empresariais.

## Etapas de implementação para o Codex

1. Auditoria do código atual e execução real dos testes. Corrigir erros
   antes de construir autenticação ou migrar dados.
2. Refatorar a persistência para um repositório abstrato, sem mudar as
   fórmulas de estoque. Criar backend PostgreSQL com migrações/constraints,
   testes transacionais e scripts de criação. Migrar o uso atual de SQLite.
3. Criar autenticação OIDC e autorização por allowlist/roles, com testes de
   acesso negado. Manter segredos em Streamlit Secrets.
4. Criar página inicial com flash de insights usando indicadores calculados.
   Não mostrar dados de outros usuários sem permissão.
5. Preparar configuração de deploy e instruções do Streamlit Cloud.
6. Executar testes de fluxo completo com dados fictícios no ambiente web.
7. Somente após as verificações, configurar acesso privado na conta do
   Streamlit e conectar o Supabase autorizado.
8. Só depois, estudar importações de dados comerciais e a API GestãoClick.

## Ações manuais necessárias do dono da conta

- Criar projeto **Supabase Free** e obter dados da conexão; inserir segredos
  diretamente no painel do Streamlit, nunca compartilhar chaves no chat ou
  colocar no GitHub.
- Definir o provedor de login e configurar seu cliente OAuth/OIDC e URI de
  retorno para o endereço final `*.streamlit.app`, se for usado login interno.
- Configurar privacidade do Streamlit para convidados selecionados.
- Informar/gerenciar os e-mails autorizados na configuração privada do
  sistema, sem precisar divulgá-los no repositório público.
- Autorizar qualquer armazenamento de dados reais da empresa em cloud.

## Limites do estado atual

**O repositório ainda contém a implementação local com SQLite e não possui
autenticação/controle de acesso próprio**, portanto o app atual NÃO deve ser
publicado como sistema interno de dados reais. Este documento é um plano,
não comprova que a migração ou o deploy já foram feitos.

A branch experimental `feat/demo-publica-streamlit` foi iniciada antes
desta decisão, apenas com uma proposta inicial de insights. **Não mesclar
automaticamente essa branch**, porque propunha um aplicativo público de demo
separado, contrário à decisão mais recente.

## Fontes oficiais

- https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app
- https://docs.streamlit.io/develop/concepts/connections/authentication
- https://docs.streamlit.io/develop/api-reference/user/st.user
- https://docs.streamlit.io/develop/concepts/connections/connecting-to-data
- https://supabase.com/docs/guides/platform/free-project-pausing
