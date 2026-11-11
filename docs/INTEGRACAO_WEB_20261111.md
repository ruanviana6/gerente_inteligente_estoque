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

As migrações pendentes do código são: snapshots por lote, sincronização de vendas,
Central de Acessos, regras de reposição e Inteligência Comercial. A aplicação
produtiva e o resultado de cada etapa devem ser registrados depois da execução.
Não restaurar backup por cima de dados novos nem executar DROP/purge automático.

## Verificação e limites

O ambiente local não permite abrir sockets, portanto o teste HTTP do Streamlit
falha com PermissionError. Também não contém scikit-learn. Os testes de HTTP, ML,
Auth real, PostgreSQL, navegador e dependências são executados no GitHub Actions,
com versões fixadas e dados fictícios. Um teste ignorado localmente não é contado
como aprovado. Consultar os checks do PR da integração para o resultado conjunto.

Na primeira observação anônima, o domínio respondeu HTTP 200, mas não exibiu o
login da aplicação ao navegador de teste. Isso não comprova publicação funcional.
É necessário observar o marcador da versão e validar os fluxos autenticados no
domínio. O proprietário pode precisar permitir acesso pela conta do Streamlit ou
efetuar o login, pois credenciais e sessão Master não são compartilhadas no chat.

Clientes/cidades reais e ML produtivo dependem do OpenAPI autorizado, finalidade
LGPD, histórico confirmado e auditoria. A conta Master existente não é recriada.
SMTP e relatórios externos continuam sujeitos à configuração e autorização. Não
foram ativados envio real, conexão GestãoClick, compras ou operações de NF-e.
