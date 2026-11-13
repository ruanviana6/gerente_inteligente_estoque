# Contrato de leitura do GestãoClick

O conector de `integracoes/gestaoclick.py` usa somente GET. Não existe argumento
para escolher outro método, enviar corpo, fornecer uma URL pelo navegador ou
executar uma rota arbitrária. O transporte urllib fixa GET e bloqueia redirects.
Nenhum fluxo cria, altera ou exclui dados no ERP, agora ou em evolução futura.

## O que foi confirmado e o que falta conferir

As fontes oficiais consultadas foram:

- [Visão geral da API](https://gestaoclick.com.br/integracoes/api/).
- [Campos, filtros e retorno](https://central.gestaoclick.com.br/hc/pt-br/articles/1863-api-nomes-de-campo-filtros-e-formato-do-retorno).
- [Identidade do token](https://central.gestaoclick.com.br/hc/pt-br/articles/1867-api-quem-o-token-e-quando-entra-usuario-loja-e-permissoes).
- [Recursos e endereços](https://central.gestaoclick.com.br/hc/pt-br/articles/1871-api-o-que-da-e-o-que-nao-da-para-fazer-e-os-enderecos-que-confundem).

A documentação pública descreve autenticação pelos dois headers, listas
paginadas de até 100 itens, metadados de contagem e próxima URL. Também informa
que usuário e loja associados à chave podem limitar os registros visíveis,
inclusive em respostas HTTP 200. O recurso `pedidos` pode representar a loja
virtual; seu nome não permite inferir que seja uma venda efetivada do ERP.

O contrato da conta deve ser conferido na aba Referência, pelo OpenAPI ou pela
documentação online. O arquivo OpenAPI não é obrigatório. Nenhum contrato de
campos de produto, estoque, documentos, linhas ou situação real foi presumido.
Os testes usam especificações e linhas fictícias declaradas no próprio teste.

A API pode depender de aplicativo adicional ao plano do ERP. O proprietário deve
confirmar acesso já habilitado e ausência de contratação não autorizada. Esta
implementação não contrata, ativa ou paga nenhum recurso.

## Liberação restrita no servidor

`GestaoClickConfig.from_mapping` lê os Secrets protegidos. O padrão é
`GESTAOCLICK_ENABLED=false`. O modelo de configuração exige:

```toml
GESTAOCLICK_ENABLED = false
GESTAOCLICK_BASE_URL = "COPIAR_BASE_HTTPS_DA_REFERENCIA_CONFERIDA"
GESTAOCLICK_ORGANIZATION_ID = "UUID_DA_ORGANIZACAO_AUTORIZADA"
GESTAOCLICK_ACCESS_TOKEN = "CONFIGURAR_SOMENTE_NOS_SECRETS"
GESTAOCLICK_SECRET_ACCESS_TOKEN = "CONFIGURAR_SOMENTE_NOS_SECRETS"
GESTAOCLICK_SOURCE_ID = "IDENTIFICADOR_INTERNO_DA_FONTE_AUTORIZADA"
GESTAOCLICK_STORE_ID = "IDENTIFICADOR_DA_LOJA_CONFERIDA"
```

Não copie tokens para o chat, repositório, argumentos de shell ou screenshots.
Cada conta/loja tem configuração própria. Não há fallback automático para outra
loja, usuário, empresa ou chave. Confira no ERP qual usuário e loja a chave
representa, pois os parâmetros da URL podem ser ignorados em favor da chave.
Prefira usuário operacional com permissão mínima de consulta e confira IP,
dias/horários e permissões dos recursos com o administrador da empresa.

A liberação requer contrato do servidor construído por
`ContratoGestaoClick.from_openapi` ou `from_read_contract`. O primeiro exige
OpenAPI 3, servidor exato, GET documentado, resposta 200 e parâmetros de
paginação declarados. O segundo usa o formato equivalente descrito abaixo.
Em ambos, a resposta precisa respeitar o envelope 200/success. A allowlist
fechada admite apenas `produtos`, `vendas`, `lojas` e `situacoes_vendas`, quando
o GET também estiver comprovado no documento. Referências de parâmetros
precisam ser resolvidas administrativamente antes da aprovação do contrato.
A configuração fica no backend, nunca em um editor de rotas no navegador.

O domínio deve pertencer a `gestaoclick.com` ou `gestaoclick.com.br`, com HTTPS,
porta padrão e base `/api` ou `/v1`. A única exceção de caminho é a raiz exata
`https://api.gestaoclick.com`, informada pelo proprietário na Referência em
13/11/2026. Essa exceção não libera outras raízes, `/mcp` ou domínios parecidos.
Não acrescentar `/api` automaticamente à base informada. Domínios diferentes eventualmente informados
no OpenAPI da conta continuam bloqueados até revisão do código e comprovação
oficial; não desative a verificação para contornar a recusa. Não há acesso a IP,
localhost, userinfo, query/fragmento na base ou versões antigas presumidas.

## Cobertura e limites

### Alternativa ao arquivo OpenAPI

`GESTAOCLICK_READ_CONTRACT_JSON` aceita uma string JSON com exatamente quatro
chaves: `version` igual a 1, `base_url`, `documentation_urls` e `resources`.
As referências são URLs HTTPS oficiais, sem chaves em query. O parser não
consulta essas páginas e citar uma URL não demonstra validação comercial.
O administrador precisa conferir cada filtro na Referência da conta.

Cada recurso admite somente `method: "GET"` e uma lista `filters` sem repetição,
contendo `pagina` e `limite`. O caminho é derivado do nome permitido, nunca
recebido do navegador ou copiado de uma URL arbitrária. Até os recursos que
não serão usados são validados. POST, recursos fiscais, caminhos livres,
servidor divergente e propriedades desconhecidas são recusados.

Exemplo **estrutural**, insuficiente para sincronizar vendas: os filtros reais
de período, a loja e os mapas ainda precisam ser conferidos e acrescentados.

```json
{
  "version": 1,
  "base_url": "https://api.gestaoclick.com",
  "documentation_urls": ["https://api.gestaoclick.com"],
  "resources": {
    "vendas": {"method": "GET", "filters": ["pagina", "limite"]}
  }
}
```

O diagnóstico limitado de conexão é separado desse leitor e não exige os mapas
de vendas nem cria uma fonte interna fictícia. Exige organização explícita nos
Secrets, Master e consentimento. Consulte `ATIVACAO_GESTAOCLICK.md`.

`GestaoClickClient.consultar` retorna somente depois de terminar todas as páginas
e reconciliar as contagens. A próxima URL deve conservar domínio, caminho,
filtros, loja e limite, avançando exatamente uma página. Links externos,
parâmetros duplicados, troca de usuário ou alteração de filtros são recusados.

Metadados ausentes ou incoerentes impedem sincronização. Uma lista vazia é válida
somente quando o contrato paginado confirma zero registros. Uma mudança de
contagem durante a leitura também interrompe a execução. Contagens iguais não
provam que a origem ficou imóvel: a reconciliação humana continua necessária e
os filtros de modificação só podem ser habilitados quando documentados para o
recurso da conta. Não há inferência automática de cobertura completa por HTTP 200.

Os limites iniciais são 20 mil registros, 500 páginas, 1.000 requisições por
instância, até três tentativas, timeout de 20 segundos e intervalo mínimo de um
segundo entre chamadas. Cada resposta aceita no máximo 5 MiB e o total por instância é limitado a 20 MiB. Ajustes de ritmo
precisam respeitar as cotas reais por chave informadas na conta. O orçamento
local não representa uma medição da cota diária global do ERP.

401/403 interrompem sem repetição. 429 e falhas temporárias têm backoff limitado.
`Retry-After` numérico é respeitado; espera acima de 60 segundos ou formato não
reconhecido interrompe com orientação para retomar depois, sem tentar antes do
prazo. Timeouts esgotam as tentativas com mensagem segura. Nenhuma resposta,
URL, header ou credencial é registrada em logs pelo conector. Resultados e
configuração não revelam seus campos em `repr`.

## Normalização e análises dependentes

`normalizar_linhas` recebe um `MapaLinhaGestaoClick` explicitamente conferido
com os campos reais da conta. Preserva IDs e SKUs, valida datas ISO,
quantidades finitas, devoluções e identidade explícita da loja, remove dados pessoais e rejeita linhas
repetidas. A chave é fonte, loja, documento e linha; um SKU não identifica
sozinho um documento ou produto entre contas.

Os status internos são `potential`, `separating`, `completed`, `cancelled`,
`returned` e `unknown`. O mapa entre a situação real da API e esses valores é
obrigatório. O conector não presume o código de uma situação por seu nome.
Quantidade devolvida deve estar explicitamente mapeada: campo ausente não
comprova devolução zero. Dados ausentes desabilitam a análise dependente,
sem criar venda, receita, demanda perdida ou ajuste de estoque fictício.

`normalizar_documentos` também aceita um mapa explícito para documentos e sua
coleção de itens. Ele propaga identidade, data, loja e situação do documento
para cada linha. O nome da coleção nunca é deduzido e documentos sem itens
verificáveis precisam de reconciliação antes de importar.

O retorno normalizado contém apenas os campos da análise, sem nomes,
documentos, telefones ou endereços de clientes. Receita e custo não são
inventados a partir da quantidade. A primeira versão de sincronização trata
linhas de vendas; o saldo e o catálogo do motor permanecem no fluxo CSV validado
até conferência do contrato específico de produtos/estoque da conta.

## Validação e ativação real

Os testes de `tests/test_gestaoclick.py` usam transporte fictício e inspecionam
a requisição urllib sem conexão com o ERP. Isso verifica o contrato do código,
não autenticação da conta, cobertura real de lojas, limites contratados ou
correção dos campos oficiais. Não houve chamada autenticada ao GestãoClick.

Antes de ligar a primeira sincronização real: reativar o Supabase somente se
estiver pausado e validar seu estado,
aprovar as correções de segurança, obter autorização da empresa, conferir o
contrato, preparar mapa de linhas/status no backend, instalar os Secrets e
realizar GET de amostra pequena com autorização. Reconcilie contagens e
quantidades com o ERP. A importação completa exige autorização explícita
adicional após essa prévia; sincronização automática permanece desligada.

## Leitor montado pelos Secrets do servidor

`integracoes.runtime.build_reader(values)` valida a configuração sem abrir rede.
O retorno possui `.config` e o método `.read(inicio, fim)`, que aceita datas
civis ISO ou `datetime.date` e entrega linhas canônicas para o coordenador de
sincronização. Esse método não grava no banco. Ele sempre lê o recurso `vendas`;
a interface não informa rota, método HTTP, mapa ou filtros arbitrários.

Além dos valores básicos, configure nos Secrets protegidos:

- `GESTAOCLICK_OPENAPI_JSON`: o documento JSON OpenAPI 3 da conta, conferido.
- **Ou** `GESTAOCLICK_READ_CONTRACT_JSON`: contrato equivalente conferido na
  documentação online. Configure exatamente um dos dois; uma variável vazia
  também conta como presente e não há fallback para contornar contrato inválido.
- `GESTAOCLICK_FIELD_MAP_JSON`: mapa explícito de campos, no formato abaixo.
- `GESTAOCLICK_STATUS_MAP_JSON`: objeto que associa o valor real da situação ao
  status interno permitido, por exemplo `{"CODIGO_REAL_CONFERIDO":"completed"}`.
- `GESTAOCLICK_PERIOD_FILTERS_JSON`: objeto `{"start":"FILTRO_INICIAL_REAL","end":"FILTRO_FINAL_REAL"}`,
  com nomes confirmados nos parâmetros query do GET da conta.
- `GESTAOCLICK_FIXED_FILTERS_JSON`: opcional, objeto de filtros adicionais
  confirmados, por exemplo um tipo de documento. Não aceita usuário, loja,
  paginação ou sobrescrita dos filtros temporais.

Os objetos ficam como **strings JSON** nos Secrets, por exemplo com strings
multilinha TOML. O parser recusa chaves repetidas, valores não finitos, campos
pessoais arbitrários, configurações incompletas e JSON acima de 5 MiB. Não
inclua tokens dentro da especificação OpenAPI ou dos mapas.

O exemplo seguinte é apenas um modelo de mapeamento, com nomes deliberadamente
fictícios. Substitua cada caminho pelo campo confirmado na especificação e na
amostra autorizada, não pelo nome que pareça semelhante:

```json
{
  "mode": "documents",
  "lines_path": ["ITENS_REAIS_CONFERIDOS"],
  "document_fields": {
    "document_id": ["ID_DOCUMENTO_REAL"],
    "sale_date": ["DATA_REAL"],
    "status": ["SITUACAO_REAL"],
    "store_id": ["LOJA_REAL"]
  },
  "line_fields": {
    "line_id": ["ID_LINHA_REAL"],
    "product_id": ["ID_PRODUTO_REAL"],
    "sku": ["SKU_REAL"],
    "quantity": ["QUANTIDADE_REAL"],
    "returned_quantity": ["QUANTIDADE_DEVOLVIDA_REAL"]
  }
}
```

Se o endpoint confirmado já devolver uma linha por registro, use
`{"mode":"lines","fields":{...}}`, reunindo todos os campos obrigatórios
nesse objeto. Caminhos são listas de até seis nomes, admitindo objetos
aninhados, sem expressões executáveis. Os campos opcionais são `product_name`,
`category`, `supplier` e `source_updated_at`. Nenhum dado de cliente é coletado.

O leitor recusa datas futuras, período invertido e janelas acima de dez anos.
Cada `sale_date` normalizada deve pertencer à janela solicitada; receber uma
venda fora dela interrompe a prévia com aviso sobre filtros ignorados, em vez
de descartar silenciosamente o registro. Isso não demonstra que registros
faltantes foram entregues. A confirmação de abrangência continua humana.

Esse fluxo trata uma janela de **datas de vendas**, não presume filtros de
modificação para atualização incremental. A liberação de incrementais exige
contrato e reconciliação específicos, incluindo alterações e cancelamentos de
documentos cuja data original esteja fora da janela. Não substitua os nomes
dos filtros temporais por filtros de modificação para simular esse suporte.

Erros desse leitor são subclasses de `GestaoClickError`, com mensagens seguras.
A camada de interface deve apresentá-las sem imprimir Secrets, mapas completos
ou respostas do ERP. O coordenador continua responsável por exigir Master,
revalidar a sessão, preparar a prévia e pedir confirmação antes da persistência.
