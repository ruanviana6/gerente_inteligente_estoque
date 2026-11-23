# Correção do seletor de produtos reais na Reposição — 23/11/2026

## Evidência e causa

A tela de Reposição exibia repetidamente `Não informado · Não informada · ID 625...` porque a fotografia antiga do Supabase tem IDs e saldos, porém os campos `nome` e `categoria` estão nulos. Uma conferência agregada de 23/11 confirmou **490 linhas de fotografias históricas, nenhuma com nome ou categoria**. Os saldos são observações datadas, não estoque vivo.

A consulta GET `/produtos` já interpreta `nome` e os campos permitidos de grupo/categoria. A migração existente `20261123004500_catalogo_nome_categoria_reais.sql` aceita e armazena esses rótulos quando o ERP os devolver. Os registros antigos NÃO são enriquecidos automaticamente: uma nova fotografia autorizada precisa ser salva. O código não pode deduzir rótulo de ID.

## Alteração nesta entrega

- O seletor de **Reposição Inteligente** mostra `Nome · Categoria · ID · Saldo histórico: N UN` somente para produtos que efetivamente têm nome.
- Se todos os nomes estiverem ausentes, o simulador bloqueia a seleção de 245 IDs anônimos e mostra uma tabela limitada com IDs e **saldos na fotografia (históricos)**. O usuário ganha um botão de navegação para **Estoque e histórico**.
- Se houver alguns nomes, os produtos identificados continuam utilizáveis; aparece a contagem de rótulos ausentes.
- O saldo **atual** permanece um campo obrigatório do cálculo. O valor histórico exibido nunca é preenchido automaticamente como saldo atual, nem vira uma proposta com dados incertos.
- A aba Estoque mostra a sequência correta de atualização diretamente em seu painel, sem redirecionamento desnecessário à Administração.
- Testes verificam exibição de nome/categoria/ID, valores inteiros e decimais, fotografias legadas e identificação parcial.

## Procedimento necessário após publicar

Como Master, abra **Estoque e histórico**, selecione a loja correta, consulte a conta e o catálogo completos por GET, confira o contador de nomes e categorias informados pela API, e só então confirme a gravação da nova fotografia no Supabase. Se a API continuar sem devolver rótulos, pare e compare a resposta/cadastro do produto pelo diagnóstico protegido, sem compartilhar dados privados ou tokens. Salvar uma fotografia com nomes ausentes não resolverá a identificação.

O aplicativo não alterou o ERP nem modificou registros anteriores. A integração fiscal e os webhooks não são afetados. O deploy precisa passar pelo verificador público antes de declarar a versão disponível.
