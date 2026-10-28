# Relatórios mensais automáticos por e-mail

> Estado em 28/10/2026: geração mensal, exportação, fila e simulação implementadas no PR #5. Agendamento preparado no GitHub Actions, sem ativação externa nem envio real. Consulte [ATIVAR_RELATORIOS.md](ATIVAR_RELATORIOS.md) para configuração e [ENTREGA_WEB.md](ENTREGA_WEB.md) para evidências.
> O texto abaixo preserva os requisitos do planejamento. A implementação usa aviso por e-mail com link autenticado, sem anexar dados empresariais. O relatório completo fica no aplicativo web único. Provedor, remetente e destinatários ainda exigem configuração e autorização.

## Objetivo

Enviar um resumo mensal do desempenho de estoque e vendas para usuários
autorizados, sem que precisem abrir o painel. O sistema também deve permitir
visualizar e baixar o mesmo relatório dentro da aplicação.

## Frequência e período

- Padrão: enviar **no dia 1 de cada mês**, com dados do **mês civil anterior**
  no fuso `America/Fortaleza`. Esse calendário é configurável pelo Master.
- O agendamento deve ocorrer em serviço independente do ciclo de execução do
  Streamlit; a interface não pode ser responsável por executar o envio.
- Bloquear relatórios com dados incompletos ou sem fechamento confiável;
  exibir período, última atualização, origem e limitações.
- Não disparar duas vezes o mesmo relatório para o mesmo usuário/período.

## Informações do relatório

1. Total de unidades vendidas e quantidade de produtos com vendas no período.
2. Produtos mais vendidos por **quantidade**, com identificação do SKU.
3. Variação frente ao período anterior (quando houver histórico comparável).
4. Produtos sem giro, parados e com estoque em excesso.
5. Estoque em ruptura, risco de faltar durante o prazo e itens que merecem reposição.
6. Tendências de aumento e queda, sinalizando a incerteza dos dados.
7. Valor estimado em estoque ou capital parado **somente se existirem custos válidos**.
8. Faturamento, margem e análises financeiras avançadas **apenas quando a fonte
   realmente fornecer dados de valor de vendas confiáveis e houver permissão**.

Os indicadores devem ser calculados pelas mesmas regras auditáveis do motor
utilizadas pelo dashboard. Nunca inventar números ou confundir quantidades
vendidas, receita de vendas e custo de estoque. Relatórios mensais exigem
histórico próprio do período completo e não apenas os saldos atuais.

## Administração pelo Master

- Habilitar/desabilitar a funcionalidade globalmente.
- Selecionar usuários que podem receber relatórios e suspender envios individuais.
- Escolher periodicidade (mensal como padrão; quinzenal/semanal como evolução).
- Escolher os blocos de indicadores, com as limitações das permissões do usuário.
- Consultar histórico de execuções, destinatários, erros e reenvios manuais.
- Configurar envio de teste com **dados fictícios** antes de ativar o serviço.

## Permissões

| Permissão | Uso |
| --- | --- |
| `reports.view` | Visualizar os próprios relatórios disponíveis |
| `reports.export` | Baixar relatório ou anexo permitido |
| `reports.receive` | Receber relatórios automáticos habilitados pelo Master |
| `reports.manage` | Configurar periodicidade, destinatários e conteúdo permitido |

O gerenciamento de destinatários fica restrito ao Master por padrão.
O destinatário precisa estar **ativo** e possuir `reports.receive` e as
permissões vigentes dos dados presentes em cada bloco do relatório.
A autorização deve ser verificada **na geração e novamente antes do envio**.
Se uma permissão for retirada, bloquear o conteúdo correspondente e não
enviar anexos antigos sem revalidar autorização. Financeiro só com
`finance.view`. Evitar anexos com dados sensíveis desnecessários;
preferir e-mail resumido com link autenticado quando aplicável.

## Arquitetura e segurança

- Usar Supabase PostgreSQL para regras, preferências e histórico de envios.
- Avaliar Supabase Cron + Edge Function para gerar/disparar tarefas periódicas.
- O envio de e-mail exige provedor próprio de entrega (por exemplo, Resend),
  independente de Supabase Auth, com limites e autenticação verificados.
- Não presumir que o e-mail cadastrado no login fornece SMTP gratuito.
- Verificar requisitos de domínio/remetente validado e custos reais de
  produção antes de escolher um fornecedor. Prioridade de **custo zero**.
- Segredos só em infraestrutura protegida; nunca no GitHub ou no navegador.
- Não incluir informações comerciais reais em e-mails antes de autorização
  da empresa e avaliação de proteção de dados e LGPD.
- Registrar job, mês de referência, destinatário, conteúdo permitido, status,
  horário, erro (sem segredos) e chave de idempotência única.
- Prever retry com limite e tratamento de falhas sem disparos duplicados.

## Critérios de aceite para a implementação futura

- Cada e-mail corresponde ao mês anterior e informa período/fonte dos dados.
- Quem não está ativo ou não foi autorizado **não recebe** relatório.
- Um usuário sem `finance.view` jamais recebe valores ou custos.
- Repetir a execução do job não duplica envios já concluídos.
- Falha do provedor não interrompe o painel nem expõe credenciais.
- O Master pode desabilitar a rotina e consultar o resultado de cada execução.
- Testar todos os cenários com dados fictícios antes de qualquer dado real.

## Ordem de trabalho

1. Estabilizar o MVP e migrar dados para Supabase PostgreSQL com RLS.
2. Implementar login, Master, permissões e auditoria.
3. Construir painel de insights e autenticação da aplicação web única.
4. Definir dados suficientes para fechar um mês corretamente.
5. Criar relatórios no painel, testar autorização e fórmulas.
6. Integrar agendamento e provedor de e-mail com configuração segura.
7. Testar envio, revogação de permissões, idempotência e falhas.
8. Ativar somente com destinatários e dados autorizados.

Referências a verificar na implementação: documentação oficial de Supabase Cron,
Edge Functions, Supabase Auth, regras RLS e limites/entrega do provedor de e-mail.

