# Relatórios mensais e agendamento

A visualização e exportação no app já têm implementação. Cada relatório exige lote histórico com abrangência declarada de todo o mês e saldo observado no último dia, no calendário de Fortaleza. Sem esse fechamento a geração é recusada. Os números usam o mesmo motor do painel; unidades vendidas não são receita. O Master escolhe destinatários, dia mensal e blocos. As permissões vigentes restringem o conteúdo.

O e-mail preparado é um **aviso com link autenticado**, sem valores financeiros, produtos ou anexos. O relatório completo permanece na aplicação, onde a autorização pode ser revalidada. O envio real continua desligado até configuração e autorização explícitas.

## Simulação e segredo dedicado

O worker não usa sessão Master nem `service_role`. Ele recebe um segredo aleatório exclusivo, cujo hash fica no schema privado do banco. Esse segredo permite somente reservar, revalidar e finalizar trabalhos de relatório de uma organização. Trate-o como credencial: ele autoriza leitura dos lotes necessários aos destinatários elegíveis.

1. Gere localmente um segredo aleatório de pelo menos 32 caracteres usando um gerenciador de senhas. Não use o texto fictício dos testes.
2. Cadastre-o em **GitHub → Settings → Environments → relatorios → Environment secrets**, nome `REPORT_JOB_SECRET`.
3. Configure o mesmo valor via conexão administrativa protegida usando `private.configure_job_secret(UUID_DA_ORGANIZACAO, SEGREDO)`. Evite salvar consultas contendo o valor no histórico do SQL Editor; prefira uma consulta parametrizada em ferramenta local com secret carregado do ambiente. A função só pode ser executada administrativamente.
4. Nesse environment, adicione `SUPABASE_PUBLISHABLE_KEY`. Não adicione chave administrativa do Supabase.
5. Em **Settings → Secrets and variables → Actions → Variables**, crie a **repository variable** `REPORT_SCHEDULER_ENABLED=true`. Ela precisa existir no repositório, pois é avaliada antes de iniciar o job. No environment `relatorios`, mantenha `REPORT_ALLOW_REAL_EMAIL=false`. O workflow usa environment protegido e permissões de GitHub somente para leitura do código.
6. Na aplicação, habilite a rotina em **Administração**, mantenha o modo de simulação e selecione contas ativas com `reports.view` e `reports.receive`.

O workflow `.github/workflows/relatorios.yml` tem oportunidades diárias às 09h, 12h e 15h UTC, equivalentes a 06h, 09h e 12h em Fortaleza. O banco respeita o dia escolhido para envio real, inicialmente dia 1, e não repete entregas concluídas. As oportunidades adicionais permitem até três tentativas em caso de falha clara. Simulações podem rodar fora desse dia. Schedules do GitHub só passam a operar na branch padrão após a revisão e merge pelo proprietário; podem sofrer atraso ou suspensão por inatividade. A interface não é responsável pelo agendamento.

O comando de execução manual, com as variáveis protegidas configuradas, é:

```bash
python -m scripts.run_monthly_reports --month 2026-09-01
```

O padrão é simular. A fila registra período, destinatário, tentativas e estado. Repetições não duplicam uma entrega concluída. Falhas claras têm até três tentativas; timeout ou confirmação ambígua ficam como `incerto` e não são reenviados automaticamente. O Master consulta o histórico no app. Estados incertos exigem conferência no provedor antes de qualquer ação manual.

## Habilitação opcional do envio real

Somente após testar a simulação e autorizar os destinatários:

1. Configure uma conta no provedor compatível **Resend**, sem contratar plano pago, e um remetente validado. Caso o domínio ou o volume exceda o que já existe gratuitamente, mantenha a simulação.
2. Em secrets do environment, adicione `RESEND_API_KEY`.
3. Em variables, adicione `REPORT_EMAIL_FROM` (endereço validado), `REPORT_APP_URL` (URL HTTPS publicada e verificada) e `REPORT_ALLOW_REAL_EMAIL=true`.
4. No app, o Master deve desmarcar simulação e confirmar explicitamente os destinatários. O banco exige essa autorização novamente imediatamente antes da entrega.

O workflow solicita `--real` apenas quando a variável externa é `true`. Sem os dois controles, não há envio real. Supabase Auth e relatórios usam serviços de e-mail independentes. Nenhuma mensagem real foi enviada durante o desenvolvimento.
