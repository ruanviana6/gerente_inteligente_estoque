# Permanência do login

A opção **Manter acesso neste navegador por até 30 dias** permite retomar a conta
após atualizar a página, fechar uma aba ou reabrir o navegador. É destinada a um
dispositivo pessoal. Sair remove o cookie e encerra a sessão; desativação, corte
de sessões, redefinição de acesso e expiração impedem a retomada no banco.

O cookie contém somente um identificador aleatório e um validador de 256 bits.
Não contém nome, e-mail, senha ou tokens Supabase. Tem prefixo `__Host-`, caminho
`/`, `Secure`, `SameSite=Strict` e validade limitada. Não usa URL, localStorage ou
arquivo local. A senha nunca é persistida pelo aplicativo.

Os tokens Supabase ficam cifrados com AES-256-GCM na tabela privada
`persistent_sessions`, sem grants diretos para os papéis da API e com RLS ativa.
O conteúdo é vinculado ao usuário, organização e dispositivo. O segredo já
existente `SUPABASE_BACKEND_SECRET` deriva a chave desta finalidade e também
autoriza os RPCs do backend. Não há uso de `service_role` nesta rotina, nova
conta ou novo serviço. Rotacionar o segredo exige novo login nos navegadores.

A retomada verifica conta ativa, sessão real em `auth.sessions`, primeiro acesso
concluído, organização, expiração e cortes da Central de Acessos. O backend
confere o usuário no Supabase Auth e consulta novamente as permissões. Uma
concessão no banco serializa refresh entre abas/instâncias; tokens atualizados
substituem o conteúdo anterior, sem histórico redundante. Falha transitória de
rede não equivale a revogação. Não se prolonga o prazo absoluto a cada retomada.

Há no máximo dez sessões lembradas ativas por usuário e duas mil referências
por organização. O limite impede crescimento indiscriminado; atingido, o login
normal continua possível e a lembrança exige revisão. Nenhuma venda, snapshot
ou informação comercial é apagada por esta funcionalidade.

## Limite do Streamlit Community Cloud

Streamlit oferece leitura dos cookies da conexão inicial, mas não permite
definir um `Set-Cookie` próprio com `HttpOnly`. Um iframe local escreve apenas a
referência opaca na mesma origem. Portanto esse cookie é acessível ao JavaScript
da própria origem e não oferece proteção HttpOnly contra uma eventual XSS. Os
tokens continuam cifrados e fora do navegador. Manter HTML estático, dependências
auditadas e nenhum dado externo interpolado em scripts é parte da proteção.
Não confundir essa limitação com armazenamento de JWT ou senha no navegador.

Janela anônima, limpeza/bloqueio de cookies e troca de dispositivo podem exigir
novo login. Não alterar CORS, XSRF ou privacidade do aplicativo para contornar
essas restrições. O prazo de 30 dias é para retomar; as permissões são conferidas
em cada operação e a sessão Supabase pode ser encerrada antes por segurança.

## Ativação e validação

A migração `20261111122840_sessoes_persistentes.sql` é aditiva. Preserva a conta
Master, seus atributos e os Secrets. O backend deve ter o segredo já registrado
no projeto nos Secrets protegidos. Sem isso, o login normal funciona, mas a tela
informa que não conseguiu lembrar o navegador. Não informe o segredo no chat.

A validação executada cobre criptografia, retomada, rotação entre abas, negações,
RLS, expiração, revogação e Auth/PostgREST real em ambiente descartável. O
Chromium aprovou F5, fechar/reabrir aba, isolamento de navegadores, Sair,
desativação, opção desmarcada e F5 no celular. Os resultados estão no
[CI da integração](https://github.com/ruanviana6/gerente_inteligente_estoque/actions/runs/43031175853).

A migração foi aplicada ao projeto existente após backup e preservação dos
registros/Master. Em 11/11/2026, a opção de manter acesso e o release
`web-integrada-20261111.2` foram observados no domínio publicado. O teste público
recusou credencial fictícia inválida, sem entrar como Master. O ciclo de retomada
produtiva com a conta real ainda depende da conferência do proprietário.

Na primeira entrada após a atualização, autentique-se normalmente e deixe a
opção marcada para lembrar esse navegador. Uma sessão antiga sem referência de
dispositivo não pode ser retomada retroativamente.

Referências: [contexto de cookies do Streamlit](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.context)
e [sessões do Supabase](https://supabase.com/docs/guides/auth/sessions).
