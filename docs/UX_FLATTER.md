# Interface Flatter

A Issue #6 reorganiza a aplicação web existente em Streamlit. As páginas usam o mesmo Supabase, as mesmas permissões e o motor de estoque validado. A interface não integra nem modifica o GestãoClick.

## Organização

- `.streamlit/config.toml`: cores, fontes do sistema, bordas e configuração nativa dos widgets.
- `web/theme.py`: imagem local da marca, cabeçalho, cartões de indicadores, superfícies e status. Não consulta dados nem guarda estado de usuários.
- `web/ui.py`: entrada, login, sessão, monitoramento de autorização e navegação.
- `web/pages.py`: páginas operacionais, filtros, detalhe, relatórios e administração.
- `web/downloads.py`: preparação autorizada e entrega direta do CSV à página.

O CSS complementar usa as classes `st-key-` documentadas para containers. Não depende de hashes, `data-testid`, posição dos elementos ou scripts de terceiros. `st.html` recebe apenas CSS estático. Produtos, fornecedores, nomes de pessoas e indicadores passam pelos widgets nativos, sem interpolação em HTML.

## Cores e marca

| Uso | Cor |
| --- | --- |
| Fundo | `#FBF8F6` |
| Barra lateral | `#F4ECE8` |
| Cartões | `#FFFFFF` |
| Ação e fundo da marca | `#9C4438` |
| Texto principal | `#2D1C25` |
| Texto secundário | `#66575F` |
| Links e destaque em texto | `#8B332E` |
| Destaque ameixa | `#61334D` |
| Borda de controles | `#8A727A` |

O terracota da Flatter, o vinho do Êxtase e o ameixa do Êxtase Elixir orientam a paleta. As superfícies claras deixam os indicadores legíveis; ações terracota usam texto branco. Status também mostram nomes, como risco e atenção, sem depender exclusivamente de cor.

A entrada e a barra lateral exibem a imagem local `assets/flatter-logo.jpg`, carregada por `st.image` com descrição acessível. O arquivo é uma cópia sem alterações da imagem do [perfil público da Flatter](https://graph.facebook.com/323978121026634/picture?type=large), que apresenta uma variante azul sobre branco. Na interface, filtros CSS a apresentam em branco sobre terracota; nenhum recurso externo é carregado durante o uso. A imagem mostrada pelo usuário na conversa não estava disponível como arquivo no ambiente, portanto esta variante pode ser substituída posteriormente pelo arquivo exato da identidade desejada.

Para alterar as cores, ajuste `.streamlit/config.toml`, `PALETTE` e as constantes CSS de `web/theme.py` em conjunto. Execute `python -m pytest tests/test_flatter_theme.py` e a validação visual antes de aceitar a alteração. Evite clarear apenas o fundo ou escurecer apenas o texto, pois isso pode comprometer o contraste.

Para usar outra versão autorizada da marca, substitua o arquivo local e ajuste os filtros de `brand_header` conforme a imagem. Preserve um nome acessível, uma largura limitada ao container e uma alternativa textual. Não carregue logos remotos nem use recortes de screenshots.

## Acessibilidade e celular

Os componentes nativos mantêm rótulos e navegação por teclado. O complemento visual fornece foco visível e altura mínima de 44 px para botões, campos e seletores dentro dos containers Flatter. Botões também têm largura mínima de 44 px, incluindo a ação de mostrar ou ocultar a senha. A navegação reserva altura de toque de 44 px. A barra lateral é recolhível; linhas horizontais usam quebra nativa do Streamlit, e tabelas mantêm sua própria rolagem. Os grupos de indicadores usam `st.columns(..., wrap=True)` e cartões com `width="stretch"`, formando uma grade no desktop e ocupando a largura disponível quando empilhados no celular.

As razões abaixo foram calculadas localmente com a fórmula de luminância relativa sRGB do WCAG. São verificações da paleta, não uma certificação de acessibilidade da aplicação inteira.

| Combinação | Contraste |
| --- | --- |
| Texto principal / cartão | 16,10:1 |
| Texto secundário / cartão | 6,79:1 |
| Destaque em texto / cartão | 8,04:1 |
| Branco / botão terracota | 6,37:1 |
| Borda de controle / cartão | 4,40:1 |

Textos normais têm meta mínima de 4,5:1; bordas necessárias para identificar controles, 3:1. As bordas decorativas dos cartões não identificam campos editáveis e usam um tom mais discreto.

## Validação e limites

`tests/test_flatter_theme.py` verifica contraste, consistência da configuração, proteção dos identificadores e ausência de HTML dinâmico nos componentes. A navegação e o layout precisam também passar pelos testes de interface e pelo navegador em 390 px e 1440 px, com dados fictícios. Consulte o resultado efetivamente executado no PR e no GitHub Actions; a presença dos scripts não comprova aprovação.

O roteiro `scripts/validar_interface_flatter.py` registra screenshots da entrada e das páginas principais, verifica transbordamento horizontal e exercita perfis distintos. As imagens são evidências de teste fictício, sem credenciais ou dados empresariais. Após alterações nas versões do Streamlit, repita essa verificação porque os widgets e a interpretação do tema podem mudar.

A nova interface só será publicada após revisão e autorização do proprietário. Antes de usar dados empresariais, executar a auditoria independente da Issue #3.

## Referências

- [Tema e cores no Streamlit](https://docs.streamlit.io/develop/concepts/configuration/theming-customize-colors-and-borders)
- [Containers e classes associadas à chave](https://docs.streamlit.io/develop/api-reference/layout/st.container)
- [Critério de contraste WCAG](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)

## Exportação e revogação

Use **Preparar CSV** para gerar o arquivo, depois **Salvar CSV** para salvá-lo. A preparação reconsulta identidade e permissões antes e depois do cálculo. O arquivo segue pela conexão da própria página, em um link `data:` dentro de um iframe nativo, com nome fixo e conteúdo codificado em base64. Nenhum nome ou texto importado é interpolado como HTML. Não há callback ou arquivo registrado no gerenciador global de downloads do Streamlit.

Essa escolha evita depender do isolamento de callbacks diferidos do Streamlit 1.66.1. Uma sessão diferente não recebe um identificador global que execute a geração em nome de outra pessoa. Dados já entregues ao navegador, como qualquer arquivo baixado, não podem ser apagados retroativamente por revogação. Toda nova preparação exige nova autorização.

A opção `client.disableDataExport` oculta os controles de CSV e cópia das tabelas, concentrando a exportação nos fluxos autorizados. Ela não impede a extração de informações que o próprio usuário já recebeu na tela.

## Navegação de vendas e integração

Inteligência de Vendas usa trends.view, uma fonte/loja por análise, filtros em formulário e geração explícita. Integrações exige Master e integrations.manage, com prévia separada da confirmação. As telas compartilham o tema Flatter. scripts/validar_interface_vendas.py verifica oito capturas e os fluxos DEMO em 390/1440 px; a fixture nunca é a entrada app.py.
