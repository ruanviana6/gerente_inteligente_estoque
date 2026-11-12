# Ativação da API GestãoClick: cadastro por loja

Regra vigente de 12/11/2026: uma API, lojas distintas. Origem FLT Comércio
(atacado), destino Flagship - Flatter (varejo). A automação apenas CADASTRA
a NF-e. Emissão exclusivamente manual no ERP, nunca automática.
As instruções anteriores de duas APIs ou de criar e emitir estão substituídas.

## Preparação já implementada

- Aplicação única com Supabase Auth, permissões, RLS e login persistente.
- Aba Automação / Notas Fiscais com seleções manuais, prévia e confirmação.
- Frete 0/1/2/3/4/9, sem padrão; contribuinte 1/2/9, sem confundir isento e não contribuinte.
- Apenas Ceará; Em separação/Concretizada elegíveis, Em andamento exige aprovação Auth.
- Solicitação única por organização, conexão, lojas, ID estável do pedido e ambiente.
- Diário anterior ao cadastro, proteção concorrente e reconciliação após resultado incerto.
- Executor isolado DEMO, sem emissão, venda, pedido, download ou armazenamento XML/PDF.
- Cliente analítico somente leitura; nenhuma consulta ERP ou treinamento na navegação.
- Adaptadores futuros com uma credencial e IDs de lojas separados, sem rotas reais presumidas.

A publicação/testes efetivos devem ser conferidos no relatório da revisão.
Código preparado não equivale a cadastro real homologado.

## O proprietário precisa fornecer

1. **OpenAPI autorizado da API única**, com versão/data, base URL e limites.
2. **IDs reais das lojas** FLT Comércio e Flagship - Flatter, confirmados por
   listagem oficial. Não inferir por nome, ordem na tela ou CNPJ da imagem.
3. **Uma credencial da API**, em ambiente protegido, com o menor privilégio
   disponível. Nunca enviar chaves no chat, Git ou navegador. O executor guarda
   o segredo; o cliente analítico continua limitado a consultas.
4. Responsável fiscal para validar cliente, inscrição estadual, produtos, NCM,
   CFOP, CST/CSOSN, impostos, natureza, transporte e pagamento aplicáveis.
5. Autorização da empresa para finalidade, dados pessoais e histórico, seguida
   de autorização explícita de homologação real com poucos pedidos identificados.

Não recriar Supabase, Master, app ou Secrets atuais. A configuração futura usa
GESTAOCLICK_API_BASE_URL, GESTAOCLICK_API_ACCESS_TOKEN,
GESTAOCLICK_API_SECRET_ACCESS, GESTAOCLICK_LOJA_ATACADO_ID e
GESTAOCLICK_LOJA_VAREJO_ID. Os valores serão inseridos apenas no executor
protegido. NFE_CADASTRO_REAL_HABILITADO continua falso e não basta, sozinho,
para liberar uma operação sem adaptador homologado. Não há flag de emissão.

## Contrato a validar, sem inventar endpoints

| Operação | Verificação obrigatória |
| --- | --- |
| Autenticação única | Headers, validade, escopos, empresa e lojas acessíveis |
| Lojas | IDs estáveis e correspondência FLT Comércio/Flagship - Flatter |
| Pedido na origem | Número e ID, loja, destinatário, UF, status, itens e abrangência |
| Catálogo e histórico | Unidades, devoluções, cancelamentos, reservas, disponibilidade e dados completos/incrementais |
| Nota existente | Consulta por identidade completa, inclusive rascunho, cadastro pendente, emitida e estado desconhecido |
| Correspondência fiscal | Cliente/produto existentes na loja de destino, sem criar ou editar cadastros |
| Cadastrar NF-e direta | Precisa funcionar SEM venda/pedido no varejo e SEM emitir ou enviar automaticamente |
| Consultar/reconciliar | Somente leitura, por ID externo e referência de origem, inclusive depois de timeout sem ID |

Não implementar chamada de emissão. Não usar o botão ou a operação equivalente
a **Cadastrar e emitir**. A operação permitida equivale somente a **Cadastrar**.
Se a API não oferecer esse comportamento, registrar o impedimento e não criar
uma venda/pedido nem emitir como alternativa.

Os códigos internos de frete e contribuinte precisam de adaptador para os campos
reais do OpenAPI. Não copiar nomes XML como parâmetros presumidos. Falta de IE
não determina isento ou não contribuinte. Informação fiscal essencial ausente
bloqueia cadastro real. Não inventar números de NF-e, chave ou protocolo.

## Efeitos do cadastro no ERP

Validar se o simples cadastro da nota pode movimentar estoque, gerar venda,
acionar envio/agendamento automático ou alterar configurações da loja. Se
houver qualquer efeito proibido ou não esclarecido, manter o cadastro real
bloqueado e consultar documentação/suporte. A automação não altera configurações
para contornar o problema. Emissão e seus efeitos ficam sob operação manual.

Pedidos Em separação/Concretizada podem ter baixado estoque na origem: não
realizar uma segunda baixa. Em andamento é apenas situação observada e exige
aprovação explícita. Nota já cadastrada ou emitida bloqueia duplicidade.
Estado desconhecido exige reconciliação, nunca retry de POST.

## Homologação e ativação controlada

1. Versionar contrato sanitizado/hash e mapas aprovados, sem segredos ou dados reais.
2. Conferir leituras autorizadas e reconciliar amostras por loja com o ERP.
3. Implementar somente cadastro direto no único executor, com allowlist mínima,
   journal durável, ID persistido e consulta de reconciliação. Não mapear emissão.
4. Medir latência real e limites Free da Edge Function: 150 s de wall time,
   2 s de CPU e 256 MB na documentação consultada. A DEMO não comprova latência ERP.
5. Testar sessões expiradas, revogação, usuários simultâneos, repetição, nota
   anterior, dados incompletos, recusa do cadastro, 401/403/429/5xx e timeout
   antes/depois de receber ID. Nenhum POST pode ser repetido automaticamente.
6. Validar nova confirmação humana de solicitações expiradas sem apagar histórico
   ou mudar a identidade. Operação já iniciada/incerta sempre exige reconciliação.
7. Conferir backup/restauração, RLS, auditoria, cotas e desligamento do executor.
   Desligar preserva dados e não desfaz nem modifica a nota no ERP.
8. Com autorização específica, cadastrar poucos pedidos e conferir manualmente
   no ERP. A emissão é feita por pessoa autorizada diretamente no GestãoClick.
9. Obter aceite final, ativar gradualmente, monitorar e preservar leitura estrita
   da origem e do módulo analítico. Não habilitar e-mails reais junto da mudança.

Nenhuma exclusão/arquivamento automático de histórico está autorizado. Retenção
exige aprovação, backup verificável e recuperação testada. Serviços pagos não
fazem parte deste procedimento.
