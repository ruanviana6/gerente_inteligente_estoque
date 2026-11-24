# Ajuste mobile do quadro de Reposição — 24/11/2026

## Evidência
O Master conferiu na interface Streamlit da loja **416023** (24/11/2026, ~11h23 de Fortaleza):
- 245 produtos consultados no GestãoClick por GET;
- 245 com saldo retornado (unidade do ERP ainda a conferir);
- 116 produtos presentes na observação comercial da janela 16–22/11;
- nomes e categorias reais renderizados no quadro.

O quadro de sete colunas exigia deslocamento horizontal no celular: ao abrir, só nome e categoria estavam visíveis, deixando saldo e vendas fora da tela.

## Alterações
- A tela passa a exibir **detalhe vertical pesquisável de um produto**: nome, categoria, código, ID, saldo GET pontual e unidades observadas no período, sem rolagem horizontal.
- A tabela completa permanece disponível em um painel recolhível com aviso para deslizar horizontalmente, mantendo filtros, ordenação e CSV existente.
- Produtos sem observação apresentam um traço e mensagem **não significa venda zero**; saldo zero retornado é preservado como zero. Variações exigem conferência.
- O aviso sobre a natureza não operacional do quadro foi encurtado para ser legível em dispositivos pequenos, sem remover bloqueios de demanda/estoque.
- Sem nova consulta ERP, sem criação/edição de vendas, produtos, saldos ou NF-e, sem migrações e sem novas credenciais.
- Testes com Streamlit AppTest para visualizar saldo e vendas e preservar a distinção entre ausência da amostra e quantidade zero.

## Não concluído
Reposição automática continua bloqueada pela falta de histórico comercial totalmente reconciliado, devoluções, abrangência de API, reservas e conversão em caixas. Cadastro de NF-e real continua dependendo de homologação de campos e efeitos. Webhooks ficam para o final.
