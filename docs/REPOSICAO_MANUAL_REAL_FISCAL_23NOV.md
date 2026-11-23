# Reposição manual com produto real e triagem fiscal — 23/11/2026

## O que fica operacional nesta entrega

A tela **Reposição Inteligente** passa a oferecer uma simulação por produto da última fotografia real da loja. O Master escolhe a loja e produto, informa **saldo atual conferido no ERP**, **reservas**, **saída média diária**, **unidades reais por caixa**, **prazo total de chegada**, **estoque de segurança** e ciclo de 14 ou 21 dias. Todos os valores precisam ser preenchidos e confirmados explicitamente.

O motor existente calcula:
- necessidade líquida de unidades;
- arredondamento em caixas inteiras;
- dia de solicitação, data estimada de chegada e eventual ruptura anterior;
- justificativa do cálculo, usando cobertura alvo de 30 dias (maior entre cobertura e ciclo).

A operação apresenta resultado **condicional aos dados informados manualmente** e pode gerar CSV sem dados de clientes. Não altera banco, ERP, cadastro de produtos ou estoque. Não cria proposta interna automaticamente, porque o ID de produto do catálogo real ainda não foi reconciliado como SKU do motor. Não soma duas lojas, nem usa saldo histórico da fotografia como se fosse atual.

### Uso

1. Entrar como **Master**, abrir **Reposição Inteligente** e escolher a loja da fonte real.
2. Abrir **Planejar reposição com produto real e dados conferidos** e ativar o simulador.
3. Escolher um produto identificado por nome, categoria e ID.
4. Conferir no GestãoClick os valores do estoque e as reservas atuais, além da média de saída, embalagem, prazo e segurança. Zero só deve ser digitado se for realmente confirmado.
5. Confirmar a conferência, clicar em **Calcular reposição deste produto** e revisar risco de ruptura, caixas e unidades antes de solicitar à indústria.
6. Com `replenishment.export`, baixar a simulação por produto em CSV.

Ausência de demanda não significa zero de vendas. A média diária deve ser conhecida a partir de um registro confiável da operação; o sistema não a deduz automaticamente das janelas de observação já salvas. O algoritmo é um cenário determinístico, não uma previsão estatística validada. Quantidade real consolidada, emissão de pedido e integração de histórico continuam bloqueadas até a reconciliação.

## Faturamento: triagem de situação comercial

Na aba **Automação / Notas Fiscais**, a pesquisa real por número de venda passa a apresentar o estado de triagem comercial com as regras:
- **Em separação** e **Concretizada**: situação compatível com análise para futuro cadastro;
- **Em andamento**: exige aprovação identificada e auditada;
- qualquer outra situação: bloqueada;
- documento não localizado na janela: não comprova inexistência.

**Nenhum dos estados permite cadastro real ainda.** Ainda não foram homologados o destino CE, identidade do cliente e produtos na loja de destino, notas anteriores, NCM/CFOP/impostos, efeitos do cadastro no estoque, escopo do token e não envio automático. Não criar pedido/venda no varejo, não emitir nota automaticamente e não usar HTTP POST enquanto essas provas faltarem.

## Fora do escopo desta entrega

- Webhooks e atualização em tempo real ficam **por último**, conforme nova decisão do proprietário.
- F5/login fica na Issue #63.
- Publicação efetiva exige conferência no aplicativo do Streamlit. Um merge no GitHub não prova que ele atualizou.
- Cadastro fiscal real, sincronização histórica confiável e reposição completamente automática dependem de validação operacional adicional.
