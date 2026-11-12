> HISTÓRICO. Substituído em 12/11/2026 por API única e cadastro sem emissão. Não executar estas instruções.

# Checklist de ativação das APIs GestãoClick

Este documento é o procedimento vigente para os dois repositórios Flatter.
A preparação DEMO não autoriza consultas ou emissões reais. Não cole tokens,
senhas, dados pessoais, XML ou PDF em issues, PRs ou no chat.

## Já preparado

- Aplicação única em Streamlit com Auth, permissões e Supabase existente.
- Cliente analítico GET-only, separado do componente fiscal.
- Prévia imutável, escolhas fiscais manuais, confirmação e aprovação Auth.
- Solicitações únicas por organização, origem, ID estável do pedido, destino e
  ambiente; diário, estados, concessão temporária e auditoria.
- Executor Edge sob demanda exclusivamente DEMO, sem adaptador ou segredo ERP.
- Contratos de referência e cenários compartilhados com a automação Python.
- Bloqueio de resultado incerto, sem repetir POST nem apagar histórico.
- Ausência de armazenamento/download automático de XML/PDF.
- Dashboard compacto; nenhuma consulta ERP ou treinamento ao trocar de área.

## O que o proprietário precisa fornecer

1. OpenAPI autorizado **da conta Atacado** e **da conta Varejo**, com versão/data,
   base URLs, lojas/empresas e limites. Não assumir que os contratos são iguais.
2. Confirmação da empresa sobre finalidade, usuários autorizados e tratamento
   de dados de clientes/cidades, conforme LGPD. Definir minimização e retenção.
3. Credenciais diferentes por conta/ambiente, inseridas somente no ambiente
   protegido. Atacado com menor privilégio e somente leitura quando disponível.
   Varejo com escopo mínimo de consulta e NF-e. Nunca no navegador ou Git.
4. Responsável fiscal para validar destinatário, inscrição estadual, produtos,
   NCM, CFOP, CST/CSOSN, tributação, natureza, transporte e pagamento aplicáveis.
5. Nova autorização explícita para homologação real com poucos pedidos
   identificados. A autorização de publicar DEMO não atende essa etapa.

Não é necessário recriar o app, Supabase, Master ou Secrets atuais.

## Contrato a validar, sem inventar endpoints

| Operação | Conta | Verificação obrigatória |
| --- | --- | --- |
| Autenticação e limites | Ambas | Headers oficiais, expiração, escopos, paginação, 429, timeouts e HTTPS |
| Pedido por número/ID | Atacado | ID estável, organização, loja, status e abrangência; número não é chave global |
| Cliente e destino | Atacado | Identidade mínima, UF, cidade e qualidade; origem de cidade de entrega/cadastro |
| Itens/estoque/vendas | Atacado | Unidade, devolução, cancelamento, datas, reserva, estoque já baixado e histórico completo/incremental |
| Nota existente | Varejo | Correspondência com pedido origem, cliente, itens, organização e ambiente; todos os estados |
| Correspondência fiscal | Varejo | Cliente e produtos já existentes; **nunca criar ou editar cadastros** |
| Criação direta de NF-e | Varejo | A API precisa permitir sem criar pedido. Caso contrário, registrar impedimento e parar |
| Emissão | Varejo | ID externo persistido antes do comando, sem retry; resposta síncrona/assíncrona comprovada |
| Resultado/reconciliação | Varejo | Consulta somente leitura por ID/identidade, inclusive após timeout de criação sem ID retornado |

Os códigos internos de frete 0/1/2/3/4/9 e contribuinte 1/2/9 seguem o contrato
da prévia. Os nomes de campos e códigos da API precisam de adaptador validado.
Não copiar nomes XML como parâmetros presumidos. Falta de inscrição estadual
não define isento ou não contribuinte.

Os rótulos conhecidos Em aberto, Aprovada, Reprovada, Denegada, Cancelada,
Enviando e Corrigida não autorizam presumir códigos numéricos, transições ou
nova emissão. Estado desconhecido e Reprovada exigem conferência. Pedido em
andamento não baixa estoque; em separação/concretizada não geram segunda baixa.

## Efeito sobre a emissão automática do ERP

Conferir o alerta oficial sobre emissão/cancelamento pela API e eventual
interferência no agendamento da loja:
https://central.gestaoclick.com.br/hc/pt-br/articles/1874-emitir-e-cancelar-nota-fiscal-pela-api-linha-fis

Confirmar com suporte/documentação da conta **antes** de habilitar qualquer
operação fiscal, inclusive teste autenticado que possa mudar o agendamento.
Se o efeito não for esclarecido, manter a ativação bloqueada. Cancelar,
inutilizar, excluir ou corrigir notas não faz parte da automação autorizada.

## Homologação e ativação controlada

1. Versionar contrato sanitizado, hash e mapas de campos; revisar endpoints e
   allowlists. Nenhum segredo ou histórico empresarial no repositório público.
2. Validar apenas leituras autorizadas e reconciliar amostras com o ERP.
   Confirmar integridade de pedidos/itens e limites do histórico importado.
3. Implementar o adaptador no **único executor**. Provar journal separado antes
   de criar e antes de emitir, ID externo persistido, consultas de reconciliação
   e bloqueio de toda mutação fora de criar/emitir NF-e direta no Varejo.
4. Medir duração real e limites Edge: 150 s de wall time no Free, 2 s de CPU e
   256 MB na documentação consultada em 11/11/2026. O DEMO usa timeouts de 15 s
   por chamada Supabase e concessão de 60 s. Ajustar somente após medições;
   não prometer adequação à latência do ERP antes de testá-la.
5. Testar sessão expirada, revogação, duas pessoas simultâneas, repetição,
   nota existente, todos os estados, rejeição, 401/403/429/5xx, timeout antes e
   depois de criar/emitir e falha ao persistir o resultado. Nunca repetir POST.
6. Verificar backup cifrado e restauração, RLS, auditoria, cotas e chave de
   desativação. A reversão desativa o executor e preserva solicitações incertas;
   não desfaz nota no ERP nem libera duplicidade.
7. Validar poucos pedidos previamente autorizados com o responsável fiscal,
   conferir manualmente no ERP, registrar período, conta, versão e resultado.
8. Solicitar autorização final de ativação, conceder permissões individualmente,
   ativar por conta/ambiente e monitorar erros/cotas. Manter Atacado GET-only.

Não ativar envio real de e-mails junto desta mudança. Relatórios têm configuração
e autorização próprias. Arquivamento/exclusão de histórico exige aprovação,
backup verificável e recuperação testada; não existe exclusão automática.

## Bloqueios ainda existentes

OpenAPI, credenciais protegidas, mapeamento fiscal, correspondência entre contas,
emissão direta sem pedido, efeitos no agendamento, latência e homologação reais
dependem das etapas acima. O adaptador real levanta erro por construção.
Testes com dados fictícios comprovam o fluxo DEMO e suas proteções, não a API real.
