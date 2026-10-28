# Contexto de continuidade para o Codex

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


> Documento de passagem entre planejamento em ChatGPT e implementação no Codex.
> Atualização inicial: 28/10/2026. Este arquivo descreve **intenção e decisões**, não comprova testes executados.

## Ordem de execução aprovada (28/10/2026)

Agora o proprietário pediu ao Codex que **conclua a versão web inteira**, incluindo
correções do MVP, PostgreSQL Supabase em São Paulo (ref
`csiqpzoudruzppfbpqqx`), autenticação, Master, RLS e permissões, dashboard,
insights, relatórios mensais, publicação Streamlit e testes com dados fictícios.
**A API do GestãoClick só será fornecida depois que a versão web for
validada**. A evolução do fluxo de pedidos, cesta e sazonalidade continua
reservada para a fase posterior.

Arquivos de **execução obrigatória e ordenada**:
1. `docs/EXECUCAO_CODEX.md` para implementar o app web integral.
2. `docs/REVISAO_FINAL_CODEX.md` como segunda tarefa para revisar TODO o
   projeto modificado, testar funcionalidade e segurança, corrigir falhas e
   comprovar os resultados.

O sistema não está implementado apenas porque os documentos foram atualizados.
O proprietário poderá ter que autorizar configurações de hospedagem, login
inicial e provedor de e-mail; nunca pedir secrets em chat ou Git.

## Evolução futura de pedidos e padrões (28/10/2026)

O usuário solicitou registrar, para **somente depois de o aplicativo web
funcionar**, o fluxo dos pedidos: `em andamento` não baixa estoque, pois a
compra ainda não está fechada; `em separação` e `concretizada` já baixam.
O gerente deverá avaliar cenários de demanda potencial dos pedidos em
andamento **sem alterar o estoque oficial**, identificar riscos adicionais
e impedir baixa duplicada na transição dos status.

Em etapas mais avançadas, deseja descobrir **produtos geralmente vendidos
juntos** usando pedidos fechados e investigar **sazonalidade** quando
existir histórico suficiente. Não implementar agora. Escopo completo em
`docs/PEDIDOS_E_PADROES_FUTUROS.md`.
## Planejamento de relatórios por e-mail (28/10/2026)

Foi adicionada uma etapa futura de **envio mensal automático** dos indicadores
de estoque e vendas. Usar o e-mail do usuário autorizado, com controle pelo
Master dos destinatários, periodicidade e permissões. O mês anterior é o
período padrão; o relatório deve destacar mais vendidos, riscos, parados e
tendências. Custos apenas quando houver dados e acesso financeiro permitido.

O agendamento e o provedor de envio são externos ao login Supabase Auth.
Implementar apenas depois da migração para PostgreSQL, sistema de acesso e
proteção de dados. **Consulte `docs/RELATORIOS_EMAIL.md` antes de codificar.**

## Decisão de segurança (28/10/2026)

O usuário escolheu **login com senha e permissões controladas por ele como Master**.
Implementar nativamente **Supabase Auth com e-mail/senha** e nome de usuário
para exibição, com cadastro público desativado. Criar tela administrativa para
ativar/desativar usuários e conceder/revogar permissões individualmente por
função. Quatro perfis base: Master, Administrador, Operador e Visualizador.
Validar acesso no banco com RLS e privilégios mínimos, além de ocultar controles
na interface. O aplicativo atual ainda NÃO implementa essa segurança.

O plano mais recente, com detalhes e riscos de SMTP/convites, está em
docs/PLANO_WEB_PRIVADO.md. Esta decisão **substitui a sugestão inicial de OIDC**.

## Atualização de direção (28/10/2026)

O usuário esclareceu que deseja **somente um aplicativo na web**, acessível
por computador e celular, **com controle de acesso por usuário**, e não duas
versões para trabalhar e demonstrar. A arquitetura-alvo passa a ser Streamlit
Cloud com aplicativo privado e PostgreSQL gratuito no Supabase, com autenticação
e autorização implementadas antes do uso de dados empresariais. A preferência
por custo zero permanece. Consulte `docs/PLANO_WEB_PRIVADO.md` para detalhes.

**Esta atualização substitui referências anteriores à versão local como destino
final.** SQLite continua descrevendo o MVP atual, mas não será a persistência
oficial do serviço web. A branch `feat/demo-publica-streamlit` não deve ser
mesclada como proposta de duas aplicações.

## 1. O que queremos resolver

O **Gerente Inteligente de Estoque** nasceu de um problema real da rotina: o ERP registra vendas, quantidades e outros dados, mas é difícil visualizar rapidamente o que corre risco, o que precisa ser reposto, o que está parado e **quanto pedir e por quê**.

Exemplo conceitual:
- O ERP diz: `Produto X: 14 unidades`.
- O gerente deve conseguir dizer: `A cobertura estimada é de N dias; o fornecedor leva L dias; considere comprar Q unidades`, exibindo as premissas usadas.

Não é negócio independente nem substituição do ERP. É um **apoio individual à decisão**, pensado para ser usado gratuitamente no computador e também demonstrável no portfólio com dados fictícios.

## 2. Decisões já tomadas (não rediscutir sem motivo)

1. **Custo zero como prioridade:** não contratar hospedagem, banco gerenciado, API paga ou serviço externo apenas por conveniência.
2. **Versão inicial local:** Streamlit + Python + pandas/NumPy + SQLite. Não migrar prematuramente para React, FastAPI, PostgreSQL, microsserviços ou nuvem.
3. **GestãoClick como fonte oficial futuramente:** a integração só ocorre após concluir/testar o sistema independente; operar **exclusivamente por leitura, sem perspectiva de escrita** e credenciais autorizadas. Não presumir endpoints, movimentações disponíveis, histórico completo ou limites.
4. **Demonstrar antes de conectar:** usar gerador fictício e importação CSV normalizada. Nunca colocar dados comerciais reais no GitHub.
5. **Regras de estoque antes de Machine Learning:** as sugestões devem ser explicáveis, auditáveis e comparáveis com métodos simples.
6. **A interface não calcula regras de negócio:** cálculos isolados no motor, dados em camada separada e persistência local.
7. **Vibe Coding declarado:** houve colaboração com IA na concepção, escrita, avaliação e melhoria do código. O README deve manter isso transparente.
8. **Aprendizado com o código:** documentar função por função e explicar a lógica de blocos relevantes, bibliotecas, decisões e trade-offs, sem gerar comentários inúteis.
9. **Futura automação de NF-e é outro sistema:** integrar ambos só em fase posterior; nunca misturar lógica fiscal com cálculo de reposição.

## 3. Escopo funcional desejado

### MVP prioritário
- Painel com semáforo, quantidade de produtos em risco, valor em estoque (quando conhecido), capital parado e itens com sugestão.
- **Lista de reposição prioritária:** SKU, nome, fornecedor, estoque, demanda diária, cobertura, prazo, reservas, pendências, quantidade recomendada e justificativa; exportar CSV por fornecedor.
- Detalhes do produto: vendas ao longo do tempo, snapshots reais de estoque, tendência, status e **memória do cálculo**.
- Parados e excesso: identificar estoque sem giro e estimativa de capital imobilizado.
- Tendências: comparar médias diárias de janelas curta/longa, sinalizar alta/queda, permitir futura análise de sazonalidade.
- Guardar decisões/sugestões localmente para evolução posterior. Nenhuma compra automática.

### Semáforo conceitual desejado
- 🔴 Ruptura: estoque zero com demanda observada recente.
- 🔴 Crítico: cobertura de até 7 dias.
- 🟠 Alto: mais de 7 a 15 dias.
- 🟡 Atenção: mais de 15 a 30 dias.
- 🟢 OK: acima de 30 a 90 dias.
- 🔵 Excesso: cobertura acima de 90 dias.
- ⚪ Parado: última venda registrada há pelo menos 60 dias, com saldo.
- ⚪ Sem demanda recente/sem histórico: **não presumir parado quando nunca houve venda registrada**.
- **Risco antes da entrega:** indicador adicional, não confundir com semáforo por cobertura. Um produto com cobertura acima de 7 dias pode estar em risco se o fornecedor demorar 10 ou 20 dias.

Os limites estão no `config.py` e podem evoluir com critérios documentados. Um produto novo ou com histórico parcial merece estado/aviso próprio.

### Reposição e explicabilidade
- Demanda observada / dias elegíveis; janelas curtas e longas para comparação.
- Cobertura = estoque físico / demanda média diária, indefinida quando média é zero.
- Ponto de pedido = teto(demanda diária × prazo de entrega + estoque de segurança).
- Posição = saldo físico + pedidos pendentes − quantidades reservadas.
- Estoque-alvo = teto(demanda diária × maior entre prazo e dias alvo + segurança).
- Sugestão de compra = máximo(0, alvo − posição).
- Pedidos pendentes contam na posição para evitar compra duplicada, mas não são considerados entregues no alerta de falta antes do prazo **enquanto não houver datas confiáveis de recebimento**.
- Futuros refinamentos: lotes mínimos, embalagens, múltiplos, lead time variável, revisões periódicas, limite financeiro e calendário do fornecedor.
- Recomendação é **apoio humano**, não instrução garantida de compra.

### Cuidado crítico: ruptura e viés da demanda
- Dias comprovadamente com saldo zero não devem ser computados como zero de demanda.
- **Não** confundir dia sem venda com falta de estoque.
- A ausência de snapshot significa **disponibilidade desconhecida**: o MVP deve sinalizar incerteza, sem inventar histórico nem vendas perdidas.
- Snapshot diário desde as primeiras importações, preservando data, SKU e saldo observado, cria base futura para estudar ruptura.
- Cancelamentos, devoluções, ajustes, dias fechados, estoque de várias localizações e outliers precisarão de tratamento explícito na integração real.

## 4. Dados e indicadores

Tabelas de entrada atuais: catálogo de produtos e vendas com colunas padronizadas, carregáveis por CSV; snapshots locais gerados ao importar.

Dados desejáveis depois: fornecedor, lead time, custo, reservas, compras em aberto, eventos de disponibilidade, identificadores de origem, promoções, devoluções e localização.

Indicadores relevantes:
- Cobertura, ruptura, risco de falta no prazo, ponto de pedido e sugestão.
- Tendência de vendas (30/90 dias).
- Produtos parados e excesso, valor imobilizado **a custo**, quando conhecido.
- Curva ABC (explicitar custo vs receita), giro de estoque, acuracidade por inventário e possível análise de sazonalidade.
- Acuracidade física não pode ser presumida a partir do ERP: requer contagem observada.
- Prioridade pode combinar urgência e classe ABC, mas não assumir ranking completo sem parâmetros e validação.

## 5. Estado atual do código no GitHub

Repositório: `ruanviana6/gerente_inteligente_estoque` (público).
- `app.py`: interface Streamlit de painel, reposição, detalhe, parados, tendências e decisões.
- `config.py`: janelas, limites e valores-padrão.
- `motor.py`: indicadores, cálculo, risco e ABC.
- `dados.py`: geração fictícia, normalização, validação de CSV e snapshots.
- `storage.py`: SQLite, dados importados, snapshots, decisões e backup.
- `integracoes/gestaoclick.py`: placeholder intencional, não acessa o ERP.
- `tests/`: testes de negócio e persistência.
- `.github/workflows/tests.yml`: workflow de CI.
- `docs/regras-de-negocio.md`: fórmulas do MVP.
- `README.md`: descrição pública e instruções.

Os quatro arquivos iniciais fornecidos pelo usuário (`app.py`, `config.py`, `dados.py`, `motor.py`) foram **referências** e não precisam ser copiados cegamente: a versão existente no GitHub é uma reimplementação orientada por critérios do projeto.

**Estado de verificação:** a implementação foi enviada ao GitHub, mas **a execução integral de testes e a verificação visual na nuvem não foram confirmadas na conversa de origem**. O Codex deve investigar e executar de verdade antes de declarar o MVP pronto.

Comandos úteis:
```bash
python -m pip install -r requirements.txt
python -m pytest
ruff check .
streamlit run app.py
```

## 6. Como trabalhar com o usuário

O usuário quer um projeto ao mesmo tempo útil e didático, com práticas profissionais sem burocracia desnecessária. Ao propor mudanças:
- Explicar *problema → decisão técnica → justificativa → validação → limitações*.
- Respeitar Engenharia de Software, Clean Code, segurança básica, testes, modelagem SQL, logística/estoque e manutenção.
- Adicionar docstrings em português para funções, e comentários nos blocos cuja lógica não seja óbvia. Preferir clareza a excesso de comentários.
- Não substituir solução simples comprovada por ML sem avaliação.
- Verificar documentação oficial quando APIs, bibliotecas ou detalhes de terceiros forem relevantes.
- Não inventar resultados de testes, garantias de escalabilidade ou qualidade de previsão.
- Trabalhar em branch/PR para mudanças substanciais, sem alterar dados reais, segredos ou main sem aprovação.

## 7. Fases futuras (prioridades, não autorização para implementá-las agora)

1. **Auditoria e estabilização do MVP**: dependências, testes, execução Streamlit, problemas de qualidade, cenários de ruptura e validação de cálculos.
2. **Experiência de uso**: melhorar layout, filtros, clareza dos avisos e exportação, mantendo leveza.
3. **Qualidade dos dados**: snapshots, consistência dos arquivos e eventual mapeamento das exportações reais autorizadas.
4. **Previsão estatística**: média móvel, tendência, padrões semanais e sazonalidade quando houver histórico suficiente; comparar erro em backtest temporal.
5. **Integração GestãoClick**: somente após finalização do app e acesso autorizado/documentado; sincronização de leitura, reconciliação e tratamento de falhas.
6. **Evoluções condicionais**: RFM de clientes conforme LGPD, ML quando demonstrar ganho, chat que cite valores calculados, alertas de baixo custo, expedição simples e integração futura com automação de NF-e.

## 8. Instrução para a primeira sessão no Codex

**Primeira tarefa recomendada: auditoria apenas de leitura** — revisar README, AGENTS.md, este contexto e regras de negócio; executar os testes e iniciar o Streamlit se o ambiente permitir; registrar achados por severidade e propor um plano curto para corrigir os problemas reais. Não implementar novas funcionalidades nem acessar GestãoClick nessa primeira tarefa.

A cada tarefa posterior, use o repositório como fonte, formule escopo/critério de aceite claros e preserve um breve resumo das decisões para não depender de memória entre conversas.
