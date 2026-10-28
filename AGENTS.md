# Instruções para agentes de programação — Gerente Inteligente de Estoque

## Diretiva de execução integral aprovada em 28/10/2026

O proprietário autorizou o **desenvolvimento da versão web completa** antes de conectar a API GestãoClick. O Codex deve ler e executar:
1. `docs/EXECUCAO_CODEX.md`: implementação, migração, login Master, permissões, painel web, insights, relatórios, deploy com dados fictícios e correções.
2. `docs/REVISAO_FINAL_CODEX.md`: **segunda tarefa independente**, revisão do projeto atualizado, testes funcionais, de integração e de segurança, correção dos defeitos e verificação da URL publicada.

Supabase aprovado: projeto `gerente-inteligente-estoque`, ref `csiqpzoudruzppfbpqqx`, região `sa-east-1` (São Paulo), plano gratuito desejado. **Não criar outro projeto**. O banco estava vazio no diagnóstico.

A implementação deverá ocorrer em branch/PR própria, verificando tanto a `main` quanto os requisitos mais recentes na branch `docs/web-unica-privada`. Não executar trabalho simultâneo que conflite com outro agente. O proprietário quer o app funcionando, mas segredos e operações nos painéis externos podem exigir sua participação. Não prometer deploy/testes concluídos sem evidência.

**Integração GestãoClick fica expressamente FORA desta entrega.** O proprietário fornecerá acesso à API somente após o web estar validado. Pedidos em andamento, cestas de produtos e sazonalidade avançada continuam no backlog posterior. Não alterar ERP nem usar dados reais nesta fase.

## Objetivo e limites permanentes
- Ferramenta **pessoal e gratuita** para apoiar a rotina de estoque dentro de uma empresa; **não** é um ERP, SaaS nem produto comercial.
- **Decisão mais recente: a aplicação oficial será única, acessível via web e com acesso autorizado por usuário.** Não manter duas instalações operacionais (local + demo pública). Consulte `docs/PLANO_WEB_PRIVADO.md` antes de alterar hospedagem, autenticação, dados ou interface.
- O GestãoClick é e continuará sendo a fonte operacional oficial. No estado atual o aplicativo só utiliza demonstração fictícia ou CSV local. **Não conecte a API, não escreva no ERP e não crie compras automaticamente** sem solicitação explícita para uma etapa futura.
- A futura integração com o outro projeto de automação de NF-e é apenas planejada; **não implemente emissão ou alteração fiscal** por iniciativa própria.
- Mantenha a solução simples e gratuita. Arquitetura-alvo: Python + Streamlit Community Cloud privado + PostgreSQL Supabase Free + autenticação e autorização por usuário. O código atual usa SQLite local, mas **SQLite não é solução de persistência na nuvem**. Evite dependências, serviços pagos e múltiplos ambientes de uso sem justificativa.

## Estilo de comunicação e apresentação
- Escreva **sempre em português brasileiro**, em tom simples, direto, natural e amigável.
- No `README.md`, apresente a motivação de forma humana, preferencialmente em primeira pessoa. Não faça o projeto parecer grandioso, corporativo, uma startup ou um produto comercial.
- **Não use travessão (—) no README**. Prefira ponto, vírgula, dois-pontos ou frases separadas.
- Explique o uso de Vibe Coding e IA com transparência, sem exageros ou autopromoção.
- Mantenha o README bem estruturado, mas com frases curtas, sem linguagem excessivamente formal e sem promessas de funcionalidades ainda não validadas.

## Onde consultar o contexto
- `README.md`: propósito, funcionalidades e uso público.
- `docs/CONTEXTO_CODEX.md`: visão do produto, prioridades, histórico de decisões e plano de evolução. Consulte ao planejar novas funcionalidades.
- `docs/PLANO_WEB_PRIVADO.md`: decisão atual sobre aplicativo web único, acesso individual e banco persistente.
- `docs/regras-de-negocio.md`: fórmulas e restrições do domínio. Consulte ao alterar cálculos.
- `config.py`: parâmetros, limites e janelas vigentes. Consulte ao alterar o semáforo.
- `motor.py`: lógica de demanda, cobertura, reposição e classificação.
- `dados.py` / `storage.py`: contratos de importação, validação e persistência.
- `app.py`: interface Streamlit; não duplique cálculos de domínio nela.

## Regras de engenharia
- Separe interface, cálculo, validação, armazenamento e futura integração.
- Código Python claro, tipado quando útil, com nomes expressivos, funções de responsabilidade definida e **docstrings em português explicando o que cada função faz, por que existe e a lógica importante**. Comente blocos não óbvios; evite comentários redundantes linha a linha.
- Priorize soluções determinísticas e explicáveis; **não adicione ML apenas para ter IA**.
- Crie ou atualize testes para cada regra crítica, cenário de borda e correção. Não altere fórmulas para fazer um teste passar sem justificar a regra.
- Não alegue que testes ou execução visual passaram sem executá-los. Se não puder executar, informe o impedimento e o comando de validação.
- Preserve a autoria e a transparência do uso de **Vibe Coding com IA** no README.

## Regras de pedidos e análises futuras, não implementar nesta fase
- O usuário informou os status de pedido **em andamento**, **em separação** e **concretizada**. `em andamento` **não baixa estoque**; os demais já têm impacto no estoque do GestãoClick.
- Não tratar `em andamento` como venda fechada; mostrar impacto **potencial** em cenários separados, sem alterar o saldo oficial ou registrar baixas.
- Ao mudar de `em separação` para `concretizada`, não efetuar nova baixa. Se o saldo do ERP já reflete o movimento, não subtrair novamente.
- Somente depois da versão web estável, login/permissões e validação dos dados reais da integração, estudar cenários de conversão, análise de cesta de produtos comprados juntos e sazonalidade.
- Não inventar taxa de conversão, recomendação sazonal ou associação sem histórico, amostra e validação adequados. Consulte `docs/PEDIDOS_E_PADROES_FUTUROS.md` para o escopo.
## Invariantes de estoque
- Cobertura depende de demanda diária **elegível**; não trate ruptura comprovada como zero de demanda. Ausência de snapshot não prova disponibilidade e não autoriza inventar demanda perdida.
- Sem histórico de vendas não significa automaticamente produto parado. Separe **status por cobertura** de **risco de faltar antes do prazo do fornecedor**.
- Compra sugerida considera demanda média, prazo, estoque de segurança, saldo, reservas e pedidos pendentes; mostre a justificativa e não execute compras.
- Defina claramente se os valores financeiros são a custo ou receita; não invente custos ausentes.
- Importações CSV do MVP são **completas**, não incrementais. Não presuma formato nativo específico do GestãoClick sem validar exemplos/autorização.

## Autenticação e autorização aprovadas para a web
- Login com **e-mail e senha via Supabase Auth**, nome de usuário apenas para exibição; não criar sistema próprio de senhas.
- O usuário proprietário terá o papel **Master**, atribuído em procedimento administrativo seguro, sem permitir promoção pela UI.
- Apenas o Master poderá gerenciar usuários, ativar/desativar contas e conceder/revogar **permissões individuais por funcionalidade**. Perfis possíveis: Master, Administrador, Operador e Visualizador.
- Cadastro público desligado; padrão negar acesso. Verificar autorização tanto na tela quanto na operação com banco e testar RLS/grants de todas as tabelas expostas.
- Não usar chave administrativa do Supabase para as consultas comuns. A Master UI deve manter auditoria de mudanças e impedir escalação de privilégio.
- Atenção ao SMTP padrão do Supabase: não prometer envio de convites ou recuperação de senha para usuários externos sem configurar serviço compatível.
- Consulte o plano atualizado em docs/PLANO_WEB_PRIVADO.md, que substitui a antiga sugestão de OIDC como login padrão.

## Relatórios automáticos planejados
- O plano futuro inclui relatório **mensal de vendas e estoque por e-mail**, com dados do mês anterior e controle do Master. Consulte `docs/RELATORIOS_EMAIL.md`.
- Permissões específicas: `reports.view`, `reports.export`, `reports.receive`, `reports.manage`. Validar acesso do destinatário aos indicadores na geração e antes do envio; informação financeira exige `finance.view`.
- Não enviar dados empresariais sem autorização. Não presumir que Supabase Auth envia e-mails de relatórios; exigir provedor de entrega e agendamento próprios, segredos protegidos, idempotência e histórico de falhas.
- Não implementar nem ativar envios automáticos sem concluir autenticação, autorização e testes.

## Segurança e controle de alterações
- Repositório público e futuro app web: **nunca** versionar dados comerciais, CSV reais, SQLite, backups, dados pessoais, notas fiscais, tokens ou `.env`.
- Usar informações reais da empresa requer autorização; o futuro aplicativo web deve ser privado, com autenticação, autorização e dados protegidos. Não publicar o aplicativo SQLite atual como serviço real.
- Para a futura API, consultar primeiro a documentação oficial e permissões da conta; leitura antes de qualquer escrita, e segredos sempre fora do Git.
- Antes de mudanças amplas, fazer diagnóstico e plano curto; preferir alterações pequenas em branch/PR, preservar comportamento validado e explicar trade-offs.
- Rodar quando aplicável: `python -m pytest`, `ruff check .` e `streamlit run app.py`. Testar com dados fictícios.
- O proprietário autorizou desenvolver e preparar/publicar o único app web **com dados fictícios**, após testes; publicar depende de acesso real à conta Streamlit e secrets configurados. Não mesclar na `main`, usar dados reais ou operar integrações externas/ERP sem aprovação explícita. Evitar tarefas com cobrança.

## Tarefa atual recomendada
Realizar a **implementação integral em etapas**, com diagnóstico/testes antes de cada modificação, seguindo `docs/EXECUCAO_CODEX.md`. Ao finalizar, solicitar execução da auditoria separada `docs/REVISAO_FINAL_CODEX.md`. Verificar URL e CI; não apresentar um trabalho incompleto como concluído.
