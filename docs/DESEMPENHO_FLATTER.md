# Desempenho e validação da Issue #6

## Escopo e método

A base comparada é o commit `5f4856384167d7100dfedc0f5d4fcefa94f28c03`, da branch publicada `implementacao/web-supabase`. A candidata fica em `interface/flatter-desempenho`. Nenhuma publicação, alteração de Secrets, migração produtiva ou integração com GestãoClick faz parte desta entrega.

O diagnóstico começou pela base intacta, antes das otimizações. `scripts/benchmark_flatter.py` seleciona o checkout antes de importar o código; usa AuthService, Service, cálculo e, no modo `ui`, a interface verdadeira via AppTest. Somente o transporte é fictício. A amostra determinística contém 100 e 1.200 SKUs, 90 dias declarados, vendas, snapshots esparsos, rupturas, produtos sem vendas e custos ausentes.

Cada cenário repete cinco vezes e informa amostras brutas, mediana (p50), p95 interpolado, chamadas HTTP e etapas de cálculo. Cinco amostras servem à comparação controlada, não a uma estimativa robusta de disponibilidade operacional. Primeira carga aparece separada. Os tempos de etapas são inclusivos e não devem ser somados.

O CI compara ambas as versões no mesmo runner Ubuntu, Python 3.12 e dependências de `requirements.txt`, simulando 40 ms por requisição. Isso permite medir o custo das chamadas sequenciais, mas **não mede a rede, o banco hospedado nem a latência da aplicação publicada**. Autenticação e RLS reais são verificadas em outro job, com Supabase descartável.

A navegação para Relatórios agora apenas oferece as ações. O benchmark registra separadamente o clique em **Gerar relatório**, a geração mensal completa e a geração efetiva dos CSVs, para não confundir adiamento de trabalho com eliminação de seu custo. O tempo do CSV cobre geração e autorização, não transferência pela Internet.

## Diagnóstico e mudanças

- A carga simples original fazia três GET Auth, três `app_context`, uma leitura de estoque e um cálculo do motor. A candidata valida GET Auth uma vez por execução e mantém as três consultas atuais de permissão, além de RLS e `auth.sessions` no banco. Não há cache de permissões ou dados entre execuções.
- O painel original carregava e calculava a mesma base outra vez para o flash. A candidata gera insights a partir do painel já autorizado, com avanço manual. O monitor de revogação permanece a cada 20 segundos, sem recarregar estoque; sua chamada inicial aproveita a validação do rerun.
- Reposição não gera CSV ao visitar a página. Relatórios não consultam meses antes da ação explícita. Gráficos do produto e de tendências são opcionais. As tabelas enviam 50, 100 ou 250 registros por página; isso limita renderização, não a consulta total nem os indicadores.
- O perfil de CPU local atribuiu 13,558 s de uma execução instrumentada de 15,708 s às duas chamadas de `medias_por_produto`: 2.400 construções de MultiIndex e 4.817 reindexações. O cálculo agora agrupa o mesmo conjunto de dias em blocos limitados a aproximadamente 100 mil células, em vez de recriar índices por SKU. As fórmulas e critérios de elegibilidade permanecem iguais.
- Testes diferenciais com uma referência congelada da base comparam DataFrames exatamente, incluindo 60 amostras aleatórias, painel completo de 1.100 SKUs, ruptura contínua, ausência de histórico, limites numéricos e preservação das entradas.
- O opener HTTP passa a pertencer ao cliente exclusivo da sessão. Preserva proxy, TLS, bloqueio de redirects, timeout e limite de resposta. Não há cookies, headers de autenticação persistentes ou pool de conexões. O ganho medido é preparação local do transporte, não redução comprovada do handshake de rede.

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

O lock é por sessão e mantém a identidade estável durante consulta, cálculo e apresentação. O contexto pós-consulta é comparado antes de qualquer dado ou metadado ser exibido. Mudança de organização ou permissão interrompe a apresentação. A exportação verifica Auth e permissões antes e depois da geração e usa entrega direta à página, sem callback global de download. Consulte [a documentação da interface](UX_FLATTER.md).

Dados já enviados ao navegador não podem ser apagados retroativamente. Revogação bloqueia novas operações e o monitor remove a tela aberta na próxima verificação. Não existe cache persistente de catálogos ou relatórios. Não foram alterados migrações, grants ou políticas RLS produtivas.

Partida a frio do Streamlit Community Cloud, distância de rede, carga do Supabase e tamanho total do histórico podem dominar a latência real. A consulta ainda retorna a base necessária ao cálculo. Antes de adotar agregações no banco ou cache por versão, medir esses fatores em contas fictícias autorizadas. A meta orientativa de dois segundos não é uma garantia de produção.

A URL publicada é `https://estoqueinteligente-flatter.streamlit.app/`. A candidata não foi publicada. Uma verificação anônima do endereço, quando disponível no artifact, não comprova login, permissões ou tempo de resposta autenticado. Nenhuma conta empresarial ou credencial real foi usada.

Após revisão deste PR, solicitar a auditoria independente da Issue #3 antes de autorizar dados empresariais. Merge e publicação dependem do proprietário.
