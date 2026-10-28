# 📦 Gerente Inteligente de Estoque

Criei este projeto para facilitar algumas tarefas de estoque no meu dia a dia. A ideia é reunir informações de produtos e vendas em uma tela simples e ajudar a entender o que está acabando, o que está parado e quanto pode ser necessário comprar.

É uma ferramenta pessoal, gratuita e ainda em desenvolvimento. Por enquanto, funciona no computador com dados fictícios ou arquivos CSV. Mais para frente, quero integrá-la ao GestãoClick, que continuará sendo o sistema oficial da empresa.

## Estado verificado na auditoria

A auditoria da Issue #3 encontrou e corrigiu falhas do MVP local. A versão web planejada na Issue #2 ainda não foi entregue: este código não tem login, Master, permissões, persistência Supabase nem relatórios mensais. Não use esta versão como aplicativo privado na internet.

As correções e as evidências estão no [PR #4](https://github.com/ruanviana6/gerente_inteligente_estoque/pull/4) e no [relatório da auditoria](docs/AUDITORIA_ISSUE_3.md). A revisão inclui testes de cálculo, CSV, SQLite, backup e interface Streamlit. Testes do MVP não comprovam os controles de segurança da futura versão web.

## Por que comecei

O GestãoClick já guarda informações de estoque, vendas e compras. O que eu queria era uma maneira mais prática de olhar para esses dados e decidir o que merece atenção.

Por exemplo, saber que um produto tem 14 unidades é útil, mas não me diz se ele vai durar até a próxima entrega. Quero conseguir consultar a cobertura em dias, o prazo do fornecedor e uma sugestão de reposição com o cálculo explicado.

O programa não faz pedidos sozinho. Ele organiza as informações para me ajudar a decidir.

## Como estou desenvolvendo

Estou construindo o projeto com ajuda de inteligência artificial, usando **Vibe Coding**. Eu trouxe o problema, defini os objetivos e as regras que quero seguir. A IA tem ajudado a estruturar o código, revisar soluções, criar testes e escrever a documentação.

Também compartilhei quatro arquivos de referência (`config.py`, `motor.py`, `dados.py` e `app.py`). As ideias úteis foram aproveitadas, e o restante foi reorganizado para facilitar a leitura e a manutenção.

Além de ser útil no trabalho, quero usar o projeto para praticar Python, SQL, análise de dados e boas práticas de programação. Como ainda está em desenvolvimento, toda sugestão de compra precisa ser conferida antes de ser usada de verdade.

## Funcionalidades do MVP

| Função | Estado |
| --- | --- |
| Painel com semáforo de risco e indicadores | Implementado |
| Lista de reposição por fornecedor e exportação CSV | Implementado |
| Detalhe do produto, gráfico de vendas e justificativa do cálculo | Implementado |
| Estoque parado, excesso e valor estimado imobilizado | Implementado |
| Comparação das médias de 30 e 90 dias e Curva ABC por consumo a custo | Implementado |
| Dados fictícios e reprodutíveis | Implementado |
| Importação CSV normalizado, SQLite e snapshots diários | Implementado |
| Histórico local de decisões (sem criar compra) | Implementado |
| Função de backup SQLite | Implementado, sem agendamento |
| API do GestãoClick | Planejada para após a conclusão e validação |
| Machine Learning, RFM, chat e expedição | Futuro |
| Integração com outro projeto de automação de NF-e | Futuro |

## Como funciona o cálculo

**Cobertura** = estoque físico ÷ média de venda diária (indefinida quando média é zero).

**Ponto de pedido** = teto(média diária × prazo de entrega + estoque de segurança).

**Posição de estoque** = estoque atual + pedidos pendentes − reservas.

**Estoque-alvo** = teto(média diária × maior entre prazo e cobertura desejada + segurança).

**Quantidade sugerida** = máximo(0, estoque-alvo − posição).

Exemplo fictício: 35 unidades, 8 vendas por dia, prazo 6 dias, segurança 20 unidades, 10 pendentes e 5 reservadas, cobertura-alvo de 6 dias: ponto de pedido 68, posição 40, sugestão **28 unidades**.

O semáforo usa dias de cobertura: ruptura com estoque zerado e demanda recente positiva, crítico até 7 dias, alto até 15, atenção até 30, OK até 90, excesso acima de 90 e parado quando a última venda registrada ocorreu há pelo menos 60 dias. O **risco antes da próxima entrega** é destacado separadamente: um produto pode estar OK por cobertura, mas ainda assim ter risco devido ao prazo.

**Limite importante:** dias em que o estoque comprovadamente esteve zerado são excluídos da média. Quando não há foto do estoque no dia, a disponibilidade é desconhecida. Isso não recupera demanda perdida nem prova que não houve ruptura. A tendência de 30 versus 90 dias não comprova sazonalidade e não utiliza ML.

## Arquitetura e estrutura

Separei o código em arquivos com responsabilidades diferentes para ficar mais fácil de entender, testar e manter.

- app.py: interface em Streamlit, filtros, tabelas e gráficos.
- config.py: valores padrão e políticas.
- dados.py: dados simulados, importação CSV e validação.
- motor.py: regras de cobertura, classificação, tendências, reposição e ABC.
- storage.py: banco SQLite, snapshots, histórico de decisões e backup.
- integracoes/gestaoclick.py: contrato reservado para a futura API, sem conexão ativa.
- tests/: testes automatizados de lógica, dados e persistência.
- docs/regras-de-negocio.md: premissas e limitações.
- requirements.txt: dependências gratuitas.

## Instalar e executar

Requer Python 3.11+.

~~~bash
git clone https://github.com/ruanviana6/gerente_inteligente_estoque.git
cd gerente_inteligente_estoque
python -m venv .venv
~~~

Ative o ambiente no Windows com o comando abaixo:

~~~powershell
.\.venv\Scripts\Activate.ps1
~~~

Em Linux/macOS: source .venv/bin/activate

~~~bash
python -m pip install -r requirements.txt
streamlit run app.py
~~~

O navegador abrirá o endereço local indicado pelo Streamlit (normalmente http://localhost:8501). Selecione **Demonstração fictícia** para explorar o projeto sem utilizar dados comerciais.

A configuração de desenvolvimento restringe o servidor a `127.0.0.1`. Isso reduz exposição acidental, mas não implementa autenticação. Não remova essa restrição para publicar o MVP como versão web privada.

### CSV (importação completa, não incremental)

Envie dois arquivos UTF-8 com campos normalizados:

**produtos.csv**: obrigatórios: codigo, nome, estoque. Opcionais: fornecedor, custo_unitario, prazo_entrega_dias, estoque_seguranca, pedidos_pendentes, reservas, cobertura_alvo_dias.

**vendas.csv**: obrigatórios: data, codigo, quantidade.

O separador padrão é ponto e vírgula, decimal com vírgula. O código agrega múltiplas vendas do mesmo SKU/dia. CSVs nativos do GestãoClick podem ter outros nomes: precisam de adaptação verificada, que ainda não foi implementada.

Datas devem usar `AAAA-MM-DD` ou `DD/MM/AAAA`, sem horário. Códigos como `001` e `NA` são preservados como texto. Cada arquivo aceita até 10 MiB e 200 mil linhas; números inválidos, datas futuras e cabeçalhos ambíguos são rejeitados. Textos perigosos nas exportações recebem um apóstrofo para impedir interpretação como fórmula em planilhas.

Uma nova importação substitui atomicamente o catálogo e as vendas importadas, evitando vendas duplicadas; registra uma fotografia do estoque atual e conserva fotos anteriores dos SKUs presentes. **Importe sempre catálogo e histórico completos.** Os registros permanecem somente em data/gerente.db, fora do repositório.

SKUs ausentes no catálogo completo são removidos, junto com seus snapshots e decisões locais. Confira a abrangência do arquivo antes de importar. O sistema ainda não identifica sozinho um CSV parcial que parece completo.

## Qualidade, segurança e limites

~~~bash
python -m pytest
ruff check .
~~~

- Nenhuma credencial é necessária no MVP.
- O programa **não acessa nem altera o GestãoClick**.
- Não publique CSVs, banco SQLite, backups, preços reais nem dados de clientes no GitHub.
- Este aplicativo local não possui login próprio: mantenha o computador protegido e não exponha a porta na internet.
- Use dados da empresa somente com autorização; respeite LGPD quando aplicável.
- A função de backup está disponível em storage.backup(), mas agendamento e restauração automatizados não foram construídos.
- O backup recusa um destino existente. A restauração foi exercitada abrindo uma cópia independente, sem substituir o banco em uso.
- Os avisos de qualidade distinguem ausência de histórico, snapshots conflitantes e demanda não estimável. Uma venda positiva é preservada mesmo quando o snapshot do dia informa saldo zero.
- A Curva ABC mede participação no consumo a custo, não receita; sem custos não informa valores inventados.
- Não existe ainda política para lote mínimo, múltiplos de embalagem, calendário de fornecedor, demanda perdida nem datas específicas de pedidos em trânsito.
- Previsão sazonal e ML são **evoluções futuras**, não funcionalidades concluídas.
- Comprar continua sendo uma decisão humana, após conferência de valores, fornecedores e disponibilidade.

## Próximas etapas

1. Concluir a Issue #2: aplicativo web único, Supabase, login, Master, permissões e RLS.
2. Implementar insights e relatórios mensais com dados confiáveis e envio inicialmente simulado.
3. Reexecutar a auditoria da Issue #3, incluindo os testes negativos de acesso e a URL verdadeira.
4. Publicar e validar a versão privada com dados fictícios, antes de qualquer uso empresarial autorizado.
5. Só após a validação e autorização, integrar a API GestãoClick **sempre somente por leitura, sem qualquer escrita no ERP**.
6. Estudar sazonalidade, ML e outras análises apenas quando houver histórico suficiente e benefício demonstrado.
7. Manter a automação de NF-e como projeto separado, sem antecipar integração fiscal.

## O que espero desse projeto

Quero ganhar tempo para acompanhar o estoque e planejar as reposições, enquanto aprendo e melhoro o código aos poucos.

