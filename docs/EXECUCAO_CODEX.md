# Ordem principal para o Codex: finalizar a aplicação web

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


> INSTRUÇÃO DE EXECUÇÃO, não comprovação de entrega. Atualizado em 28/10/2026.
> Antes de agir, ler AGENTS.md, docs/CONTEXTO_CODEX.md, docs/PLANO_WEB_PRIVADO.md, docs/RELATORIOS_EMAIL.md e docs/regras-de-negocio.md.
> Este documento é a fonte para a **fase de implementação**. Em seguida, executar docs/REVISAO_FINAL_CODEX.md como fase separada.

## Resultado exigido

Entregar **um único aplicativo Gerente Inteligente de Estoque**, em Python e Streamlit, pronto para uso pelo navegador (computador e celular), com persistência Supabase PostgreSQL, autenticação por e-mail/senha, um usuário Master, permissões granulares e análise explicável de estoque. Realizar o deploy real no Streamlit Community Cloud, se a conta e as credenciais estiverem acessíveis; testar a URL publicada. O projeto deve seguir simples, gratuito sempre que possível e com código de aprendizagem claro.

Projeto Supabase **existente** e aprovado:
- Nome: gerente-inteligente-estoque
- Referência/ID de projeto: csiqpzoudruzppfbpqqx
- Região: sa-east-1 (São Paulo, Brasil)
- Organização: Estoque
- Estado observado na preparação: ACTIVE_HEALTHY, sem tabelas ou migrações da aplicação.
- Não criar outro projeto, não trocar de região, não ativar serviço pago, não apagar dados ou contas.

Repositório: ruanviana6/gerente_inteligente_estoque.
O planejamento mais recente foi registrado na branch docs/web-unica-privada e PR #1. A branch main ainda contém a implementação local. O Codex deve buscar **ambas** as referências, ler e reconciliar as diferenças e trabalhar em uma branch de implementação exclusiva, preservando o histórico; não substituir código novo inadvertidamente.

## Escopo OBRIGATÓRIO da entrega web

1. **Diagnóstico e reparos do MVP.** Executar testes e lint; investigar erros existentes e confirmar inicialização real do Streamlit. O último CI observado teve 11 testes aprovados, mas falhou em Ruff por dois imports não utilizados e dois nomes de variável ambíguos. Corrigir sem quebrar regras de negócio.
2. **Banco persistente.** Projetar tabelas para catálogo, saldos/snapshots, histórico de vendas importadas, configurações por SKU, decisões e auditoria. Preservar contratos e fórmulas existentes. Criar migrações SQL versionadas, constraints, índices adequados, dados de exemplo e scripts de implantação/reversão seguros. Não migrar lixo/segredos do SQLite. Importações CSV atuais são completas: manter sem presumir integração GestãoClick.
3. **Autenticação.** Integrar Supabase Auth e-mail/senha, cadastro público desativado, login/logout, expiração e renovação de sessão, criação segura da conta Master sem senha padrão nem privilégio editável por navegador. Criar procedimento de bootstrap que exija ação autorizada do dono, sem inventar endereço de e-mail ou senha.
4. **Autorização.** Perfis Master, Administrador, Operador e Visualizador; ativação/desativação de contas e permissões individuais por funcionalidade. Implementar painel do Master para administrar usuários e consultar auditoria. Negar acesso por padrão. RLS/grants restritivos em **todas** as tabelas expostas, inclusive financeiras. Autorização no banco e por operação (não basta esconder botões). Cuidado com informações financeiras: RLS não é filtro de colunas; não oferecer consultas/views que exponham custo sem autorização específica.
5. **Interface web única.** Fluxos de painel inicial, consulta de estoque, curva ABC/tendências, cobertura, risco antes do lead time, recomendações com explicação, configurações autorizadas, importação CSV e registro de decisões. Layout acessível e responsivo em Streamlit. Sem instalação local para operação diária.
6. **Flash de insights.** Avisos rotativos explicáveis no início, calculados a partir dos indicadores reais de cada usuário; sem números inventados por IA e sem vazamento por cache.
7. **Relatórios mensais e e-mails.** Implementar geração de resumo mensal por período fechado com mais vendidos, variações, produtos parados, risco de ruptura, reposição e indicadores financeiros somente quando houver fonte confiável e acesso finance.view. Disponibilizar visualização/exportação autorizadas no próprio app. Integrar programação mensal no dia 1 (fuso America/Fortaleza), mecanismo de entrega de e-mails com provedor configurável e histórico/idempotência. Seguir docs/RELATORIOS_EMAIL.md. **O envio externo não pode ser habilitado até o dono configurar provedor, remetente validado e autorizar destinatários; sem isso, entregar a geração, teste/dry-run e instruções, assinalando o bloqueio.** Não criar conta paga nem disparar e-mails reais sem configuração explícita.
8. **Deploy.** Preparar requirements/versões, arquivo de entrada, secrets, configurações do Streamlit, variáveis Supabase, documentação passo a passo e verificações de inicialização. Se houver acesso autorizado à conta Streamlit, efetivar o deploy único e validar a URL remota com dados fictícios. Se o ambiente do Codex não permitir clicar no Streamlit Cloud nem acessar secrets externos, parar apenas na etapa bloqueada, apresentar a **ação manual mínima e exata**, e continuar todas as outras entregas. Não declarar web funcional sem abrir e testar a URL real.
9. **Qualidade, observabilidade e uso.** Logs sem segredos, mensagens úteis de erro, telas vazias, validação CSV, backups/exportações apropriadas e instruções de restauração para plano Free. Manter testes unitários, de integração e CI, além de documentação realista do que foi implementado.

## Segurança e exigências inegociáveis

- Não consultar dados internos antes de validar sessão **e** permissão.
- Supabase Auth guarda as senhas. Nunca registrar tokens, credenciais, chaves, senhas, arquivos reais de clientes ou dados fiscais no Git, app, CI, logs ou chat.
- Não conceder privilégio de Master por raw_user_meta_data / user_metadata. Usar metadados protegidos e/ou tabelas controladas pelo banco, com proteção contra elevação.
- Não usar service_role/secret key indiscriminadamente; não compartilhar cliente/sessão ou cache entre diferentes usuários Streamlit.
- Revisar RLS, grants, policies de SELECT/UPDATE/INSERT/DELETE, possíveis SECURITY DEFINER, views, isolamento por funções, revogação de acesso e sessão expirada.
- Simular usuários com papéis e permissões distintos, verificar negações e tentativas de escalonamento. Não inventar sucesso de testes.
- A revisão de segurança deve ser tão séria quanto a funcional. Dados da empresa só com autorização externa apropriada, avaliação LGPD e permissões.
- Credenciais em ferramentas de Secrets. **Não pedir que o usuário cole secrets no chat ou no GitHub.**
- Sem promover para main nem publicar dados reais automaticamente; solicitar revisão quando houver risco de produção. Deploy apenas com dados fictícios até a aprovação.
- O único gasto autorizado é R$ 0. Sempre verificar limites e condições dos serviços e comunicar se algum recurso exige pagamento.

## Fora do escopo desta fase, PROIBIDO adiantar

- **API GestãoClick:** não conectar, testar credenciais, buscar dados reais ou alterar ERP nesta entrega. O proprietário fornecerá a API posteriormente e a integração será de leitura estrita e permanente, com autorização, sem qualquer escrita no ERP.
- Não fazer baixas de estoque, emissão de NF-e, compras automáticas ou mudanças de status no ERP.
- Pedido em andamento, risco de conversão, padrões de cesta, sazonalidade avançada e qualquer ML: manter exclusivamente no backlog docs/PEDIDOS_E_PADROES_FUTUROS.md para depois da versão web estar operacional, salvo componentes básicos já necessários ao dashboard.

## Procedimento de execução

1. Criar branch de desenvolvimento e diagnóstico curto com inventário de módulos/testes, dependências, regras e riscos.
2. Executar e corrigir CI existente.
3. Construir banco e políticas com migrations em código e testes automatizados; só aplicar migrations no projeto Supabase aprovado após revisar impactos, sem modificações destrutivas.
4. Implementar auth, bootstrap Master, autorização e testes negativos.
5. Migrar fluxos da UI, insights e relatórios para a persistência remota.
6. Preparar/publicar Streamlit; validar com dados fictícios e perfis não privilegiados.
7. Configurar e testar rotina de relatórios sem enviar dados reais ou não autorizados.
8. Executar testes automatizados, lint e testes funcionais; atualizar documentação sobre limitações reais.
9. Abrir PR de implementação e descrever o que foi realmente testado. Enumerar separadamente etapas que aguardam exclusivamente ações externas do proprietário.

**Não encerrar como 'concluído' se componentes essenciais não foram testados.** Não marcar deploy como concluído sem URL verificada. Não ficar bloqueado por etapa que dependa de interface/secret externo: terminar o restante, gerar checklist curto e apontar o impedimento.

## Entregáveis a apresentar

- PR de implementação, branch e diff bem explicados;
- Migrations SQL e scripts de setup/seed fictício;
- Banco Supabase com RLS confirmado e conta Master por procedimento seguro;
- Código de login/roles, frontend e motor funcionando;
- Testes de autorização/isolamento, CI e logs de testes;
- Web URL verificada, se habilitada; caso contrário, passo manual específico;
- Relatórios mensais in-app e rotina agendada pronta/validada; envio real apenas após requisitos externos;
- README e guia de deploy/segurança em pt-BR, linguagem natural, honestidade sobre Vibe Coding, sem travessões no README;
- lista objetiva de bloqueios legítimos, sem fingir entrega.

## Verificação FINAL obrigatória

**Ao finalizar a implementação, executar uma SEGUNDA TAREFA independente:** seguir integralmente docs/REVISAO_FINAL_CODEX.md. Não considerar 'código escrito' equivalente a 'projeto funcionando'. A auditoria deve descobrir e corrigir problemas, não apenas gerar um parecer.
