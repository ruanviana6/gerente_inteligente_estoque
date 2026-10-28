# Auditoria independente da Issue #3

Data: 28/10/2026, fuso America/Fortaleza.

## Conclusão e escopo real

**A versão web não está aprovada.** O pré-requisito da Issue #3, a implementação da Issue #2, não foi encontrado no GitHub. A auditoria avançou sobre o código realmente disponível, reproduziu defeitos, corrigiu o MVP e acrescentou regressões. Não houve publicação, alteração da `main`, operação no GestãoClick, uso de dados comerciais ou envio de e-mail.

- Referência auditada: `main`, commit `e65d66b05bbd9f6c8156113e25a3167f34d4b72e`.
- Requisitos revisados: branch `docs/web-unica-privada`, commit `69a17d627d70d52ffdc3a9d9d832889483a17554`, PR #1 e documentos `AGENTS.md`, `CONTEXTO_CODEX.md`, `PLANO_WEB_PRIVADO.md`, `EXECUCAO_CODEX.md`, `RELATORIOS_EMAIL.md`, `REVISAO_FINAL_CODEX.md` e `regras-de-negocio.md`.
- Correções: [PR #4](https://github.com/ruanviana6/gerente_inteligente_estoque/pull/4), branch `auditoria/issue-3-validacao`.
- Os arquivos foram obtidos diretamente pelo conector GitHub. Alterações locais de uma tentativa anterior não foram presumidas como implementação entregue.
- Revisões paralelas independentes examinaram domínio, importação, persistência e segurança; os resultados foram confrontados com execução e CI.

Na conferência inicial, o único PR existente era o #1, de documentação. As outras branches eram `main`, `docs/web-unica-privada` e `feat/demo-publica-streamlit`. A branch experimental tem um `insights.py`, mas o `app.py` é igual ao da main e não o integra.

## Arquitetura encontrada

O código continua um monólito modular: `app.py` apresenta a interface Streamlit; `motor.py` calcula demanda, cobertura, reposição, tendência e ABC; `dados.py` valida e simula dados; `config.py` define políticas; `storage.py` persiste em SQLite. O módulo GestãoClick é um placeholder que lança `NotImplementedError`.

A separação é adequada ao tamanho do projeto. Os principais riscos eram contratos de entrada incompletos, casos de borda sem testes e diferença entre o MVP local e os requisitos de acesso web. Uma migração responsável deve preservar o motor e substituir a persistência/coordenação de acesso, sem reescrever toda a aplicação.

## Matriz de requisitos e evidências

| Requisito | Implementação/teste observado | Resultado |
| --- | --- | --- |
| Demanda, cobertura, reposição e risco no prazo | `motor.py`, `test_motor.py`, `test_motor_regressoes.py` | Corrigido e exercitado no CI |
| Tendência e ABC a custo | `motor.py`, regressões de janela, empate e custo ausente | Verificado; não é sazonalidade nem receita |
| CSV normalizado | `dados.py`, `test_importacao_seguranca.py` | Corrigido; datas, SKUs, limites e entradas inválidas testados |
| Persistência e histórico local | `storage.py`, `test_persistencia_regressoes.py` | SQLite testado; não equivale a persistência Supabase |
| Backup/restauração | Cópia exclusiva, integridade e leitura independente | Verificado com banco fictício |
| Painel, filtros, produto e decisões | `test_interface.py`, Streamlit AppTest | Verificado no runner de CI |
| Inicialização do servidor | `test_streamlit_http.py`, processo real e HTTP loopback | Verificado no CI; não é deploy |
| Login, Master e usuários desativados | Não há implementação de Auth | Não implementado; testes negativos não executáveis |
| RLS, permissões e isolamento por identidade | Não há esquema PostgreSQL de aplicação nem políticas | Não implementado; não aprovado |
| Restrição de custos por `finance.view` | Custos acessíveis pelo fluxo local sem identidade | Bloqueador de publicação |
| Flash rotativo | Não integrado na main | Não implementado |
| Relatórios mensais, fechamento e exportação autorizada | Nenhum módulo ou teste correspondente | Não implementado |
| Scheduler, destinatários, retry e idempotência | Nenhum componente correspondente | Não implementado; não apenas falta de SMTP |
| Aplicação publicada e mobile real | Nenhuma URL comprovada nas fontes acessíveis | Não verificado |

## Defeitos reproduzidos e correções

| Prioridade no MVP | Problema confirmado | Correção e regressão |
| --- | --- | --- |
| P1 | `2026-10-01` interpretada como 10 de janeiro | Datas ISO e brasileiras reconhecidas explicitamente |
| P1 | SKU `001` virava `1`; `NA` era perdido | Leitura textual de identidade |
| P1 | Snapshot zero apagava venda positiva do mesmo dia | Venda preservada e conflito sinalizado |
| P1 | Todos os dias de ruptura pareciam demanda zero confiável | Estado de demanda não estimável e aviso na UI/exportação |
| P1 | Linha de venda zero renovava última venda | Última venda usa apenas quantidade positiva |
| P1 | Custos infinitos, booleanos e datas futuras passavam em caminhos de entrada | Validação de tipos, finitude, inteiros e datas civis |
| P1 | CSV textual podia produzir fórmula em planilha | Exportação neutraliza prefixos perigosos sem converter números |
| P1 | Chamada de persistência com catálogo vazio apagava a base | Validação antes de abrir ou modificar o SQLite |
| P1 | Backup podia sobrescrever outro arquivo | Criação exclusiva, origem somente leitura e fechamento explícito |
| P1 | Consultas separadas podiam combinar importações concorrentes | Leitura em uma única transação; teste reproduz concorrência em WAL |
| P2 | Decisões aceitavam frações e truncavam observações | Contrato validado e rejeição explícita |
| P2 | Catálogo grande dependia do limite de parâmetros SQLite | Tabela temporária de códigos dentro da transação |
| P2 | Tolerância fixa apagava demanda positiva muito pequena | Arredondamento limitado à precisão de ponto flutuante |
| P2 | ABC mudava com a ordem de importação em empates | Desempate estável por SKU |
| P2 | Custos finitos extremos produziam capital infinito e ABC arbitrária | Indicadores fora do limite ficam não calculáveis com aviso; somas protegidas |
| P2 | Configurações inválidas e tendência com limiar zero | Validação e tratamento de estabilidade |
| P2 | Ruff/CI divergiam e falhavam | Mesma configuração completa, imports e nomes corrigidos |
| P2 | Exceções SQLite chegavam à interface sem orientação | Mensagens genéricas, teste de não exposição do detalhe técnico |

Os testes incluem rollback de falha no fim da importação, reimportação idempotente, preservação de snapshots dos SKUs presentes, SQL malicioso tratado como parâmetro, backup inválido e restauração em arquivo separado. A integração CSV → SQLite → motor → backup também foi executada diretamente no ambiente local.

## Cálculos e limites preservados

- Média: unidades observadas nos dias elegíveis divididas pelos dias elegíveis. Ausência de snapshot continua desconhecida e explicitamente sinalizada.
- Cobertura: estoque físico dividido pela demanda, indefinida se a demanda for zero.
- Ponto de pedido: teto da demanda durante o prazo mais segurança.
- Posição: estoque + pendentes − reservas.
- Alvo: teto da demanda pelo maior entre prazo e cobertura-alvo mais segurança.
- Sugestão: máximo entre zero e alvo − posição. Pendentes não são considerados entregues no alerta antes do prazo.
- Exemplo original preservado: estoque 35, demanda 8/dia, prazo 6, segurança 20, pendentes 10, reservas 5 e alvo 6 dias resultam em ponto 68, posição 40 e sugestão 28.
- Sem histórico não prova produto parado; sem dias elegíveis não prova demanda inexistente. O motor não estima vendas perdidas.

A primeira venda não informa necessariamente o início da operação. Não há metadado confiável de abrangência do CSV nem fechamento mensal. As importações continuam completas: SKUs removidos levam seus registros dependentes no SQLite. Esses limites precisam de tratamento na implementação web antes de relatórios históricos mensais.

## Segurança: verificado, ausente e não testado

**Bloqueadores P0 para a versão web:** o fluxo carrega dados sem login; todas as sessões apontam para `data/gerente.db`; consultas, importações e decisões não recebem identidade; custos são mostrados sem permissão financeira. São constatações do código, não prova de exploração de um serviço publicado.

Não seria válido testar senha errada, sessão expirada, refresh/logout, usuário desativado, promoção para Master, revogação em sessão ativa, IDOR/RLS ou restrição financeira usando mocks de um sistema inexistente e chamar isso de aprovação. Esses testes estão **não executáveis por ausência do componente**. Também não foi inventado um teste de relatório/retry de e-mail.

Verificações possíveis foram realizadas: entradas inválidas e limites de upload, SQL parametrizado, exportação de fórmulas, mensagens de erro e varredura de padrões conhecidos de segredos. Nenhum token/chave privada foi identificado nos 18 arquivos da baseline nem na nova varredura dos 25 arquivos da proposta. A varredura não cobre todo o histórico Git, secrets externos ou todo padrão possível. Não existe `unsafe_allow_html` na interface. `.gitignore` exclui bancos, CSV, backups, `.env` e Streamlit Secrets.

A configuração de desenvolvimento agora usa loopback, limita upload e oculta detalhes técnicos de exceções. Isso reduz exposição acidental; **não substitui Auth, RLS ou autorização**. Dependências diretas foram fixadas nas versões realmente instaladas pelo CI, com inventário das transitivas nos artefatos. O inventário não é um lockfile completo.

## Supabase conferido diretamente

Projeto existente: `csiqpzoudruzppfbpqqx`, `gerente-inteligente-estoque`, `sa-east-1`. Nenhum segundo projeto foi criado.

As consultas retornaram:

- Nenhuma tabela de aplicação no schema `public`.
- Nenhuma migração aplicada.
- Zero políticas em `public`.
- Advisors de segurança e desempenho sem alertas retornados.

**Advisors vazios em um banco sem aplicação não comprovam segurança de RLS.** Não havia persistência remota da aplicação para validar reinício, usuários ou endpoints autorizados. O banco não foi modificado. Não foram solicitadas nem impressas chaves administrativas.

## Execução e evidências

O CI anterior foi conferido nos logs da [execução 40635730309](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/40635730309): 11 testes passavam e Ruff falhava com dois imports sem uso e duas variáveis `l` ambíguas.

A primeira execução das correções, commit `90ed078`, [execução 40692779175](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/40692779175), executou **153 testes com sucesso em Python 3.11 e 3.12**. Identificou dois ajustes I001 nos testes antigos e dois FutureWarnings de pandas; ambos foram corrigidos para a rodada seguinte. O resultado final é registrado no PR #4 após a execução do commit mais recente.

Comandos do CI:

```bash
python -m pip install -r requirements.txt
python -m pytest -ra --junitxml=resultado-testes.xml
ruff check .
python -m pip check
```

Um job separado consulta vulnerabilidades conhecidas com `pip-audit`. Ele encontrou duas vulnerabilidades no ferramental pré-instalado do runner: `setuptools 79.0.1`, identificadores `PYSEC-2026-3447` (correção em 83.0.0) e `GHSA-xmj3-9gf7-h52w` (correção em 84.1.0). O CI passou a instalar explicitamente `setuptools==84.1.0`, sem ignorar alertas nem desativar a verificação. O pacote não é utilizado pelo código de estoque; a correção é no ambiente de construção/teste. Os resultados e versões instaladas são artefatos do GitHub Actions. O resultado da reexecução é registrado no PR.

No ambiente local, pandas/NumPy estavam disponíveis; Pytest, Ruff e Streamlit não estavam instalados, e a tentativa de instalação não encontrou distribuições disponíveis no acesso permitido. As reproduções diretas Python, compilação e `git diff --check` foram executados ali. A suíte Pytest, AppTest e o servidor HTTP foram realmente executados **no GitHub Actions**, não no ambiente local.

Não foi medida porcentagem de cobertura de linhas. A quantidade de testes inclui parametrizações. Não há validação visual em navegador móvel nem ponta a ponta com Supabase Auth.

## Publicação e participação do proprietário

Nenhuma URL pública validável apareceu no README ou no campo `homepage` do repositório. O endpoint de deployments foi recusado pelo conector como não suportado; portanto, não é possível afirmar que inexista algum deploy externo. Sem endereço comprovado e sem a implementação web, não foi alegada publicação nem validação de site remoto.

O trabalho restante da Issue #2 é **desenvolvimento pendente**, não uma configuração que o proprietário possa resolver apenas fornecendo secrets. A sequência necessária é:

1. Concluir a implementação web em branch própria: schema e migrações Supabase, Auth, Master, permissões/RLS e proteção financeira.
2. Migrar os fluxos e implementar insights e relatórios mensais com período confiável, autorização e simulação de e-mail.
3. Executar os testes negativos e a validação remota exigidos na Issue #3.
4. Só então configurar a conta autorizada do Streamlit, secrets no painel, cadastro público desligado e bootstrap Master por UUID, sem senha padrão nem segredo no chat.
5. Fornecer a URL real, se já existir outra implementação fora das referências examinadas, para reabrir a validação de publicação.
6. Configurar provedor/remetente e autorizar destinatários antes de qualquer e-mail real. Isso não autoriza dados comerciais nem integração GestãoClick.

O proprietário pode revisar o PR #4 sem mesclar automaticamente. As Issues #2 e #3 permanecem abertas. Este relatório comprova correções e testes do MVP, mas não declara o projeto web concluído ou seguro para dados empresariais.
