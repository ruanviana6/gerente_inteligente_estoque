# Conferência do contrato público do GestãoClick

Em 16/11/2026 foi possível obter, sem credenciais, o OpenAPI 3.1.0 em
[https://api.gestaoclick.com/docs](https://api.gestaoclick.com/docs).
SHA-256 do documento consultado:
`5c9f93ff79c4ea3249da77691df1a7cce3c534bf20779c4ab7b564dbbd3210b3`.
O documento público não demonstra acesso, permissões ou abrangência de uma conta.
Nenhum dado real foi importado nesta conferência.

## Resultado e correções

A mensagem de configuração incompleta não significa que basta inventar seis
valores nos Secrets. O conector genérico foi testado com um contrato fictício
mais completo que o retorno público padrão. A adaptação à API real ainda exige
tratar as diferenças abaixo.

| Item | Contrato público | Consequência |
| --- | --- | --- |
| Período de vendas | `data_inicio` e `data_fim`, pela data da venda | Nomes comprovados; não usar a data de modificação como se fosse a data da venda. |
| Produtos da venda | `data[].produtos[].produto` | A listagem já contém itens; não consultar cada venda apenas para obter produtos. |
| Paginação de vendas | `total_registros`, `total_da_pagina`, `pagina_atual`, `limite_por_pagina` | Não exigir `total_paginas` quando o tamanho de página e o total permitem calculá-lo; conferir próximo link e contagens. |
| Lojas | Lista de objetos `id` e `nome`, sem paginação efetiva | Consultar uma vez; a ordem não identifica varejo ou atacado. |
| Situações | IDs e nomes próprios da conta em `/situacoes_vendas` | Não usar os códigos dos exemplos públicos nem classificar por nome automaticamente. |
| Balcão/PDV | Só aparece com `tipo=vendas_balcao` | Uma consulta sem esse tipo não representa todas as vendas. |
| Situações ocultas | A lista sem filtros específicos pode omiti-las | HTTP 200 e contagens reconciliadas não comprovam cobertura integral. |

O cliente mantém o formato antigo de paginação e aceita o formato público de
vendas com validação estrita. Nenhuma página parcial é consolidada.

### Correção da conferência de situações

O primeiro assistente de conta exigia `total_da_pagina` e limite efetivo de
100 registros. Isso recusava o formato legado `total_registros_pagina` e uma
resposta coerente com limite menor, por exemplo 20. Ambos os formatos são
documentados: além do OpenAPI, o
[artigo oficial sobre campos e retorno](https://central.gestaoclick.com.br/hc/pt-br/articles/1863-api-nomes-de-campo-filtros-e-formato-do-retorno)
apresenta o envelope legado.

Agora a conferência aceita os dois nomes e exige igualdade quando ambos aparecem.
Calcula o número de páginas pelo limite efetivo, confere `total_paginas` quando
presente e exige que total e limite permaneçam estáveis. O próximo endereço é
construído localmente; o link recebido só pode indicar o limite solicitado ou
o limite efetivo já comprovado. Permanecem os mesmos host, recurso e filtros,
contagens inteiras, IDs únicos, páginas completas e teto de quatro GETs.

Se o formato ainda divergir, a tela apresenta um código e somente a presença e
o tipo dos campos de paginação conhecidos. Nunca mostra seus valores, nomes de
lojas/situações, tokens ou resposta bruta nesse diagnóstico. O erro enviado pelo
usuário não identifica sozinho qual campo da resposta real divergiu; os testes
reproduzem as incompatibilidades documentadas, sem alegar consulta autenticada.

A conferência da conta aparece antes do agendamento. Uma tentativa de ativação
com configuração incompleta orienta concluir fonte, loja e contrato antes de
salvar, sem atribuir indevidamente o problema ao UUID da organização.

Na aplicação, **Administração → Integrações → Conferir lojas e situações da conta**
usa as chaves já protegidas no servidor somente após autorização explícita.
São no máximo quatro GETs: lojas e até três páginas de situações. Não lê vendas,
dados de clientes ou valores financeiros. Somente IDs e nomes de lojas/situações
são apresentados ao Master da organização vinculada aos Secrets.

O catálogo consultado fica assinado no servidor, vinculado à sessão e expira em
cinco minutos. Selecionar uma loja não dispara consulta. **Registrar fonte analítica
desta loja** usa a RPC existente e grava somente a fonte no Supabase. Apresenta os
IDs efetivos de fonte e loja; não muda Secrets, não importa vendas, não aprova um
contrato e não ativa o agendador. Não é necessário criar outra conta no ERP.

## Prévia nativa de vendas

Depois de conferir a conta e selecionar a loja, o Master pode usar **Conferir
amostra de vendas**. A ação usa um contrato versionado no código para `GET /vendas`;
não exige mapas JSON, fonte interna nem contrato adicional nos Secrets. As chaves
protegidas e o vínculo da organização continuam obrigatórios.

A consulta recebe um período de até 31 dias e um universo explícito: produtos ou
balcão/PDV. Esse limite é da amostra de conferência, não substitui a capacidade de
732 dias da carga histórica. O fluxo faz somente um GET, solicita cinco documentos
da primeira página e não segue links recebidos. A resposta precisa ter envelope e
contagens válidos. O resultado exibe no máximo cinco documentos e cem itens; não
importa registros nem altera indicadores ou aprova o agendamento.

Somente IDs, número do documento, data, situação, quantidades e unidades são projetados.
Clientes, valores financeiros, observações, credenciais e demais atributos não
são apresentados nem guardados. A prévia não fica em cache ou no estado da sessão
e desaparece em uma nova interação. Autorização, prazo do catálogo e configuração
são conferidos antes e depois do GET, inclusive quando a consulta falha.
O catálogo também fica vinculado à conexão por um HMAC com chave efêmera do
processo. Trocar a base ou as credenciais exige conferir a conta novamente antes
de ler vendas. O objeto assinado guarda somente o digest, nunca as credenciais.

A loja é o **escopo solicitado no filtro**, não uma identidade comprovada pelo
retorno. A amostra nunca declara cobertura integral. SKU, ID estável da linha,
quantidade devolvida e instante de alteração continuam desconhecidos quando não
documentados; não são preenchidos com zero, índice do item ou horário da coleta.
Mesmo uma prévia válida não pode ser enviada ao sincronizador genérico atual.

A unidade vem de `produtos[].produto.sigla_unidade`, campo documentado na API.
Uma quantidade `2` com unidade `CX` aparece como tal; não é convertida para duas
unidades avulsas. Unidade ausente permanece desconhecida. O código comercial do
documento é texto, podendo conter pontuação como `2025/001`; ele não usa a regra
restritiva do identificador técnico. Ambos os campos têm limites de tamanho e
recusam caracteres de controle. Essas correções foram reproduzidas com dados
fictícios baseados no contrato público, sem afirmar ocorrência na conta real.

## Lacunas que continuam bloqueando a carga padrão

O schema e o exemplo de `/vendas` não documentam os seguintes campos exigidos
pelo modelo analítico atual:

- ID estável da linha de produto;
- SKU da linha;
- quantidade devolvida;
- ID de loja no objeto da venda;
- instante de alteração da venda com fuso.

O detalhe `/vendas/{id}` não resolve essas ausências. Não mapear campos
inexistentes, substituir devoluções por zero, usar posição do item como identidade
estável ou fabricar um instante de alteração.

O catálogo `/produtos` fornece `codigo_interno`, mas associar produto/variação
à venda exige uma regra explícita. Devoluções têm recursos próprios:
`/devolucoes` e `/devolucoes/{id}`. A data da listagem de devoluções é a data
de registro, com janela máxima de 12 meses; ela não é a data da venda original.
O detalhe informa pedido de origem e produtos, mas não documenta o vínculo ao
ID da linha original. Uma devolução posterior ao fim do período de vendas não
pode ser descartada. Essas associações ainda precisam de implementação e testes
antes de consolidar a resposta padrão como vendas líquidas.

Os servidores declarados no OpenAPI apontam para `https://app.api.click.app/v1`.
GETs públicos sem chave em `https://api.gestaoclick.com/vendas`, `/lojas` e
`/situacoes_vendas` reconhecem as rotas e retornam 401. Isso não comprova que os
dois endereços sejam intercambiáveis para a conta. A aplicação não troca de host
nem envia credenciais a outro domínio automaticamente.

Os limites descritos na introdução do documento e nas respostas 429 também
divergem. Mantivemos os limites conservadores existentes; não aumentamos a taxa
com base no maior número documentado.

## Ativação real

1. Conferir lojas e situações pela nova ação autenticada e escolher a loja correta.
2. Registrar a fonte e guardar seus identificadores na configuração protegida.
3. Conferir uma amostra nativa pequena pelo novo formulário, comparando documentos,
   produtos, quantidades, situações e loja efetiva com o ERP.
4. Concluir a adaptação de itens, catálogo, devoluções e versões. A persistência
   atual recusa alterações sem um instante de atualização comprovado; essa lacuna
   também exige implementação, não apenas preencher mapas JSON. Reconciliar
   cancelamentos, devoluções posteriores e PDV antes da carga completa.
5. Somente então aprovar a automação e configurar o executor conforme
   [Sincronização automática](SINCRONIZACAO_AUTOMATICA.md).

O conector permanece somente leitura, e a automação fiscal permanece separada.
O limite de 732 dias é uma capacidade do Gerente, não uma declaração de que
esse histórico já foi recebido ou validado.


## Decisão operacional posterior: PDV desativado

A partir de 16/11/2026, o proprietário determinou que este Gerente usará
**somente vendas de produtos**. A explicação histórica acima compara a API de
PDV, mas **não autoriza novas consultas de balcão** na aplicação. A prévia
e a sincronização recusam explicitamente `vendas_balcao`. A consulta
de produtos já foi observada com retorno real na tela; ela não significa
que vendas, saldos e histórico estejam consolidados no banco.
