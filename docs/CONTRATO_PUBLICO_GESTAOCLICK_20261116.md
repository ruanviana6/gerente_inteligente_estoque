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
3. Concluir a adaptação de itens, catálogo, devoluções e versões com amostra real
   autorizada; não apenas preencher mapas JSON para silenciar a validação.
4. Reconciliar uma prévia pequena, incluindo cancelamentos, devoluções e PDV.
5. Somente então aprovar a automação e configurar o executor conforme
   [Sincronização automática](SINCRONIZACAO_AUTOMATICA.md).

O conector permanece somente leitura, e a automação fiscal permanece separada.
O limite de 732 dias é uma capacidade do Gerente, não uma declaração de que
esse histórico já foi recebido ou validado.
