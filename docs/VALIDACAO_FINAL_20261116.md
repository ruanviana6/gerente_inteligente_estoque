# Validação da versão web em 16/11/2026

Esta revisão foi feita com assistência de IA, usando leitura de código, testes
fictícios e inspeção autorizada de metadados do projeto Supabase existente.
Não foram consultados payloads empresariais nem modificados dados, usuários,
Secrets ou registros do ERP. Os resultados desta revisão não equivalem a
homologação da integração real.

## Estado comprovado

- A branch publicada é `implementacao/web-supabase`. Os PRs #29 e #30 foram
  integrados, incluindo correção de paginação e prévia nativa sem mapas JSON.
- A revisão `8290f877` passou em 1.809 testes e 66 subtestes no GitHub Actions,
  em Python 3.11 e 3.12. Banco, Auth descartável, RLS, concorrência, restauração,
  navegador, desempenho, dependências e executor fiscal DEMO passaram.
- Na verificação pública dessa revisão, o site ainda apresentou a versão
  `gestaoclick-configuracao-20261116.2`, com login visível e sem tabelas ou
  indicadores para visitantes. O teste de publicação falhou por não encontrar
  a versão esperada. HTTP 200 não foi tratado como comprovação de atualização.
- As 14 migrações locais coincidem com as 14 aplicadas ao projeto Supabase.
  Não foi identificada migração pendente para este incremento.
- O projeto respondeu saudável. Todas as 45 tabelas conferidas em `public` e
  `private` têm RLS, sem SELECT anônimo ou escrita direta de `authenticated`.
  Não há funções `SECURITY DEFINER` públicas; os definers inspecionados fixam
  `search_path`. Os corpos de 21 funções sensíveis coincidem com as migrações.
- A conta Master está preservada. Não há automação configurada ou ativa nem
  execução de sincronização concluída. Registros pendentes foram preservados.
- O executor `fiscal-demo` está ativo com verificação de JWT; não foi invocado
  em produção nesta revisão. Sua presença não comprova uma operação fiscal real.

Evidências da revisão base:
[testes e banco](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/43941593910),
[navegador e desempenho](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/43941593536),
[verificação pública](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/43941593927).
Os resultados da correção abaixo ficam vinculados ao seu PR e respectivo commit,
sem reutilizar a contagem da revisão anterior como se fosse um teste novo.

## Correções desta revisão

A prévia descartava a unidade disponibilizada pelo contrato oficial. Agora
preserva `sigla_unidade`, mostra ausência explicitamente e não converte uma
quantidade de caixas em unidades avulsas. Também separa o código comercial
textual do ID técnico: um código fictício `2025/001` não deve ser recusado pela
regra destinada ao identificador. Mantém validações de comprimento e controles.

As regressões verificam unidade conhecida, ausente e inválida, preservação da
quantidade, pontuação do código comercial, ID técnico estrito e projeção sem
valores financeiros ou dados pessoais. A mudança não adiciona chamadas HTTP,
persistência, treinamento, migrações ou permissões.

## Limites e pendências de conclusão

| Requisito | Evidência disponível | Pendência real |
| --- | --- | --- |
| Login persistente, permissões e isolamento | Testes Python, Chromium, Auth e RLS descartáveis; inspeção das funções instaladas | Conferência autenticada no site pelo titular; não foi usada sessão Master nesta revisão |
| Estoque, reposição, relatórios e exportações | Suítes do motor, serviços, SQL, interface e navegador | Catálogo e saldo do motor ainda entram por CSV |
| Inteligência Comercial e ML | Modelos, baselines, processos controlados e leitura de resultados prontos testados | Validação com histórico autorizado; nenhum treino na navegação |
| Prévia nativa GestãoClick | GET limitado, autorização, projeção, desconhecidos e testes negativos | Conferir loja efetiva, documentos, unidades e situações da conta real |
| Sincronização automática | Executor, concessões, janelas de até 732 dias e banco testados com fixtures | Adaptar persistência nativa e devoluções, reconciliar dados e configurar executor aprovado |
| Automação fiscal | Fluxo DEMO e executor testados em ambiente descartável | Cadastro real não habilitado; emissão permanece exclusivamente manual no ERP |
| Publicação | URL pública testada e versão observada | Atualizar o processo do Streamlit e comprovar a versão esperada |

O modelo canônico de importação ainda exige informações ausentes no schema
público de vendas: SKU, identidade estável da linha, devoluções e versão da
origem. O banco recusa alterações sem timestamp posterior comprovado. Resolver
essas diferenças é trabalho de implementação; não é algo que o proprietário
deva contornar inventando mapas ou valores nos Secrets. A prévia nativa não é
promovida automaticamente a esse modelo.

## Segurança e operação

Os avisos informativos de RLS sem políticas correspondem a tabelas fechadas por
grants e RPCs controladas; não foram criadas políticas permissivas para silenciá-los.
O aviso de proteção contra senhas vazadas desativada permanece como limitação:
a [documentação oficial](https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection)
informa que o recurso exige plano Pro ou superior. Não foi contratado serviço pago.

O reinício do Streamlit exige acesso de proprietário ao painel, indisponível
nesta sessão. Deve manter aplicativo, branch e Secrets. Depois, a versão será
conferida novamente no endereço real. A conclusão funcional exige também a
reconciliação autenticada da amostra e o encerramento das lacunas de importação.
Não se declara o projeto finalizado enquanto essas etapas permanecerem pendentes.


## Revalidação da publicação após reinício em 16/11/2026

O teste de publicação que falhou no primeiro horário foi **reexecutado**
depois do reinício da aplicação no Streamlit Community Cloud, sem mudanças
nos Secrets, dados ou contas do projeto.

- Workflow: [Verificação da versão publicada, execução 43949821807,
  tentativa 2](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/43949821807).
- Resultado: **aprovado**. A versão servida na URL real foi
  `gestaoclick-unidades-20261116.5`, igual à versão esperada no código.
- O formulário de login apareceu no desktop e no celular (390 px);
  uma credencial intencionalmente inválida foi recusada.
- Nenhuma tabela ou métrica foi exibida a visitante sem autenticação;
  o teste não encontrou exceção pública visível.
- A tentativa 1, anterior ao reinício, permanece registrada como
  **reprovada** porque observou `gestaoclick-configuracao-20261116.2`.
  Não houve alteração de critério ou aceitação de HTTP 200 isolado.

**Alcance da evidência:** somente publicação e barreira anônima.
Não foram testados login Master, páginas autenticadas, importação real,
abrangência do histórico, situação das vendas na conta, sincronização
efetiva, e-mail de relatórios ou cadastro fiscal produtivo.
A aprovação do navegador não ativa nenhum desses fluxos.

**Pendências de uso real:** concluir o contrato e a conciliação de
catálogo/SKU, identidade de linhas, devoluções, versões e loja; validar
uma amostra autorizada no ERP; só então considerar ativação agendada,
com as credenciais e aprovação do Master. Fiscal permanece DEMO.
