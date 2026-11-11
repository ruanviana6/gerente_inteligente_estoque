# Segunda tarefa Codex: revisão final, testes de segurança e correções

## Regra permanente: GestãoClick SEMPRE somente leitura

**Regra do proprietário, inclusive depois da integração com a API:** o Gerente Inteligente de Estoque **nunca pode modificar nada no GestãoClick**. A integração será **unidirecional**: GestãoClick → Gerente (consulta/sincronização de dados), sem envio de comandos operacionais de volta ao ERP.

- Permitido: autenticar-se na API autorizada, consultar produtos, saldo, vendas, pedidos, fornecedores e histórico que a API realmente disponibilizar; armazenar cópia analítica e snapshots no **próprio banco Supabase** do gerente.
- Proibido em qualquer fase: criar, editar ou excluir produtos, preços, clientes, vendas, pedidos, status, reservas, movimentações, saldos, compras, notas fiscais ou qualquer outro objeto do GestãoClick. Não realizar baixa nem estorno no ERP.
- Não implementar endpoints de escrita, rotinas de sincronização reversa, callbacks/webhooks que alterem o ERP, nem botões administrativos de "aplicar sugestão" no GestãoClick. Recomendações e simulações só geram informações e ações **internas ao gerente**.
- Usar credencial de API com escopo somente leitura **se o GestãoClick disponibilizar**; em qualquer caso restringir o cliente de integração aos métodos/endpoints comprovadamente de consulta. Operações de escrita HTTP (POST/PUT/PATCH/DELETE para mutação) ficam bloqueadas na camada de integração.
- Os status `em andamento`, `em separação` e `concretizada` são **observados**, nunca modificados pelo gerente. Quando a operação do ERP já descontou o estoque, o gerente não realiza segunda baixa.
- Criar testes que verifiquem que as rotinas de importação/sincronização/relatórios/simulações **não disparam mutações na API**. Registrar erros e divergências no banco interno, para conferência humana no sistema oficial.
- O sistema pode criar/alterar **seus próprios** usuários, permissões, indicadores, relatórios e histórico no Supabase. A proibição de escrita aplica-se ao **GestãoClick**, não ao banco interno do gerente.
- Não interpretar "integração futura" como autorização futura para escrita. Nenhum componente deve prever escrita no GestãoClick como requisito ou atalho.


> EXECUTAR DEPOIS da tarefa de docs/EXECUCAO_CODEX.md.
> Revisão independente: não confiar nos relatos da primeira execução.
> Não executar ações destrutivas, não expor credenciais, não publicar dados reais.

## Comando operacional

Receba a branch/PR da implementação do Gerente Inteligente de Estoque.
Releia a versão mais recente de TODOS os arquivos de código, migrations, CI,
AGENTS.md, docs/PLANO_WEB_PRIVADO.md, docs/RELATORIOS_EMAIL.md,
docs/regras-de-negocio.md e docs/EXECUCAO_CODEX.md. Compare requisitos
com a entrega REAL e faça revisão funcional, de dados, arquitetura e segurança.
Corrija diretamente o que for permitido e comprove a correção com novos testes.
Se encontrar bloqueio externo, delimite-o com exatidão e indique a ação manual.

## 1. Auditoria de cobertura dos requisitos

Produza matriz requisito / arquivo / teste / resultado, separando:
- Implementado e verificado;
- Implementado mas sem teste suficiente;
- Não implementado;
- Bloqueado por conta externa ou por decisão de usuário.

Validar de ponta a ponta: app web único, banco Supabase São Paulo
(project ref csiqpzoudruzppfbpqqx), login e senha, Master, perfis,
permissões individuais, estoque, histórico/importação, semáforo, cálculos,
sugestões de compra, indicadores, flash de insights, relatórios mensais,
agendamento, logs, backups, documentação e publicação Streamlit.
Não integrar API GestãoClick.

## 2. Testes de código e domínio

Executar e documentar comandos e saídas:
- python -m pytest (incluindo novos testes de integração e regressão);
- ruff check . (ou conjunto configurado no CI);
- inicialização real do Streamlit e smoke test de navegação;
- testes para condições de borda: CSV inválido/duplicado, formato brasileiro,
  números ausentes, período incompleto, sem vendas, ruptura e snapshots faltantes,
  lead time, reservas, pedidos pendentes, custo ausente e mudança de parâmetros;
- formulas consistentes entre motor, dashboard, exportações e e-mails;
- persistência após reinício, importação repetida e consistência transacional.

O último CI antes da implementação tinha 11 testes aprovados e 4 erros de
lint (dois imports sem uso e duas variáveis ambíguas). Verificar que foram
corrigidos, mas não tomar esses 11 testes como cobertura suficiente.

## 3. Segurança: testes POSITIVOS e NEGATIVOS

Criar somente identidades e dados **fictícios** para teste.

### Autenticação e sessões
- Sem login: nenhuma consulta, importação, exportação ou dado interno.
- Senha inválida, usuário não cadastrado/desativado, sessão expirada,
  logout, renovação de token e recuperação de acesso.
- Cadastro público desativado; Master não criado via formulário público.
- Tentativa de alterar ou inserir metadados de usuário para obter papel Master.
- Permissões alteradas ou usuário bloqueado enquanto outra sessão segue ativa.
- Garantir isolamento entre duas sessões de usuários diferentes no Streamlit;
  revisar session_state, caches globais, conexões e vazamento entre abas.

### Autorização e banco
- Avaliar cada permissão dashboard/inventory/replenishment/trends/finance/
  reports/import/parameters/decisions/users/permissions e suas variações.
- Tentar consultar tabelas e endpoints da API Supabase diretamente com
  credencial anon, com usuário não autorizado, Operador e Visualizador.
- Inspecionar grants e RLS em **todas** as tabelas expostas. Testar SELECT,
  INSERT, UPDATE e DELETE indevidos, inclusive troca de identificador em
  payload (IDOR/BOLA), escalonamento de perfil e leitura de outra área.
- Verificar políticas WITH CHECK, views security_invoker, funções SECURITY
  DEFINER e revogação de EXECUTE quando aplicável. Auditar alterações sensíveis.
- Confirmar que finance.view limita os dados financeiros na consulta,
  não só na interface. RLS protege linhas, não colunas: revisar endpoints,
  views e consultas específicas para evitar exposição de custos.
- Service key jamais usada em consultas comuns ou enviada ao navegador.
  Não autorizar o cliente a contornar RLS.
- Rodar Supabase security/performance advisors e registrar avisos, ações
  corretivas e limitações.

### Aplicação e dados
- Buscar credenciais, tokens, emails, bancos, dumps e dados comerciais
  por todo o repo, logs, exemplos, markdown e workflow CI.
- Checar SQL parametrizado, validação de upload e tipos, limites de tamanho,
  prevenção de injeção, nomes de arquivos e exposição desnecessária de erros.
- Verificar conteúdo HTML e entradas não confiáveis exibidas no Streamlit.
- Revisar dependências, versões fixadas e vulnerabilidades relevantes.
- Senhas nunca em logs, cookies expostos, README ou Git.
- Testar backup/restauração em ambiente de teste e comportamento de falha
  no banco, sem apagar dados do projeto real.

## 4. Relatórios mensais, scheduler e e-mail

- Validar fuso America/Fortaleza e fechamento do mês civil correto, inclusive
  virada de ano, mês sem dados e possíveis diferenças de data/horário.
- Relatório por usuário e por permissão. Remover acesso antes do envio deve
  impedir recebimento de conteúdo restrito.
- Verificar que os números batem com consultas do banco e motor.
- Simular scheduler duas vezes: não duplicar e-mails por período/destinatário.
- Testar falha/retry/limite de tentativas sem repetição indevida.
- Não disparar e-mail real não autorizado; usar sandbox/fake provider.
- Se SMTP/provedor/domínio ainda não estiver configurado, declarar envio
  como BLOQUEADO, mas confirmar que geração e dry-run funcionam.

## 5. Testes reais da implantação web

- Revisar Streamlit secrets e variáveis esperadas sem imprimi-las.
- Se o app estiver publicado, abrir a URL verdadeira e provar acesso,
  navegação e restrições com identidades de teste. Abrir também no contexto
  de tela pequena, se o ambiente permitir; registrar limitações.
- Confirmar que o banco remoto persiste entre sessões e reinício.
- Caso não exista URL por limitação da ferramenta, NÃO afirmar deploy.
  Fornecer os cliques/configurações exatas para o dono finalizar.
- App Streamlit privado por plataforma não substitui RLS e autorização.
  Exigir que todos os dados operacionais estejam protegidos no banco.

## 6. Correções e fechamento

- Corrigir todos os defeitos relevantes detectados, preferencialmente em
  commits pequenos na branch de implementação.
- Acrescentar teste para cada falha reproduzida.
- Rodar novamente CI, lint e testes de autorização; revisar as mudanças.
- Atualizar docs e README com comportamento realmente disponível.
- Reportar evidências objetivas: tabela de requisitos, comandos, quantidades
  de testes, resultado do GitHub Actions, projeto de banco verificado,
  URL web (se realmente publicada), limites e itens bloqueados.
- Separar P0/P1/P2, riscos remanescentes, etapas manuais e próximos passos.
- Não marcar como "tudo pronto" com falhas P0/P1 ou sem verificar a URL.
- Proibir consumo da API GestãoClick até a aprovação do proprietário.

## Definição de pronto

**Pronto para demonstração**: app web publicado, dados fictícios, login,
Master e ao menos dois papéis testados, todos os cálculos principais
verificados, persistência, roles/RLS validados, CI verde e sem falhas críticas.
Relatórios in-app funcionais, e-mail automático habilitado apenas se
provedor/remetente/autorização estiverem realmente configurados e testados.

**Pronto para dados reais da empresa**: além de tudo acima, exige
autorização da empresa, análise adequada de privacidade e segurança,
adequação do backup e validação operacional. Não equiparar uma demo com
uso corporativo aprovado.

**Obrigatório**: fornecer uma conclusão honesta, citando o que foi testado
e o que depende do usuário. Não inventar resultado positivo.

