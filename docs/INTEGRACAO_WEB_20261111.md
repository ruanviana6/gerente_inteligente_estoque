# Integração da versão web em 11/11/2026

## Autorização e destino

O proprietário autorizou merges, deploy e migrações não destrutivas nesta data.
O destino continua `implementacao/web-supabase`, em
https://estoqueinteligente-flatter.streamlit.app/. Não é necessário recriar o
aplicativo. A `main` e as configurações/Secrets do Streamlit não são modificadas.

A integração concilia Central de Acessos (PR #12), Reposição Inteligente (PR #13),
Inteligência Comercial (PR #14) e compatibilidade de permissões (PR #15).
As suítes SQL de cada frente são mantidas, inclusive depois de restaurar o banco
descartável. Autenticação, revogação e os cálculos existentes são preservados.

## Correções durante a integração

Permissões futuras do banco não bloqueiam versões antigas: elas são ignoradas,
nunca concedidas. O rollout deve primeiro publicar essa compatibilidade. Um
marcador público e sem informações sensíveis identifica a versão no DOM.

O banco também mantém o contrato legado no RPC sem argumentos. A versão nova
declara as permissões que compreende e recebe somente a interseção com suas
concessões reais. Isso preserva o login mesmo se o Streamlit ainda estiver com
código antigo durante a atualização. Declarar uma permissão nunca a concede.

A cobertura alvo do SKU passou a ser considerada na reposição: usa o maior valor
entre alvo e ciclo. Recebimentos com data confirmada dentro da cobertura reduzem
a necessidade, sem ocultar déficits anteriores à sua chegada. Campos antigos sem
data continuam fora dessa projeção. Os downloads de estoque e indústria possuem
regressões de revogação durante a serialização, além das validações por clique.

O menu mantém uma Reposição Inteligente e uma Inteligência Comercial. Não duplica
a antiga área Vendas. A interface comercial lê páginas prontas de dez insights.
Publicação de resultados, treino e agregação incremental são processos explícitos,
separados da navegação. Detalhes em `INTELIGENCIA_COMERCIAL.md`.

## Backup e migrações

Foi capturada uma cópia lógica consistente das dezoito tabelas `public/private`
antes da integração, com mil e um registros, funções, colunas, constraints,
índices, grants, políticas e histórico de migrações. O arquivo está fora do Git,
criptografado com AES-256-GCM e protegido por permissões locais 0700/0600. A
decifragem foi conferida por igualdade e checksum. Cada uma das dezoito listas de
registros foi reconstituída pelo tipo PostgreSQL original, sem divergência.

Esse procedimento valida a leitura e reconstrução tipificada, não uma restauração
integral de desastre do Supabase. Não inclui cópia integral do Auth, objetos de
Storage ou Secrets externos. Nenhuma migração altera usuários/senhas do Auth ou
Secrets. Um checksum dos atributos estáveis do Master permite conferir sua
preservação. O teste completo de pg_dump/pg_restore usa somente dados fictícios
em PostgreSQL descartável, nunca dados reais no GitHub Actions.

Foram aplicadas no projeto existente as migrações de snapshots por lote,
sincronização de vendas, Central de Acessos, regras de reposição e Inteligência
Comercial. Depois, a migração aditiva `20261111122840_sessoes_persistentes.sql`
foi aplicada para permitir retomada do login. O histórico contém dez migrações,
com as versões alinhadas aos arquivos do repositório.

Antes da última migração, um segundo backup cifrado cobriu as 37 tabelas
`public/private`, com 1.015 registros. A decifragem e a reconstrução tipificada
foram exatas. Após a migração, todas as 37 tabelas anteriores permaneceram
idênticas e o checksum dos atributos protegidos do Master/Auth foi preservado.
A nova tabela de sessões tem RLS ativa e não permite leitura direta por `anon`
ou `authenticated`. Não restaurar backup por cima de dados novos nem executar
DROP/purge automático.

## Verificação e limites

A integração foi incorporada à branch publicada pelos PRs #16, #17 e #18. No head
`0fb0b94`, o GitHub Actions aprovou 871 testes e 66 subtestes em Python 3.11/3.12,
Ruff, dependências, PostgreSQL/RLS/restauração e sete testes de Auth/PostgREST
reais em ambiente descartável. Os testes de ML usam scikit-learn instalado no CI.
O Chromium aprovou retomada após F5, reabertura de aba, isolamento de navegadores,
Sair, conta desativada, opção desmarcada e F5 no celular. São validações com dados
fictícios; não representam login Master em produção. Referências:
https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/43031175890
e https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/43031175853.

A checagem independente do domínio identificou a moldura do Streamlit Cloud:
o aplicativo está no iframe da mesma origem em `/~/+/`. O verificador foi
corrigido para observar esse quadro e possui regressões que rejeitam marcadores
em origens de terceiros. A falha do script externo de analytics da plataforma
não é usada como evidência de falha da aplicação.

Inicialmente o processo do Streamlit ainda servia o código antigo. A hospedagem
informou `isOwner=false` para a sessão disponível, sem acesso para reiniciar o
processo. O proprietário executou Reboot app e confirmou o reinício no chat.
Não foi preciso mudar branch, recriar o aplicativo ou editar Secrets.

Em 11/11/2026 às 13:47:32 UTC, o navegador do GitHub Actions observou o release
`web-integrada-20261111.2` no domínio real. Uma segunda execução independente no
Chromium do ambiente confirmou o mesmo release e a opção de manter acesso. Ambas
validaram login em 1440 e 390 pixels, ausência de rolagem horizontal, nenhuma
tabela/indicador sem autenticação e recusa de uma credencial fictícia inexistente.
A logo como imagem e a paleta clara também foram conferidas nas capturas.
Resultado e capturas anônimas estão no artefato `verificacao-publicada`:
https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/43031780655.
O resumo sem dados pessoais está em `evidencias/publicacao-20261111.json`.

Isso comprova a atualização da aplicação e esses fluxos públicos. Não representa
login Master ou validação de operações com dados empresariais na URL. Sessão,
senha e Secrets do proprietário não foram solicitados nem compartilhados. A
versão integrada está pronta para a auditoria independente da Issue #3.

Clientes/cidades reais e ML produtivo dependem do OpenAPI autorizado, finalidade
LGPD, histórico confirmado e auditoria. A conta Master existente não é recriada.
SMTP e relatórios externos continuam sujeitos à configuração e autorização. Não
foram ativados envio real, conexão GestãoClick, compras ou operações de NF-e.
