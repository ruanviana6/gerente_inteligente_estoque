# Gerente Inteligente de Estoque

Criei este projeto para entender melhor o estoque no dia a dia. Saber o saldo de um produto nem sempre é suficiente para decidir quando repor, quanto pedir ou o que merece atenção.

O gerente transforma catálogo, vendas e histórico de disponibilidade em indicadores explicáveis. O GestãoClick continua sendo a fonte operacional oficial da empresa. O conector analítico de consulta está preparado e desativado por padrão; ele nunca pode alterar dados no ERP. A preparação fiscal funciona exclusivamente em DEMO, por um executor separado. Somente o cadastro futuro da NF-e será permitido na loja Flagship - Flatter, por uma API única compartilhada com a origem FLT Comércio. A emissão é exclusivamente manual no ERP e nunca será executada pela automação.

O desenvolvimento acontece com apoio de IA e Vibe Coding: eu defini o problema e as necessidades da rotina, e a IA ajuda a escrever, revisar e testar o código. O objetivo é ter uma ferramenta útil e aprender Python, SQL, Engenharia de Software, dados e logística. As sugestões continuam dependendo de conferência humana.

## Versão web

A aplicação tem uma única entrada, **app.py**, em Streamlit, com Supabase Auth e PostgreSQL. O projeto Supabase utilizado é o já existente em São Paulo, csiqpzoudruzppfbpqqx. SQLite fica apenas como referência técnica do MVP e suporte aos seus testes de regressão; não armazena os dados da web.

A aplicação está em [estoqueinteligente-flatter.streamlit.app](https://estoqueinteligente-flatter.streamlit.app/), usando a branch **implementacao/web-supabase**. A versão web preserva as correções auditadas do MVP e reúne Central de Acessos, Reposição Inteligente, Inteligência Comercial e permanência do login, integradas pelos PRs #16, #17 e #18.

Na publicação anterior, em 11/11/2026, após o reinício confirmado pelo proprietário, o navegador verificou a versão **web-integrada-20261111.2** no domínio real. A tela de login foi conferida no computador e no celular, uma credencial fictícia inválida foi recusada e nenhum dado ficou exposto sem login. A validação não utilizou a conta Master nem dados empresariais. O [registro da publicação](docs/INTEGRACAO_WEB_20261111.md) separa essa evidência dos testes em ambiente descartável.

## Análises e integração preparada

A área **Inteligência Comercial** reúne Clientes, Cidades, Produtos Juntos e a análise de Vendas existente. Vendas usa `trends.view` e as novas visões usam suas permissões específicas. As análises leem a cópia autorizada no Supabase e não apresentam faturamento ou custos. Cestas exigem pedidos identificados e amostra mínima. Séries curtas mostram variação observada, sem prometer sazonalidade.

O Master pode preparar uma carga DEMO em **Administração → Integrações**, conferir a prévia e confirmar a persistência separadamente. O conector GestãoClick aceita apenas GET documentado, com OpenAPI e mapas de campos protegidos. Nenhuma consulta real ao ERP foi executada nesta entrega. Catálogo e saldo do motor continuam usando CSV: a ligação desses campos à API depende do contrato real, sem interpretar uma extração incremental como importação completa.

As dez migrações versionadas foram aplicadas ao projeto Supabase existente após backups cifrados e validação de preservação dos dados e da conta Master. Migrações, RLS e autorização também foram testadas em bancos descartáveis. Veja [ativação segura do conector](docs/ATIVACAO_GESTAOCLICK.md), [contrato](docs/CONTRATO_GESTAOCLICK.md), [análises de vendas](docs/INTELIGENCIA_VENDAS.md) e [Inteligência Comercial](docs/INTELIGENCIA_COMERCIAL.md). O conector real continua desativado.

## Interface consolidada e cadastro fiscal DEMO

A navegação reúne Visão Geral, Estoque, Reposição Inteligente, Inteligência Comercial, Automação / Notas Fiscais e Relatórios. Acompanhamento de Ações fica em Reposição. Usuários, regras, importação, integrações e auditoria ficam em Administração, conforme as permissões de cada pessoa.

A aba Automação / Notas Fiscais permite consultar pedidos fictícios, escolher manualmente frete e contribuinte, conferir a prévia e confirmar somente o cadastro da NF-e. Origem: FLT Comércio. Destino: Flagship - Flatter. O modelo usa uma API com lojas distintas. Em andamento exige aprovação identificada. Solicitações únicas, histórico e resultados ficam no Supabase. O executor Edge é isolado, apenas cadastra a nota DEMO e encerra o processo. A emissão é exclusivamente manual no ERP. Usa o modelo de uma API com lojas, sem criar venda ou pedido no varejo, baixar XML/PDF ou acessar a API real. Nenhum perfil nãoMaster recebe os novos acessos por padrão. Consulte [o fluxo DEMO e as permissões](docs/FISCAL_DEMO.md), [o registro da consolidação](docs/CONSOLIDACAO_FISCAL_20261111.md) e [o checklist da API](docs/CHECKLIST_ATIVACAO_API_GESTAOCLICK.md).

## O que o aplicativo faz

- Login por e-mail e senha, logout, renovação de sessão e fluxo de recuperação por código do Supabase.
- Conta Master criada por procedimento administrativo, sem senha padrão e sem promoção pela interface.
- Perfis Administrador, Operador e Visualizador, com concessões e revogações individuais. Desativação e permissões são verificadas no backend e no banco.
- Interface Flatter para computador e celular, com menu recolhível, cartões e prioridades que levam aos filtros correspondentes.
- Visão Geral com até seis indicadores, cinco prioridades e dois gráficos leves, lendo um resumo pré-calculado no Supabase. O Master atualiza os indicadores por ação explícita; mudanças no histórico ou parâmetros sinalizam desatualização.
- Tabelas paginadas e gráficos do produto carregados quando solicitados.
- Estoque, filtros por fornecedor, histórico de vendas e snapshots, detalhes do produto e memória do cálculo.
- Cobertura, risco durante o prazo do fornecedor, reposição, tendências e Curva ABC a custo quando autorizada.
- Importação CSV completa e transacional. Cada lote preserva sua versão do catálogo, vendas e custos; uma repetição idêntica não duplica o histórico.
- Parâmetros de reposição e registro de decisões internas, sem compras automáticas.
- Relatórios de mês fechado gerados por ação explícita, com comparação anterior quando há dados comparáveis.
- CSV preparado somente após o clique e nova autorização. O botão Salvar CSV entrega o arquivo já preparado, sem link remoto compartilhado entre sessões.
- Agendador separado do Streamlit, com simulação, destinatários controlados, histórico, limite de tentativas e proteção contra duplicidade. E-mail real exige configuração e autorização adicionais.

Custos ficam em tabela separada e exigem **finance.view**. O navegador não concede permissões por conta própria. As consultas comuns usam o JWT individual, sem chave administrativa. Usuários sem **inventory.view** acessam seus módulos pelo backend, protegido também por um segredo exclusivo da organização. Esse segredo fica no servidor e não concede permissões ao usuário. Sessões e dados não são compartilhados em cache global.

## Começar com segurança

Na implantação atual, a conta Master e os Secrets existentes foram preservados. Os passos de criação abaixo servem à instalação inicial; não recrie a conta nem troque os segredos para acessar a atualização.

O [guia de configuração e publicação](docs/CONFIGURACAO_WEB.md) detalha a instalação inicial:

1. Desligar cadastro público e acesso anônimo no Supabase Auth.
2. Criar sua conta e ativar o Master pelo UUID, no painel administrativo.
3. Gerar um segredo aleatório em um gerenciador de senhas, configurá-lo com `private.configure_backend_secret` e guardar o mesmo valor em `SUPABASE_BACKEND_SECRET` nos Secrets do Streamlit. O guia explica o procedimento protegido e os limites de 32 a 512 caracteres.
4. Configurar os demais Secrets e publicar a branch autorizada no Streamlit Community Cloud.
5. Validar com dados e usuários fictícios, incluindo as restrições de acesso.

Não cole credenciais no chat nem no GitHub. O arquivo .streamlit/secrets.example.toml contém somente o modelo. O segredo do backend é diferente de `REPORT_JOB_SECRET` e precisa ser configurado mesmo que o primeiro login Master funcione sem ele. Relatórios por e-mail têm [instruções separadas](docs/ATIVAR_RELATORIOS.md), porque não usam o SMTP do login automaticamente.

A aplicação foi pensada para uso dentro dos limites gratuitos. Não há contratação de serviço pago, garantia de disponibilidade contínua nem autorização implícita para dados empresariais.

## Importação e relatórios

Os arquivos são UTF-8, com separador ponto e vírgula e decimal com vírgula:

| Arquivo | Campos obrigatórios |
| --- | --- |
| produtos.csv | codigo, nome, estoque |
| vendas.csv | data, codigo, quantidade |

O catálogo aceita fornecedor, custo_unitario, prazo_entrega_dias, estoque_seguranca, pedidos_pendentes, reservas e cobertura_alvo_dias. Cada arquivo aceita até 10 MiB e 200 mil linhas. Datas usam AAAA-MM-DD ou DD/MM/AAAA. SKUs como 001 e NA permanecem texto. Valores inválidos, datas futuras, duplicidades ambíguas e referências desconhecidas são rejeitados.

Informe a data real do saldo e a abrangência completa do histórico. **Não importe um CSV parcial como se estivesse completo.** O sistema não consegue provar sozinho a abrangência declarada. Um relatório mensal exige saldo do último dia e vendas cobrindo todo o mês; não usa o saldo de hoje para representar um mês passado.

O banco também limita cada lista normalizada de produtos ou vendas a 20 MiB. Código aceita até 120 caracteres; nome e fornecedor, até 300. Esses limites podem ser atingidos antes dos limites do arquivo CSV.

Dias comprovadamente sem estoque não são tratados automaticamente como demanda zero. Dias sem snapshot permanecem desconhecidos e sinalizados. Venda positiva com snapshot zero é preservada e gera aviso de conflito. Custos ausentes não viram zero; unidades vendidas não são faturamento. As [regras de negócio](docs/regras-de-negocio.md) explicam as fórmulas e limites.

A exportação neutraliza textos que poderiam executar fórmulas em planilhas. Dados, backups e arquivos comerciais não devem ser versionados.

## Organização do código

| Arquivo ou diretório | Responsabilidade |
| --- | --- |
| app.py / web/ui.py | Entrada, sessão e navegação |
| web/pages.py / web/theme.py | Páginas, componentes e identidade Flatter |
| web/downloads.py | Entrega do CSV autorizado à página |
| web/telemetry.py | Tempos opcionais sem dados identificáveis |
| web/auth.py / web/api.py | Sessões, Auth e chamadas autorizadas |
| web/persistent_sessions.py / web/persistent_crypto.py | Retomada do navegador e cifra dos tokens no backend |
| web/service.py | Coordenação, validação e autorização por operação |
| reposicao/ | Planejamento de reposição e conversão por embalagem |
| analytics/commercial*.py | Indicadores comerciais, modelos e processos controlados |
| dados.py / motor.py / config.py | Validação e cálculos auditados |
| web/dashboard*.py | Resumo controlado e Visão Geral compacta |
| web/fiscal_*.py / fiscal/ | Interface, fachada e contrato fiscal DEMO |
| supabase/functions/fiscal-demo/ | Único executor fiscal operacional, exclusivamente DEMO |
| web/insights.py / web/reports.py | Insights e relatórios determinísticos |
| web/email_delivery.py | Fila, simulação e adaptador de envio opcional |
| supabase/migrations/ | Schema, funções, grants, RLS e histórico de migrações |
| scripts/run_monthly_reports.py | Agendador externo |
| scripts/verificar_publicacao.py | Conferência da versão e do acesso anônimo no domínio real |
| tests/ | Regressões, serviços, interface e segurança |
| tests/sql/ | Testes PostgreSQL com rollback |
| tests/live_supabase.py | Integração com Auth e PostgREST reais em ambiente descartável |

## Desenvolvimento e testes

Python 3.11 ou 3.12. A execução local serve ao desenvolvimento da mesma aplicação web:

~~~bash
python -m venv .venv
python -m pip install -r requirements.txt
python -m pytest
ruff check .
streamlit run app.py
~~~

Ative o ambiente virtual antes de instalar. Configure os Secrets localmente, fora do Git, para acessar o Supabase autorizado. Sem configuração, o aplicativo apresenta instruções e não oferece uma demonstração pública sem login.

O GitHub Actions executa Python 3.11/3.12, Ruff, auditoria de dependências, PostgreSQL 17, backup/restauração e integração local com Supabase. A versão integrada passou por 871 testes Python, 66 subtestes, sete testes de Auth/PostgREST reais e verificações de navegador e desempenho. As fontes e limitações estão no [registro da integração](docs/INTEGRACAO_WEB_20261111.md). As fixtures do MVP preservam testes anteriores; não são outra aplicação para operação diária.

## Interface e desempenho Flatter

A interface usa a logo Flatter como imagem e uma paleta clara com terracota e ameixa. A [documentação da interface](docs/UX_FLATTER.md) explica a organização das páginas. O [comparativo de desempenho](docs/DESEMPENHO_FLATTER.md) registra o método, os resultados e os limites das medições com dados fictícios. O motor foi otimizado preservando as regras de estoque; as correções e migrações posteriores estão no registro da integração.

## Limites e próximos passos

A versão integrada reúne a [Central de Acessos](docs/CENTRAL_ACESSOS.md), a
[Reposição Inteligente](docs/REPOSICAO_INTELIGENTE.md) e a Inteligência Comercial.
O [registro da integração](docs/INTEGRACAO_WEB_20261111.md) distingue código,
migrações, testes e validação no endereço publicado. A conta Master existente é
preservada. Usuários novos recebem acesso temporário e precisam definir sua
senha pessoal antes de consultar dados.

O [login pode ser lembrado neste navegador](docs/LOGIN_PERSISTENTE.md) por até
30 dias. O botão Sair encerra o acesso, e desativação ou redefinição continuam
bloqueando a retomada. Use a opção somente em um dispositivo pessoal.

A evolução da [Inteligência Comercial](docs/INTELIGENCIA_COMERCIAL.md) reúne clientes,
cidades e produtos comprados juntos em uma área. A interface consome resultados
pré-calculados. Os modelos locais são comparados a regras simples em validação
temporal; não há treino na navegação nem probabilidades inventadas de abandono.
Clientes e cidades reais aguardam o contrato autorizado da API e a avaliação de
privacidade. Há medição de ocupação restrita ao Master, resumos incrementais e
versionamento com reversão, sem exclusão automática de histórico.

Os fluxos autenticados foram testados com usuários fictícios e Supabase descartável. A conferência final com a conta real na URL publicada cabe ao proprietário, sem compartilhar senha ou sessão. O envio de e-mail permanece em simulação até autorização e configuração do provedor. O cadastro público deve ser desligado no painel Auth; os testes descartáveis não alteram essa configuração externa.

A versão web integrada está pronta para a auditoria independente da Issue #3. Dados empresariais só podem ser usados com autorização e validação apropriadas. A ativação real do GestãoClick depende das condições documentadas. Emissão automática de NF-e é permanentemente proibida. Compras automáticas, sincronização reversa e previsão sazonal avançada continuam bloqueadas. O fluxo fiscal DEMO é a preparação atual, não uma homologação da API.

Quero que o projeto economize tempo na rotina e continue simples de entender e manter, sem transformar uma necessidade pessoal em uma infraestrutura desnecessária.
