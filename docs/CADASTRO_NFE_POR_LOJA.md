# Cadastro de NF-e por loja

Regra vigente informada pelo proprietário em 12/11/2026. Substitui o modelo
anterior de duas APIs e a ideia de emissão automática, inclusive nas imagens
históricas. A confirmação da automação **cadastra a nota e encerra o processo**.
A emissão fica exclusivamente manual no GestãoClick.

## Uma API e duas lojas

- Origem: **FLT Comércio**, atacado, somente consulta.
- Destino do cadastro: **Flagship - Flatter**, varejo.
- Uma conexão autenticada, com IDs de lojas explícitos e distintos.
- Os IDs reais dependem do OpenAPI e da confirmação do proprietário. Nome,
  posição na interface e CNPJ não são identificadores presumidos da API.

O cliente analítico do Gerente continua somente leitura. O executor fiscal é
isolado e, futuramente, poderá apenas consultar e cadastrar NF-e direta na loja
de destino. Não pode criar venda/pedido, emitir, cancelar, corrigir, inutilizar,
excluir, editar produtos/clientes ou movimentar estoque. A credencial não fica
no navegador, no chat ou em repositório público.

## Aba de automação

A área **Automação / Notas Fiscais** permanece entre os seis menus principais.
O funcionário informa o pedido da FLT Comércio, confere destinatário, UF,
situação, itens, quantidades e valores e faz duas escolhas manuais:

- Modalidade de frete: 0, 1, 2, 3, 4 ou 9. FOB (1) não é preenchido sozinho.
- Tipo de contribuinte: 1, 2 ou 9. Isento (2) e não contribuinte (9) são distintos.

Após gerar a prévia, a confirmação é **Confirmar cadastro da NF-e · DEMO**.
As seleções ficam congeladas. Para editar, use **Alterar regras e gerar nova
prévia**; o aceite anterior é descartado e uma nova confirmação é obrigatória.
Ela não emite a nota. Não são inventados CFOP, NCM, CST/CSOSN, alíquotas,
natureza ou transporte. A configuração tributária real dependerá dos dados
aprovados e do mapeamento do contrato. Informação essencial ausente bloqueia
o cadastro real, sem recorrer a valores genéricos.

Somente CE/Ceará/Ceara são elegíveis. Em separação e Concretizada podem seguir;
Em andamento exige aprovação Auth explícita e auditada. Outras situações,
nota existente e resultado incerto não liberam cadastro duplicado.

## Banco e executor

O contrato atual é `fiscal/contrato_v2.json`, idêntico a
`contratos/fiscal_demo_v2.json` na automação. SHA256:
`918b42b3fdf584203d06e673e20c65883489cc4d770c5d3b4e3330c76508d121`.
As versões anteriores permanecem como história, sem reinterpretar seus registros.

A migração `20261112192003_cadastro_fiscal_por_loja.sql` adiciona escopo da API,
lojas e versão da política. Registros anteriores ficam com escopo novo nulo,
preservados sem reexecução. A unicidade passa a incluir organização, conexão,
loja de origem, ID estável do pedido, loja de destino e ambiente. Aliases
`source_account`/`destination_account` são compatibilidade da versão anterior,
não representam duas credenciais vigentes.

Resultado normal: `registered`, exibido como **Cadastrada (DEMO), emissão manual
no ERP**. Não é autorização da SEFAZ. A simulação mantém estado Em aberto,
sem gerar número autorizado, chave ou protocolo. Nenhum XML/PDF é baixado
ou armazenado. A Edge Function declara `operation=register_only` e
`automatic_issuance=false`; não possui etapa ou chamada de emissão.

O diário é reservado antes do cadastro. Timeout sem ID (1007) ou após cadastro
com ID preservado (1008) exige reconciliação explícita. Não repetir POST.
Rejeição simulada é recusa do cadastro (1002), não uma rejeição fiscal inventada.
Resultados e aprovações anteriores permanecem auditáveis.

## Segurança, login e publicação

Permissões fiscais continuam independentes e negadas por padrão a nãoMaster.
RLS, organização, sessão ativa, solicitante e aprovador são revalidados no banco.
O corpo HTTP não pode escolher lojas, aprovador, organização ou habilitar emissão.
As configurações antigas de emissão e de duas APIs não reativam esse fluxo.

O login continua com cookie opaco Secure/SameSite, validade limitada, tokens
cifrados no backend e revogação por Sair/desativação. A opção de manter acesso
fica marcada inicialmente; o usuário pode desmarcá-la em dispositivo compartilhado.
F5 e reabertura não devem pedir senha enquanto a sessão continuar autorizada.

A publicação permanece na branch `implementacao/web-supabase`, no mesmo app:
https://estoqueinteligente-flatter.streamlit.app/.
Release esperada: `cadastro-nfe-lojas-20261112.1`.
Não é necessária troca de branch, recriação do app ou mudança dos Secrets atuais.

## O que ainda depende da API

O cadastro real continua bloqueado até validar OpenAPI, IDs das lojas, permissões,
mapeamento fiscal e cadastro direto sem venda, pedido ou emissão automática.
Se cadastrar provocar emissão ou outra mutação proibida, a integração não será
ativada. A emissão não será uma etapa futura da automação.

A renovação explícita de solicitações bloqueadas por expiração, preservando a
mesma identidade e exigindo nova conferência, precisa ser validada antes da
operação real; não se apaga histórico para contornar o bloqueio.
Consulte [o checklist](CHECKLIST_ATIVACAO_API_GESTAOCLICK.md).
