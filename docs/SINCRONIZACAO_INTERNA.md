# Cópia analítica de vendas e sincronização manual

O GestãoClick continua sendo a fonte operacional oficial. A implementação desta etapa recebe **linhas de vendas normalizadas** e mantém uma cópia analítica no Supabase. Não realiza baixas, estornos, mudanças de situação, atualizações de preço ou qualquer escrita no ERP. A sincronização de catálogo e saldo oficial para o motor de estoque depende do contrato de produtos da conta e permanece bloqueada nesta etapa. O estoque existente continua usando importação CSV validada.

## Componentes

- `integracoes/gestaoclick.py`: transporte GET, contrato OpenAPI e normalização explícita.
- `integracoes/runtime.py`: configuração protegida do servidor, sem rotas ou mapas recebidos da navegação.
- `sincronizacao/service.py`: autorização antes da leitura externa, validação, manifesto de idempotência e checkpoints.
- `web/integration_page.py`: ação manual do Master, prévia, confirmação e cancelamento.
- `web/sales_service.py`: consulta da cópia consolidada, sem chamada ao ERP durante navegação.
- `20261111004546_sincronizacao_vendas.sql`: estrutura, regras transacionais, RLS, grants e RPCs restritas.

O coordenador usa o JWT individual. Não necessita de `service_role`. Cada operação revalida papel Master, `integrations.manage`, conta, organização e geração da sessão antes e depois. O banco também confere a sessão ativa e o escopo fonte/loja. Antes de chamar o leitor externo, `sync_authorize_source` confirma que o UUID configurado no servidor pertence à organização da sessão e autoriza a loja solicitada.

## Modelo e integridade

| Tabela | Conteúdo |
| --- | --- |
| `integration_sources` | UUID, organização, chave interna, nome e lojas autorizadas; não contém credenciais |
| `sync_runs` | Execução, manifesto, período, reconciliação, concessão temporária e resultado |
| `sync_pages` | Número e hash das páginas recebidas, para repetição segura |
| `sync_staging` | Linhas validadas da execução ainda não consolidada |
| `sales_line_versions` | Versões imutáveis das linhas observadas |
| `sales_lines_current` | Ponteiro para a versão atual de cada documento/linha/fonte/loja |

Todas as seis tabelas têm RLS habilitada. `anon`, `authenticated` e `service_role` não recebem leitura ou DML direto dessas tabelas na migração. Os wrappers públicos são `SECURITY INVOKER`. Funções privadas que precisam transacionar a cópia são `SECURITY DEFINER`, com `search_path` vazio e checagem explícita de sessão, permissão e organização. A leitura de vendas exige `trends.view`; o contrato não armazena receita, custos, contatos ou documentos pessoais de clientes.

A chave operacional da linha é organização + fonte + loja + documento + linha. SKU e ID de produto não são considerados globalmente únicos. Duas lojas nunca são consolidadas implicitamente.

## Fases

1. Autorizar a fonte e a loja no banco, antes de qualquer GET externo.
2. Consultar todas as páginas permitidas e normalizar com o mapa conferido da conta.
3. Rejeitar dados fora do período, identificadores inválidos, quantidades não finitas, campos inesperados e documentos/linhas duplicados.
4. Iniciar ou retomar uma execução. A chave de repetição vincula período, contagem, soma líquida e hash SHA-256 do conteúdo canônico ordenado pelo cliente confiável.
5. Enviar páginas de até cem linhas ao staging. O banco exige sequência e conteúdo idêntico ao repetir uma página.
6. Gerar a prévia. A contagem de linhas e a soma `quantidade − devolução`, apenas para linhas concretizadas, precisam conferir com o manifesto.
7. O Master confere amostra, período e abrangência antes de confirmar.
8. Uma única transação cria novas versões, atualiza ponteiros, registra resultado e auditoria. Só então as telas analíticas enxergam as mudanças.

O hash do manifesto identifica uma repetição do conteúdo pelo cliente confiável; não é prova independente da abrangência comercial da origem. A reconciliação no banco valida o lote recebido. A conta ERP pode ocultar registros conforme usuário, loja e permissões mesmo retornando HTTP 200. A conferência com a origem continua obrigatória.

## Repetição, interrupção e concorrência

- A mesma chave com conteúdo, período, contagem ou soma diferentes é recusada.
- Uma página idêntica pode ser reenviada sem duplicar itens.
- Há no máximo uma execução ativa por fonte/loja. Advisory locks e locks de linha serializam tentativas concorrentes.
- A concessão vence após trinta minutos sem atividade. Retomar a mesma execução renova a concessão e invalida a anterior.
- A tentativa interrompida conserva páginas e staging. Uma retomada reconsulta e confere o conteúdo; não presume que a origem ficou estável.
- O Master pode cancelar uma execução ativa com sua concessão. Para uma execução expirada ou interrompida, pode usar `sync_cancel(run_id, null)` para liberar staging abandonado, sempre na própria organização.
- Cancelar staging não exclui nenhuma venda consolidada nem versão histórica.
- Confirmar a mesma execução duas vezes retorna o resultado já confirmado, sem uma segunda versão.

A primeira versão reconsulta períodos completos autorizados. Não usa filtros incrementais de modificação sem comprovação no OpenAPI da conta. Linhas ausentes em uma consulta posterior não são apagadas: podem estar ocultas ou fora do escopo. Se o Master declarar cobertura completa, a transação também reconcilia a contagem e a soma líquida da cópia consolidada no período com a extração. Divergência, inclusive resposta vazia com linhas antigas preservadas, recusa toda a promoção. Uma carga parcial explicitamente não conferida conserva linhas ausentes e não ganha uma nova declaração de cobertura. Cancelamento e devolução precisam vir explicitamente na normalização.

Uma linha alterada já existente só substitui a versão atual quando há `source_updated_at` posterior comprovado. Sem horário comparável, a mudança é recusada para evitar ressuscitar cancelamentos ou sobrescrever correções com respostas antigas. Isso é uma limitação deliberada que pode exigir reconciliação da conta; não se inventa um horário de alteração da origem.

## Cobertura e análises

Uma execução confirmada pelo Master pode declarar a abrangência conferida. `sales_payload` une apenas intervalos confirmados, concluídos e contínuos da mesma organização/fonte/loja. Intervalos adjacentes ou sobrepostos compõem uma ilha. Um único dia ausente impede declarar cobertura integral entre as pontas. Execuções não conferidas ou de outra loja não preenchem essa lacuna.

Essa união permite construir histórico em cargas menores e depois analisar períodos maiores. Ainda não prova disponibilidade do produto em todos os dias, ausência de filtros invisíveis do ERP, nem comparabilidade comercial. O módulo analítico mostra as limitações e não transforma variação curta em sazonalidade comprovada.

## Limites conservadores

- Até dez fontes por organização, trinta lojas por fonte.
- Até vinte mil linhas por execução e no staging simultâneo da organização.
- Até cem linhas e 1 MiB por página; até mil páginas numeradas.
- Até cem mil linhas atuais e duzentas mil versões por organização.
- Até mil execuções registradas e vinte não encerradas por organização.
- Resposta de uma consulta analítica limitada a vinte mil linhas; reduza o período se necessário.

São barreiras de software, não garantia de permanecer abaixo dos 500 MB do Supabase Free: índices, auditoria, outros módulos e espaço recuperável também consomem armazenamento. Staging é removido após confirmação ou cancelamento, enquanto as versões definitivas permanecem. Os limites de versões são conservadores e podem impedir uma reextração perto da capacidade, mesmo sem mudanças. Não há limpeza automática do histórico. Antes de ampliar limites ou criar retenção, confira a ocupação e faça backup privado validado. Não contratar plano pago sem autorização.

## Validação reproduzível

```bash
python -m pytest tests/test_sync_service.py tests/test_revisao_seguranca.py
```

No PostgreSQL descartável, após `tests/sql/bootstrap.sql` e todas as migrações:

```bash
psql -v ON_ERROR_STOP=1 -f tests/sql/sync.sql
GERENTE_DISPOSABLE_DATABASE=1 python tests/sql/sync_concorrencia.py
```

O teste de concorrência recusa um schema Auth diferente do fixture mínimo. Ele disputa criação de execução, envio da mesma página e confirmação em conexões reais separadas. Esses comandos não devem ser apontados para o Supabase produtivo. Testes descartáveis não comprovam conectividade, permissões ou reconciliação da conta real do GestãoClick.

A migração é transacional e não altera as tabelas legadas de estoque. Para interromper a ativação, mantenha `GESTAOCLICK_ENABLED=false` e não execute sincronizações. Uma reversão operacional pode voltar o código web preservando as tabelas e versões novas. Remover estruturas/histórico exige avaliação própria e backup; não há rollback destrutivo automático.
