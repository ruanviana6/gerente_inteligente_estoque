# 📦 Gerente Inteligente de Estoque

**Uma camada pessoal de análise e decisão sobre os dados de estoque — não um substituto do GestãoClick.**

Projeto gratuito e local, construído com Python, Streamlit, pandas e SQLite. O objetivo é facilitar a rotina de trabalho com produtos, vendas, fornecedores e reposição.

## Problema que motivou o projeto

O ERP informa: **Produto X tem 14 unidades**. Porém, isso não responde se esse saldo dura até a próxima entrega, se é necessário comprar hoje, quanto solicitar, nem quais produtos estão parados e imobilizando capital.

Este projeto nasceu para transformar dados em recomendações transparentes: **o que está em risco, o que pedir, quanto pedir e por quê**. É uma ferramenta de uso próprio para apoiar decisões dentro de uma empresa, sem pretensão de virar uma plataforma comercial.

## Desenvolvimento com Vibe Coding e IA

O desenvolvimento usa **Vibe Coding de forma declarada e transparente**. O idealizador definiu a necessidade, os problemas reais e as regras prioritárias. A IA foi utilizada para ajudar a planejar a arquitetura, revisar quatro arquivos iniciais (config.py, motor.py, dados.py e app.py), escrever e reorganizar código, comentar as funções, criar testes e redigir a documentação.

A abordagem não consiste em confiar cegamente em código gerado por IA. A implementação foi estruturada em componentes com responsabilidades distintas e testes para proteger cálculos essenciais. **As sugestões ainda precisam de validação humana antes de compras reais.** O objetivo é aprender Engenharia de Software, Clean Code, SQL, segurança básica, logística e análise de dados por meio de um problema concreto.

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

O programa funciona como **monólito modular** para evitar complexidade desnecessária.

- app.py — interface em Streamlit, filtros, tabelas e gráficos.
- config.py — valores padrão e políticas.
- dados.py — dados simulados, importação CSV e validação.
- motor.py — regras de cobertura, classificação, tendências, reposição e ABC.
- storage.py — banco SQLite, snapshots, histórico de decisões e backup.
- integracoes/gestaoclick.py — contrato reservado para a futura API, sem conexão ativa.
- tests/ — testes automatizados de lógica, dados e persistência.
- docs/regras-de-negocio.md — premissas e limitações.
- requirements.txt — dependências gratuitas.

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

### CSV (importação completa, não incremental)

Envie dois arquivos UTF-8 com campos normalizados:

**produtos.csv** — obrigatórios: codigo, nome, estoque. Opcionais: fornecedor, custo_unitario, prazo_entrega_dias, estoque_seguranca, pedidos_pendentes, reservas, cobertura_alvo_dias.

**vendas.csv** — obrigatórios: data, codigo, quantidade.

O separador padrão é ponto e vírgula, decimal com vírgula. O código agrega múltiplas vendas do mesmo SKU/dia. CSVs nativos do GestãoClick podem ter outros nomes: precisam de adaptação verificada, que ainda não foi implementada.

Uma nova importação substitui atomicamente o catálogo e as vendas importadas, evitando vendas duplicadas; registra uma fotografia do estoque atual e conserva fotos anteriores dos SKUs presentes. **Importe sempre catálogo e histórico completos.** Os registros permanecem somente em data/gerente.db, fora do repositório.

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
- A Curva ABC mede participação no consumo a custo, não receita; sem custos não informa valores inventados.
- Não existe ainda política para lote mínimo, múltiplos de embalagem, calendário de fornecedor, demanda perdida nem datas específicas de pedidos em trânsito.
- Previsão sazonal e ML são **evoluções futuras**, não funcionalidades concluídas.
- Comprar continua sendo uma decisão humana, após conferência de valores, fornecedores e disponibilidade.

## Próximas etapas

1. Validar indicadores e cálculos com dados simulados e casos de borda.
2. Conferir formatos de exportação CSV autorizados e reconciliação do estoque.
3. Concluir testes operacionais da ferramenta independente.
4. Integrar a API oficial do GestãoClick em **modo leitura**, com permissões e limites verificados.
5. Estudar sazonalidade e comparar modelos ML com baseline simples usando validação temporal.
6. Avaliar indicadores de clientes (RFM), chat numérico auditável e expedição.
7. Futuramente integrar com o projeto separado de automação de NF-e, sem misturar lógica fiscal à lógica de compras.

## Propósito

**Transformar informação em decisão**, usando uma aplicação simples, explicável e de custo zero, enquanto pratico Engenharia de Software, Python, SQL, dados, logística e desenvolvimento assistido por inteligência artificial.
