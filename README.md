# Gerente Inteligente de Estoque

Criei este projeto para entender melhor o estoque no dia a dia. Saber o saldo de um produto nem sempre é suficiente para decidir quando repor, quanto pedir ou o que merece atenção.

O gerente transforma catálogo, vendas e histórico de disponibilidade em indicadores explicáveis. O GestãoClick continua sendo a fonte operacional oficial da empresa. **Esta versão não se conecta à sua API e nunca deve alterar dados no ERP, inclusive em uma integração futura.**

O desenvolvimento acontece com apoio de IA e Vibe Coding: eu defini o problema e as necessidades da rotina, e a IA ajuda a escrever, revisar e testar o código. O objetivo é ter uma ferramenta útil e aprender Python, SQL, Engenharia de Software, dados e logística. As sugestões continuam dependendo de conferência humana.

## Versão web

A aplicação tem uma única entrada, **app.py**, em Streamlit, com Supabase Auth e PostgreSQL. O projeto Supabase utilizado é o já existente em São Paulo, csiqpzoudruzppfbpqqx. SQLite fica apenas como referência técnica do MVP e suporte aos seus testes de regressão; não armazena os dados da web.

O código da versão web está no [PR #5](https://github.com/ruanviana6/gerente_inteligente_estoque/pull/5), incorporando as correções auditadas do PR #4. **A publicação e o login com a conta do proprietário dependem da configuração protegida descrita no guia. Não há URL publicada declarada como validada neste README.**

## O que o aplicativo faz

- Login por e-mail e senha, logout, renovação de sessão e fluxo de recuperação por código do Supabase.
- Conta Master criada por procedimento administrativo, sem senha padrão e sem promoção pela interface.
- Perfis Administrador, Operador e Visualizador, com concessões e revogações individuais. Desativação e permissões são verificadas no backend e no banco.
- Painel com indicadores, avisos de qualidade e flash rotativo de insights calculados.
- Estoque, filtros por fornecedor, histórico de vendas e snapshots, detalhes do produto e memória do cálculo.
- Cobertura, risco durante o prazo do fornecedor, reposição, tendências e Curva ABC a custo quando autorizada.
- Importação CSV completa e transacional. Cada lote preserva sua versão do catálogo, vendas e custos; uma repetição idêntica não duplica o histórico.
- Parâmetros de reposição e registro de decisões internas, sem compras automáticas.
- Relatórios de mês fechado, visualização e exportação autorizadas, com comparação anterior quando há dados comparáveis.
- Agendador separado do Streamlit, com simulação, destinatários controlados, histórico, limite de tentativas e proteção contra duplicidade. E-mail real exige configuração e autorização adicionais.

Custos ficam em tabela separada e exigem **finance.view**. O navegador não concede permissões por conta própria. As consultas comuns usam o JWT individual, sem chave administrativa. Sessões e dados não são compartilhados em cache global.

## Começar com segurança

Siga o [guia de configuração e publicação](docs/CONFIGURACAO_WEB.md). Ele detalha:

1. Desligar cadastro público e acesso anônimo no Supabase Auth.
2. Criar sua conta e ativar o Master pelo UUID, no painel administrativo.
3. Configurar Streamlit Secrets e publicar a branch autorizada no Streamlit Community Cloud.
4. Validar com dados e usuários fictícios, incluindo as restrições de acesso.

Não cole credenciais no chat nem no GitHub. O arquivo .streamlit/secrets.example.toml contém somente o modelo. Relatórios por e-mail têm [instruções separadas](docs/ATIVAR_RELATORIOS.md), porque não usam o SMTP do login automaticamente.

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
| app.py / web/ui.py | Entrada única e interface |
| web/auth.py / web/api.py | Sessões, Auth e chamadas autorizadas |
| web/service.py | Coordenação, validação e autorização por operação |
| dados.py / motor.py / config.py | Validação e cálculos auditados |
| web/insights.py / web/reports.py | Insights e relatórios determinísticos |
| web/email_delivery.py | Fila, simulação e adaptador de envio opcional |
| supabase/migrations/ | Schema, funções, grants, RLS e histórico de migrações |
| scripts/run_monthly_reports.py | Agendador externo |
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

O GitHub Actions executa Python 3.11/3.12, Ruff, auditoria de dependências, PostgreSQL 17, backup/restauração e integração local com Supabase. Os resultados comprovados, as limitações e as pendências estão no [registro da entrega](docs/ENTREGA_WEB.md) e nos checks do PR #5. As fixtures do MVP preservam testes anteriores; não são outra aplicação para operação diária.

## Limites e próximos passos

A implantação ainda precisa ser verificada pela URL real. O envio de e-mail permanece em simulação até autorização e configuração do provedor. O cadastro público deve ser desligado no painel Auth; os testes do ambiente descartável não alteram automaticamente essa configuração externa.

Depois da implementação e da ativação, será feita a auditoria independente da Issue #3. Dados empresariais só podem ser usados com autorização e validação apropriadas. GestãoClick, NF-e, compras automáticas, associação de produtos e previsão sazonal avançada continuam fora desta etapa.

Quero que o projeto economize tempo na rotina e continue simples de entender e manter, sem transformar uma necessidade pessoal em uma infraestrutura desnecessária.
