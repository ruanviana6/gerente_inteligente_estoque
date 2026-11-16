# Sincronização automática de vendas

O conector mantém o GestãoClick somente leitura. Ele atualiza uma cópia de
linhas de vendas no Supabase. Não cadastra vendas, não emite notas e não altera
saldos no ERP. Catálogo e saldo do motor de estoque continuam sendo recebidos
por CSV; a autorização de consulta não significa que esses recursos estejam
sincronizados nesta versão.

## O que mudou

- A consulta manual aceita até **732 dias inclusivos**, o dobro do limite anterior.
  Os mesmos limites são conferidos no leitor, no coordenador e no banco.
- A atualização automática roda em GitHub Actions, fora do Streamlit. Fechar o
  navegador, sair da conta ou a hibernação do app não interrompem o agendador.
- O Master aprova uma fonte, loja e contrato. O executor usa uma credencial
  dedicada; não recebe senha Master, JWT de usuário ou chave `service_role`.
- Cada execução lê uma janela de até 30 dias e consolida somente após validar
  todas as páginas, quantidades e identificadores. Não publica carga parcial.
- A carga inicial percorre até 732 dias do mais recente para o mais antigo.
  As próximas janelas ficam disponíveis depois de uma hora, até completar a carga.
  Depois, o intervalo padrão é de 24 horas entre janelas.
- A rotina alterna revisão recente com revisão histórica. A sobreposição de
  14 dias ajuda a recuperar alterações; um marcador de progresso recupera
  atrasos maiores que essa janela. Cancelamentos antigos dependem da revisão
  histórica, pois o filtro atual é a data da venda, não a data de alteração.

O banco não transforma uma carga automática em declaração de histórico completo.
`coverage_confirmed` permanece falso. Os indicadores continuam mostrando a
limitação de abrangência; HTTP 200 e contagens consistentes não comprovam que
a chave enxergou todas as vendas da empresa.

## Antes de ativar

1. Aplicar a migração aditiva de sincronização automática depois da validação
   em banco descartável e backup verificável. Não editar migrações anteriores.
2. Em **Administração → Integrações → Configuração e sincronização automática**,
   usar **Verificar configuração da sincronização**. O diagnóstico lista apenas
   nomes de configurações ausentes, sem mostrar seus valores e sem consultar o ERP.
3. Conferir fonte interna, ID real da loja, contrato de GET, nomes dos filtros,
   mapa de campos, situações, devoluções e instante de alteração. As chaves de
   acesso sozinhas não permitem inferir esses campos.
4. Preparar uma amostra real, com **DEMO desmarcado**, e comparar com o ERP.
   A demonstração fictícia não comprova a conexão ou a integridade dos dados reais.
5. Escolher o histórico e intervalo, confirmar a validação e ativar. O banco
   retorna uma credencial exclusiva do executor somente na criação ou rotação.
   A autorização fica registrada, mas ainda depende da configuração abaixo.

## Configuração protegida do executor

O workflow `.github/workflows/sincronizacao.yml` precisa existir na branch
padrão `main` para o GitHub executar o cron. Ele faz checkout explicitamente
de `implementacao/web-supabase`; a aplicação continua publicada nessa branch.
Adicionar o arquivo apenas à branch do Streamlit não inicia o agendamento.

Em **GitHub → Settings → Environments → sincronizacao**, guardar um Secret
chamado `SYNC_JOB_CONFIG_JSON`. Ele contém um objeto JSON com:

- `SUPABASE_URL` e `SUPABASE_PUBLISHABLE_KEY` do projeto existente;
- `SYNC_JOB_SECRET`, credencial exclusiva gerada na ativação Master;
- as configurações `GESTAOCLICK_*` conferidas que já ficam protegidas no servidor:
  ativação, URL, tokens, organização, fonte, loja, um contrato, campos, situações
  e filtros. Os valores JSON internos continuam sendo strings JSON, como nos Secrets.

Não copiar `SUPABASE_BACKEND_SECRET`, senha Master nem chave administrativa.
O executor recusa chaves de configuração alheias à sua finalidade. Não colocar
esse objeto em arquivo versionado, Issue, PR, artefato de CI ou mensagem.

Em **Settings → Secrets and variables → Actions → Variables**, definir
`SYNC_SCHEDULER_ENABLED=true` somente depois de configurar o Secret e aprovar
a automação. Sem essa variável, nenhum cron consulta o ERP.

Em **Actions → Sincronização automática somente leitura → Run workflow**:

1. Executar primeiro o modo `check`: valida configuração, sem rede.
2. Executar `run`: processa uma única janela aprovada, se houver trabalho devido.
3. Conferir última execução e status no aplicativo. Um workflow verde em modo
   `check` não significa que vendas foram importadas; `not_due` também não.

O GitHub pode atrasar agendamentos e desativá-los em repositórios públicos sem
atividade. O painel mostra última conclusão e próxima janela; não há promessa
de tempo real. Não foi contratado plano pago nem criado mecanismo para impedir
artificialmente a hibernação do Streamlit.

## Segurança, retomada e cotas

A credencial do job só alcança RPCs fechadas. Cada etapa confere organização,
fonte, loja, contrato, Master aprovador ativo e concessão temporária. Alterar
o contrato exige nova aprovação; pausar ou trocar a credencial invalida o acesso.
O processo não fabrica sessões de usuário nem contorna RLS com chave administrativa.

Uma resposta perdida após o commit permite concluir o checkpoint sem reler o
ERP. Falhas anteriores não avançam a janela. Quando o conteúdo muda durante uma
tentativa, o staging descartável pode ser cancelado; o histórico consolidado é
preservado. Ausência de uma venda na resposta nunca é tratada como exclusão.

Por job: até 180 segundos, 100 requisições, 10 mil registros, 30 dias e páginas
internas de cem linhas. Permanecem os limites de bytes e validação de paginação.
Um período acima do limite de volume é recusado; não se aceita truncamento silencioso.

O limite atual de mil execuções registradas por organização é verificado antes
de liberar nova consulta. Ao atingir a capacidade, o agendamento pausa com
`RUN_LIMIT`; execuções pendentes excessivas usam `RUNS_PENDING`. Nenhum histórico
é apagado automaticamente. É necessário revisar ocupação e política de retenção
com backup e autorização antes de ampliar esses limites.

## Correção da demonstração e do diagnóstico

O gerador anterior registrava alterações às 18h UTC no próprio dia, podendo
produzir datas futuras recusadas pelo banco. Além disso, a quantidade de um
mesmo documento mudava conforme o início da janela consultada. A demonstração
agora usa dados estáveis por data e 03h UTC, início do dia de Fortaleza.

O novo cenário usa uma fonte DEMO separada, preservando execuções antigas para
auditoria. DEMO não vem mais selecionado por padrão. Os erros de comunicação,
configuração e preparação apresentam orientações específicas sem expor corpos
HTTP, tokens ou dados comerciais.

Testes automatizados usam dados fictícios. A ativação real só pode ser declarada
depois de configurar o executor, validar o contrato e observar uma execução
real concluída no banco. A automação fiscal permanece independente e em DEMO.
