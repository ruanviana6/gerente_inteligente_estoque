# Ativação da aplicação web

O aplicativo usa exclusivamente o projeto `csiqpzoudruzppfbpqqx`, região `sa-east-1`.
Não crie outro projeto. A estrutura é versionada em `supabase/migrations/`.
Não copie dados empresariais para a validação inicial. Este guia não declara publicação concluída.

## 1. Configurar Supabase Auth

No painel do projeto existente, em **Authentication → Sign In / Providers**:

1. Mantenha o provedor de e-mail habilitado.
2. Desative **Allow new users to sign up** e logins anônimos. O código não possui cadastro público; esta opção também bloqueia chamadas diretas ao Auth.
3. Configure a política de senhas. O aplicativo exige 12 a 128 caracteres com letras, números e símbolos na criação e troca.
4. Em **URL Configuration**, informe a URL HTTPS definitiva do Streamlit quando ela existir. Não use redirects genéricos ou curingas desnecessários.

O banco nega acesso a qualquer identidade sem perfil ativo, mesmo que o cadastro público seja ativado por engano. Isso não dispensa desligar o cadastro no Auth.

## 2. Ativar a conta Master

Este procedimento exige o proprietário no painel do Supabase. Não envie senha, chave ou token no chat.

1. Em **Authentication → Users → Add user → Create new user**, crie sua conta com seu e-mail e uma senha forte e exclusiva. Use confirmação administrativa do e-mail somente para a conta cuja titularidade você controla.
2. Copie o **UUID** da conta criada. O UUID identifica a conta; o nome e metadados enviados pelo navegador não concedem privilégios.
3. No **SQL Editor**, conectado como `postgres`, execute substituindo apenas UUID e nome:

```sql
select private.bootstrap_master(
  'UUID_DA_SUA_CONTA_AUTH'::uuid,
  'Seu nome',
  'Meu estoque'
);
```

A função exige conta confirmada, cria a organização e registra auditoria. Recusa uma segunda inicialização e não pode ser chamada por `anon`, `authenticated` ou `service_role`. Não há senha padrão, promoção por formulário nem atribuição por `user_metadata`.

Depois do primeiro login, a área **Administração** permite autorizar contas por UUID, ativar/desativar, escolher perfil e sobrescrever permissões individuais. Operações de gestão permanecem exclusivas do Master. A alteração de acesso vale nas consultas seguintes, inclusive para tokens antigos.

O Visualizador começa somente com o painel. O Operador recebe painel, estoque, reposição, tendências, decisões e visualização de relatórios. O Administrador recebe as funções operacionais, inclusive finanças e importação, mas não gerencia usuários, permissões ou destinatários. Em cada funcionalidade, o Master pode escolher **Herdar do perfil**, **Permitir** ou **Negar**, sem alterar as demais concessões ao editar o nome da conta.

Para criar novas identidades dentro do app, há uma opção de criação via Auth Admin API. Ela exige a chave administrativa opcional nos Secrets e uma sessão Master validada. A chave não participa das consultas de estoque. Se preferir não armazená-la no Streamlit, crie identidades no painel Supabase e autorize seus UUIDs na aplicação.

## 3. Publicar no Streamlit Community Cloud

1. Acesse `https://share.streamlit.io` com a conta autorizada ao GitHub.
2. Escolha **Create app / Deploy an app**, repositório `ruanviana6/gerente_inteligente_estoque`, branch `implementacao/web-supabase` e arquivo `app.py`.
3. Em **Advanced settings**, selecione Python 3.12 e preencha **Secrets** com o modelo de `.streamlit/secrets.example.toml`. Copie a chave **publishable** do projeto no painel Supabase. Chave `anon` legada também é aceita; chave administrativa é recusada como chave comum.
4. Faça o deploy. Use uma única aplicação; não publique a fixture de testes nem o antigo MVP SQLite.
5. Acesse a URL HTTPS gerada e faça login com a conta Master. Uma sessão sem login deve mostrar apenas o formulário de autenticação. Sem Secrets, o app mostra instrução de configuração e não consulta dados.
6. Se desejar restringir também quem abre o endereço, configure os viewers da plataforma. Essa restrição é adicional à autorização do app e à RLS.

Não há conta/conector de Streamlit Cloud disponível nesta execução. O proprietário precisa realizar esses passos e fornecer a URL para a verificação remota posterior. Um teste HTTP local não comprova publicação.

## 4. Validar com dados fictícios

No app, entre como Master e abra **Importação → Carregar dados fictícios para validação**. Confirme a carga. Para conferir relatórios e comparação entre meses, importe primeiro uma demonstração no último dia do mês anterior ao relatório, depois no último dia do mês desejado e por fim na data atual. Cada carga gera um lote separado. Importar um fechamento antigo não substitui um saldo operacional mais recente.

Crie também contas fictícias Operador e Visualizador. Confirme negações de importação/exportação, ausência de custos sem `finance.view`, bloqueio após desativação e logout. Valide a URL em computador e celular. A auditoria independente da Issue #3 deve ocorrer antes do uso com dados reais.

## 5. Recuperação de senha

O SMTP padrão do Supabase pode limitar destinatários. Para recuperação externa, configure um remetente autorizado e SMTP compatível no painel. Não contrate serviço pago sem aprovação.

O fluxo implementado utiliza **código digitado**, sem tokens na URL. No template de recuperação do Supabase, inclua `{{ .Token }}` e oriente o usuário a digitar esse código em **Recuperar acesso**. Só então habilite `AUTH_EMAIL_ENABLED = true` nos Secrets. O app valida o código com o Auth, altera a senha e encerra a sessão. A criação administrativa de contas não envia convite automaticamente.

## 6. Migrar ou reverter com segurança

As migrações já aplicadas não devem ser editadas ou reexecutadas. Consulte o histórico no Supabase e aplique somente migrações pendentes, mantendo os arquivos versionados. A CLI usada no CI é `supabase@2.122.0`; consulte `--help` antes de comandos de implantação.

Uma falha no app pode ser revertida para um commit anterior **da versão web autenticada**, mantendo o banco. Não publique novamente o MVP sem login. Para uma interrupção emergencial, desligue o app e o agendador; não remova tabelas nem usuários. Mudanças destrutivas exigem backup validado e uma migração corretiva própria.

## 7. Backup

SQLite não é o banco da web. Use `pg_dump` do PostgreSQL compatível, com conexão obtida no painel Supabase e senha em variável protegida ou arquivo de credenciais com permissão restrita. Não escreva a senha na linha de comando, no chat ou no repositório. O backup contém dados e deve ficar privado e, quando necessário, criptografado.

O CI executa dump, restaura em outro banco descartável e repete os testes de RLS. Isso verifica o esquema do aplicativo; não equivale a testar recuperação operacional de dados reais ou a migrar identidades entre projetos. Nunca restaure um dump sobre o projeto existente sem avaliação e autorização específicas. Acompanhe a ocupação do plano gratuito: os lotes versionados preservam histórico e consomem armazenamento.
