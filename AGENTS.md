# Instruções para agentes de programação — Gerente Inteligente de Estoque

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

## Objetivo e limites permanentes
- Ferramenta **pessoal e gratuita** para apoiar a rotina de estoque dentro de uma empresa; **não** é um ERP, SaaS nem produto comercial.
- O GestãoClick é e continuará sendo a fonte operacional oficial. No estado atual o aplicativo só utiliza demonstração fictícia ou CSV local. **Não conecte a API antes da etapa autorizada e NUNCA escreva no ERP, inclusive após a integração.**
- A futura integração com o outro projeto de automação de NF-e é apenas planejada; **não implemente emissão ou alteração fiscal** por iniciativa própria.
- Mantenha a solução simples, local e sem mensalidades: Python, Streamlit, pandas, SQLite e testes. Evite infraestrutura, dependências e serviços pagos sem justificativa e aprovação.

## Estilo de comunicação e apresentação
- Escreva **sempre em português brasileiro**, em tom simples, direto, natural e amigável.
- No `README.md`, apresente a motivação de forma humana, preferencialmente em primeira pessoa. Não faça o projeto parecer grandioso, corporativo, uma startup ou um produto comercial.
- **Não use travessão (—) no README**. Prefira ponto, vírgula, dois-pontos ou frases separadas.
- Explique o uso de Vibe Coding e IA com transparência, sem exageros ou autopromoção.
- Mantenha o README bem estruturado, mas com frases curtas, sem linguagem excessivamente formal e sem promessas de funcionalidades ainda não validadas.

## Onde consultar o contexto
- `README.md`: propósito, funcionalidades e uso público.
- `docs/CONTEXTO_CODEX.md`: visão do produto, prioridades, histórico de decisões e plano de evolução. Consulte ao planejar novas funcionalidades.
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

## Invariantes de estoque
- Cobertura depende de demanda diária **elegível**; não trate ruptura comprovada como zero de demanda. Ausência de snapshot não prova disponibilidade e não autoriza inventar demanda perdida.
- Sem histórico de vendas não significa automaticamente produto parado. Separe **status por cobertura** de **risco de faltar antes do prazo do fornecedor**.
- Compra sugerida considera demanda média, prazo, estoque de segurança, saldo, reservas e pedidos pendentes; mostre a justificativa e não execute compras.
- Defina claramente se os valores financeiros são a custo ou receita; não invente custos ausentes.
- Importações CSV do MVP são **completas**, não incrementais. Não presuma formato nativo específico do GestãoClick sem validar exemplos/autorização.

## Segurança e controle de alterações
- Repositório público: **nunca** versionar dados comerciais, CSV reais, SQLite, backups, dados pessoais, notas fiscais, tokens ou `.env`.
- Usar informações reais da empresa requer autorização; manter a aplicação local restrita, sem exposição pública de dados reais.
- Para a futura API, consultar primeiro a documentação oficial e permissões da conta; leitura estrita e permanente, sem qualquer escrita no ERP; segredos sempre fora do Git.
- Antes de mudanças amplas, fazer diagnóstico e plano curto; preferir alterações pequenas em branch/PR, preservar comportamento validado e explicar trade-offs.
- Rodar quando aplicável: `python -m pytest`, `ruff check .` e `streamlit run app.py`. Testar com dados fictícios.
- Não fazer deploy, mesclar na `main`, executar integrações externas nem operações com dados reais sem aprovação explícita.

## Primeira tarefa recomendada
Ao iniciar com este projeto, priorize uma **auditoria read-only** do estado real do repositório, validação dos testes e inicialização do Streamlit, antes de propor novas funcionalidades. Consulte `docs/CONTEXTO_CODEX.md` para o que já foi decidido.
