# Interface Flatter

A Issue #6 reorganiza a aplicação web existente em Streamlit. As páginas usam o mesmo Supabase, as mesmas permissões e o motor de estoque validado. A interface não integra nem modifica o GestãoClick.

## Organização

- `.streamlit/config.toml`: cores, fontes do sistema, bordas e configuração nativa dos widgets.
- `web/theme.py`: marca em texto, cabeçalho, cartões de indicadores, superfícies e status. Não consulta dados nem guarda estado de usuários.
- `web/ui.py`: entrada, login, sessão, monitoramento de autorização e navegação.
- `web/pages.py`: páginas operacionais, filtros, detalhe, relatórios e administração.

O CSS complementar usa as classes `st-key-` documentadas para containers. Não depende de hashes, `data-testid`, posição dos elementos ou scripts de terceiros. `st.html` recebe apenas CSS e marca estáticos. Produtos, fornecedores, nomes de pessoas e indicadores passam pelos widgets nativos, sem interpolação em HTML.

## Cores e marca

| Uso | Cor |
| --- | --- |
| Fundo | `#101720` |
| Barra lateral | `#151E2B` |
| Cartões | `#1C2937` |
| Ação e fundo da marca | `#B55440` |
| Texto principal | `#F5F7FA` |
| Texto secundário | `#B8C2CE` |
| Links e destaque em texto | `#D97963` |
| Borda de controles | `#6C7A8B` |

O terracota original é usado como fundo de ação, com texto claro. Textos terracota sobre os cartões usam a variação mais clara `#D97963` para manter contraste. Status também mostram nomes, como risco e atenção, sem depender exclusivamente de cor.

O arquivo original da marca não estava disponível nesta implementação. A entrada mostra **Flatter como texto branco em fonte serifada do sistema**, sobre terracota. Isso é uma identificação textual provisória, não uma reprodução do logo oficial.

Para alterar as cores, ajuste `.streamlit/config.toml`, `PALETTE` e as constantes CSS de `web/theme.py` em conjunto. Execute `python -m pytest tests/test_flatter_theme.py` e a validação visual antes de aceitar a alteração. Evite clarear apenas o fundo ou escurecer apenas o texto, pois isso pode comprometer o contraste.

Quando houver um arquivo oficial autorizado, adicione o arquivo local em `assets/` e troque somente a apresentação em `brand_header`. Preserve um nome acessível, uma largura limitada ao container e uma alternativa textual. Não carregue logos remotos nem use recortes de screenshots.

## Acessibilidade e celular

Os componentes nativos mantêm rótulos e navegação por teclado. O complemento visual fornece foco visível e altura mínima de 44 px para botões, campos e seletores dentro dos containers Flatter. A navegação também reserva altura de toque de 44 px. A barra lateral é recolhível; linhas horizontais usam quebra nativa do Streamlit, e tabelas mantêm sua própria rolagem.

As razões abaixo foram calculadas localmente com a fórmula de luminância relativa sRGB do WCAG. São verificações da paleta, não uma certificação de acessibilidade da aplicação inteira.

| Combinação | Contraste |
| --- | --- |
| Texto principal / cartão | 13,76:1 |
| Texto secundário / cartão | 8,19:1 |
| Terracota claro / cartão | 4,83:1 |
| Texto principal / botão terracota | 4,54:1 |
| Branco / fundo da marca | 4,88:1 |
| Borda de controle / cartão | 3,37:1 |

Textos normais têm meta mínima de 4,5:1; bordas necessárias para identificar controles, 3:1. As bordas decorativas dos cartões não identificam campos editáveis e usam um tom mais discreto.

## Validação e limites

`tests/test_flatter_theme.py` verifica contraste, consistência da configuração, proteção dos identificadores e ausência de HTML dinâmico nos componentes. A navegação e o layout precisam também passar pelos testes de interface e pelo navegador em 390 px e 1440 px, com dados fictícios. Consulte o resultado efetivamente executado no PR e no GitHub Actions; a presença dos scripts não comprova aprovação.

O roteiro `scripts/validar_interface_flatter.py` registra screenshots da entrada e das páginas principais, verifica transbordamento horizontal e exercita perfis distintos. As imagens são evidências de teste fictício, sem credenciais ou dados empresariais. Após alterações nas versões do Streamlit, repita essa verificação porque os widgets e a interpretação do tema podem mudar.

A nova interface só será publicada após revisão e autorização do proprietário. Antes de usar dados empresariais, executar a auditoria independente da Issue #3.

## Referências

- [Tema e cores no Streamlit](https://docs.streamlit.io/develop/concepts/configuration/theming-customize-colors-and-borders)
- [Containers e classes associadas à chave](https://docs.streamlit.io/develop/api-reference/layout/st.container)
- [Critério de contraste WCAG](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
