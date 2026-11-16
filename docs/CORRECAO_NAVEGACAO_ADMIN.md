# Correção da navegação administrativa

Ao abrir Integrações, Auditoria, Importação manual / DEMO ou Regras de Reposição
pela Administração, o Streamlit interrompia a tela com
`StreamlitDuplicateElementKey`. O cabeçalho da Administração e o da subárea
usavam a mesma chave `flatter_page_header` na mesma execução.

O cabeçalho administrativo agora possui identidade própria. O componente
valida sua chave e mantém o mesmo estilo para os cabeçalhos pai e filho.
Os títulos, a paleta Flatter, as permissões e os formulários permanecem iguais.
Não foi necessário alterar dados, Secrets, migrações, cálculos ou operações do ERP.

O teste anterior de Integrações abria a página isoladamente. A regressão agora
executa `_administracao` de verdade, com os cabeçalhos e o roteamento reais,
incluindo troca de áreas, rerun, confirmação separada, revogação e permissões
granulares. Só os dados, Auth/RPC e transporte usam respostas fictícias.
A fixture de navegador também passou a exercitar essa composição administrativa
nas larguras de computador e celular, em vez de chamar Integrações isoladamente.

Validação local: 97 testes focados e Ruff aprovados. A suíte completa, os
fluxos de navegador e a publicação são verificados pelos workflows do PR.
O teste publicado de login anônimo não comprova sozinho as áreas autenticadas;
os testes de composição e o uso pelo Master complementam essa verificação.

A correção prepara a versão `gestaoclick-conexao-20261113.2`. É preciso observar
essa versão no site antes de considerar o código atualizado. A consulta real
do GestãoClick ainda exige o diagnóstico Master; este reparo não ativa carga
automática, cadastro fiscal real ou emissão de notas.
