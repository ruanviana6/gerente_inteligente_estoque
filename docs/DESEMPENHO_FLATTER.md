# Desempenho e validação da Issue #6

## Escopo e método

A base comparada é o commit `5f4856384167d7100dfedc0f5d4fcefa94f28c03`, da branch publicada `implementacao/web-supabase`. A candidata fica em `interface/flatter-desempenho`. Nenhuma publicação, alteração de Secrets, migração produtiva ou integração com GestãoClick faz parte desta entrega.

O diagnóstico começou pela base intacta, antes das otimizações. `scripts/benchmark_flatter.py` seleciona o checkout antes de importar o código; usa AuthService, Service, cálculo e, no modo `ui`, a interface verdadeira via AppTest. Somente o transporte é fictício. A amostra determinística contém 100 e 1.200 SKUs, 90 dias declarados, vendas, snapshots esparsos, rupturas, produtos sem vendas e custos ausentes.

Cada cenário repete cinco vezes e informa amostras brutas, mediana (p50), p95 interpolado, chamadas HTTP e etapas de cálculo. Cinco amostras servem à comparação controlada, não a uma estimativa robusta de disponibilidade operacional. Primeira carga aparece separada. Os tempos de etapas são inclusivos e não devem ser somados. A medida `backend_total_sem_sobreposicao` calcula a união das fronteiras externas de serviços. O residual `execucao_UI_fora_servicos` subtrai essa união do tempo do AppTest e inclui montagem de widgets, serialização e coordenação do framework; não representa pintura ou interação no navegador.

O CI compara ambas as versões no mesmo runner Ubuntu, Python 3.12 e dependências de `requirements.txt`, simulando 40 ms por requisição. Isso permite medir o custo das chamadas sequenciais, mas **não mede a rede, o banco hospedado nem a latência da aplicação publicada**. Autenticação e RLS reais são verificadas em outro job, com Supabase descartável.

A navegação para Relatórios agora apenas oferece as ações. O benchmark registra separadamente o clique em **Gerar relatório**, a geração mensal completa e a geração efetiva dos CSVs, para não confundir adiamento de trabalho com eliminação de seu custo. O tempo do CSV cobre geração e autorização, não transferência pela Internet.

## Diagnóstico e mudanças

- A carga simples original fazia três GET Auth, três `app_context`, uma leitura de estoque e um cálculo do motor. A candidata valida GET Auth uma vez por execução e mantém as três consultas atuais de permissão, além de RLS e `auth.sessions` no banco. Não há cache de permissões ou dados entre execuções.
- O painel original carregava e calculava a mesma base outra vez para o flash. A candidata gera insights a partir do painel já autorizado, com avanço manual. O monitor de revogação permanece a cada 20 segundos, sem recarregar estoque; sua chamada inicial aproveita a validação do rerun.
- Reposição não gera CSV ao visitar a página. Relatórios não consultam meses antes da ação explícita. Gráficos do produto e de tendências são opcionais. As tabelas enviam 50, 100 ou 250 registros por página; isso limita renderização, não a consulta total nem os indicadores.
- O perfil de CPU local atribuiu 13,558 s de uma execução instrumentada de 15,708 s às duas chamadas de `medias_por_produto`: 2.400 construções de MultiIndex e 4.817 reindexações. O cálculo agora agrupa o mesmo conjunto de dias em blocos limitados a aproximadamente 100 mil células, em vez de recriar índices por SKU. As fórmulas e critérios de elegibilidade permanecem iguais.
- Testes diferenciais com uma referência congelada da base comparam DataFrames exatamente, incluindo 60 amostras aleatórias, painel completo de 1.100 SKUs, ruptura contínua, ausência de histórico, limites numéricos e preservação das entradas.
- O opener HTTP passa a pertencer ao cliente exclusivo da sessão. Preserva proxy, TLS, bloqueio de redirects, timeout e limite de resposta. Não há cookies, headers de autenticação persistentes ou pool de conexões. O ganho medido é preparação local do transporte, não redução comprovada do handshake de rede.

## Resultados verificados no GitHub Actions

Medição do commit `63f7fe7e27cc281d716fd099b2c17ec5edabdf2a`, em 29/10/2026: [workflow de desempenho e navegador](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/40913861264). Ambiente: Ubuntu do runner, Python 3.12.15, pandas 2.3.3, NumPy 2.4.6 e Streamlit 1.66.1. Latência artificial de 40 ms por requisição. Os JSON completos estão em [antes](evidencias_flatter/antes.json) e [depois](evidencias_flatter/depois.json).

| SKUs | Página | p50 antes | p50 depois | p95 antes | p95 depois |
| --- | --- | ---: | ---: | ---: | ---: |
| 100 | Painel | 1,460 s | 0,309 s | 1,518 s | 0,344 s |
| 100 | Estoque | 0,876 s | 0,304 s | 0,887 s | 0,305 s |
| 100 | Reposição | 1,617 s | 0,305 s | 1,649 s | 0,307 s |
| 100 | Relatórios | 1,888 s | 0,088 s | 1,929 s | 0,088 s |
| 1200 | Painel | 9,576 s | 0,711 s | 9,881 s | 0,751 s |
| 1200 | Estoque | 4,881 s | 0,702 s | 5,067 s | 0,760 s |
| 1200 | Reposição | 9,895 s | 0,728 s | 10,437 s | 0,751 s |
| 1200 | Relatórios | 10,907 s | 0,088 s | 11,191 s | 0,088 s |

Relatórios nesta tabela mede a entrada na página. Antes, ela gerava o fechamento e o CSV automaticamente; agora, aguarda uma ação. O clique real em **Gerar relatório**, incluindo interface e autorização, levou p50/p95 de **0,482/0,485 s** com 100 SKUs e **1,259/1,304 s** com 1.200 SKUs.

| Ação completa no backend, 1.200 SKUs | p50 antes | p50 depois | p95 antes | p95 depois |
| --- | ---: | ---: | ---: | ---: |
| Gerar relatório mensal | 4,844 s | 1,189 s | 4,890 s | 1,200 s |
| Gerar CSV mensal | 5,050 s | 1,450 s | 5,095 s | 1,452 s |
| Gerar CSV de reposição | 4,559 s | 0,888 s | 4,621 s | 0,923 s |

| Página | Requisições HTTP antes → depois | Cálculos do motor antes → depois |
| --- | --- | --- |
| Painel | 16 → 5 | 2 → 1 |
| Estoque | 9 → 5 | 1 → 1 |
| Reposição | 20 → 5 | 2 → 1 |
| Entrada em Relatórios | 24 → 2 | 2 → 0, cálculo sob demanda |

No Painel, as cinco requisições atuais são um GET Auth, três `app_context` e um `load_inventory`. No CSV mensal há oito verificações atuais de contexto e duas confirmações Auth: reduzir o número total não justificaria eliminar a revalidação sensível.

Com 1.200 SKUs, o custo mediano residual de UI do Estoque caiu de **0,121 s para 0,021 s**, e o backend de **4,759 s para 0,682 s**. No Painel, o residual ficou em **0,023 s antes e 0,029 s depois**, enquanto o backend caiu de **9,552 s para 0,683 s**. O ganho principal vem da redução de cálculos e consultas repetidas; a nova apresentação não é apresentada como redução universal do tempo de desenho.

A primeira renderização com 100 SKUs caiu de **2,005 s para 0,814 s**. Com 1.200, de **9,823 s para 0,841 s**. A segunda amostra começa em uma sessão nova, mas com o processo já aquecido pela primeira; esses valores não medem partida a frio do Community Cloud.

### Testes e revisão efetivamente executados

- [Workflow Python e banco](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/40913861416): **466 testes e 66 subtestes aprovados em Python 3.11 e 3.12**, Ruff e `pip check` aprovados; auditoria de dependências aprovada.
- Auth/PostgREST reais: cinco testes em Supabase descartável, sem acesso ao projeto produtivo.
- PostgreSQL/RLS: 101 verificações, repetidas após backup/restauração, e teste de vínculo concorrente de usuário.
- Chromium: 12 capturas em 1440/390 px, incluindo login, painel, estoque, detalhe do produto, relatórios e administração. Sem rolagem horizontal da página nas capturas; ações principais com alvos mínimos de 44 px. A contagem geral de controles pequenos inclui componentes nativos e não equivale a certificação integral WCAG.
- Fluxos reais de navegador com transporte fictício: quatro perfis, CSV gerado e baixado, negação após revogação, remoção de valores financeiros em tela aberta, desativação sem interação e isolamento de logout entre sessões.
- Uma revisão independente reproduziu e motivou correções de troca concorrente de identidade, permissão alterada durante carga e entrega global de downloads. O teste de alteração de mês antes do CSV também foi incorporado.

Os testes locais completos de Pytest/Ruff/Streamlit não estavam disponíveis por falta de dependências e acesso de rede no executor. A suíte completa foi efetivamente instalada e executada no GitHub Actions; localmente também foram executados os testes independentes com `unittest`, compilação e verificação de diff.

A abertura anônima da URL publicada retornou HTTP 200, mas **não foi observado o formulário de login**. Não houve autenticação, alteração de configuração nem operação no ambiente publicado. Esse resultado não comprova o funcionamento autenticado do site. As capturas do artifact são da candidata local com dados fictícios.

## Medição local inicial

Ambiente compartilhado: Python 3.12.14, pandas 2.2.3 e NumPy 2.3.5. Transporte fictício sem latência adicionada. Cinco amostras do backend completo (`context` + `load_inventory`). As versões são diferentes das dependências fixadas usadas no CI, portanto não se misturam os dois conjuntos de tempos.

| SKUs | p50 antes | p50 depois | p95 antes | p95 depois |
| --- | ---: | ---: | ---: | ---: |
| 100 | 0,489 s | 0,124 s | 0,676 s | 0,146 s |
| 1.200 | 9,309 s | 0,550 s | 12,966 s | 0,668 s |

Os números foram registrados após a primeira otimização, antes do endurecimento adicional contra troca concorrente de sessão. A medição final da candidata deve ser consultada nas evidências do CI e no PR.

Em 100 repetições do transporte HTTP fictício, preparar um opener em cada chamada custou p50 de 10,669 ms e p95 de 34,722 ms. Reutilizá-lo por cliente custou 0,039 ms e 0,212 ms, respectivamente; a primeira preparação custou 8,484 ms. O script `scripts/benchmark_http_flatter.py` reproduz essa comparação sem conexão externa.

## Como reproduzir

Em um checkout da candidata, com as dependências instaladas:

```bash
mkdir -p /tmp/flatter-base
git archive 5f4856384167d7100dfedc0f5d4fcefa94f28c03 | tar -x -C /tmp/flatter-base
python scripts/benchmark_flatter.py --repository /tmp/flatter-base --mode ui --sizes 100 1200 --iterations 5 --latency-ms 40 --output /tmp/antes.json
python scripts/benchmark_flatter.py --repository . --mode ui --sizes 100 1200 --iterations 5 --latency-ms 40 --output /tmp/depois.json
python scripts/benchmark_http_flatter.py --iterations 100 --output /tmp/http.json
python -m pytest
ruff check .
```

Para executar a validação visual, instale Playwright 1.62.0 e seu Chromium no ambiente de teste e rode:

```bash
python scripts/validar_interface_flatter.py --output /tmp/interface-flatter --strict-layout
```

A fixture só inicia em loopback, exige um marcador de teste e nunca é importada por `app.py`. Os dados e usuários são fictícios. O workflow `flatter.yml` faz essas etapas e guarda JSON, versões e screenshots como artifacts.

## Instrumentação operacional

`GERENTE_PERFORMANCE_LOG=true` habilita tempos sanitizados de bootstrap/rerun, Auth, `app_context`, leitura, motor, renderização e exportação. O padrão é desligado. O log contém apenas nomes de etapas predefinidos, contagens e durações; não registra e-mails, UUIDs, JWTs, URLs, filtros, tabelas ou valores financeiros. Escopos independentes não compartilham métricas de usuários. O tempo de renderização é do servidor, não de pintura do navegador.

## Segurança e limites restantes

O devcontainer deixa de desativar CORS/XSRF e instala somente as versões fixadas em `requirements.txt`. Isso corrige a configuração de desenvolvimento; não reconfigura a aplicação publicada.

O lock é por sessão e mantém a identidade estável durante consulta, cálculo e apresentação. O contexto pós-consulta é comparado antes de qualquer dado ou metadado ser exibido. Mudança de organização ou permissão interrompe a apresentação. A exportação verifica Auth e permissões antes e depois da geração e usa entrega direta à página, sem callback global de download. Consulte [a documentação da interface](UX_FLATTER.md).

Dados já enviados ao navegador não podem ser apagados retroativamente. Revogação bloqueia novas operações e o monitor remove a tela aberta na próxima verificação. Não existe cache persistente de catálogos ou relatórios. Não foram alterados migrações, grants ou políticas RLS produtivas.

Partida a frio do Streamlit Community Cloud, distância de rede, carga do Supabase e tamanho total do histórico podem dominar a latência real. A consulta ainda retorna a base necessária ao cálculo. Antes de adotar agregações no banco ou cache por versão, medir esses fatores em contas fictícias autorizadas. A meta orientativa de dois segundos não é uma garantia de produção.

A URL publicada é `https://estoqueinteligente-flatter.streamlit.app/`. A candidata não foi publicada. Uma verificação anônima do endereço, quando disponível no artifact, não comprova login, permissões ou tempo de resposta autenticado. Nenhuma conta empresarial ou credencial real foi usada.

Após revisão deste PR, solicitar a auditoria independente da Issue #3 antes de autorizar dados empresariais. Merge e publicação dependem do proprietário.
