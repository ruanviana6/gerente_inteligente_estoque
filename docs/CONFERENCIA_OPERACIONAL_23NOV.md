# Uso no trabalho — conferência operacional segura (23/11/2026)

## O que já pode fazer
No Gerente Flatter, entre como Master e vá a **Administração → Conferir lojas e situações da conta**. Selecione a loja correta e abra **Conferência para o trabalho — vendas observadas × saldo da fotografia**. Clique em **Consultar conferência operacional**. A aplicação lerá **somente o Supabase existente**, sem GET adicional no GestãoClick e sem alterar pedidos, produtos, saldos ou NF-e.

A lista mostra **uma única observação** (a mais recente salva) e a última fotografia de estoque da **mesma fonte e loja**, com respectivas datas explícitas. Os produtos são apresentados em ordem de unidades comerciais observadas. Utilize a lista para conferir manualmente os saldos no GestãoClick e organizar a revisão.

É possível exportar CSV para Excel, só com IDs, total de UN observadas e saldo da última fotografia. O CSV **não** contém dados de clientes, preços ou notas.

## Restrições
- Não chame isso de saldo atual, cobertura calculada, previsão ou sugestão de compra. Não há dados suficientes para fazer essa transformação com segurança.
- **Não** somar observações de janelas sobrepostas, e **não** descontar as unidades observadas do saldo da fotografia, pois as datas divergem e pode ocorrer baixa já refletida.
- Uma fotografia de estoque com mais de 24 horas aciona alerta explícito. Confirme os saldos diretamente no ERP antes de qualquer pedido.
- Não prova devoluções, pedidos excluídos, cobertura integral do token nem edição de linhas. `sales_lines_current` continua separado e a Reposição Inteligente permanece desativada para essa fonte.
- O módulo fiscal permanece DEMO; o painel não aciona emissão ou cadastro de NF-e.

## Primeiro uso
Na data de elaboração, a única fotografia de catálogo da loja 416023 é de 17/11/2026 (horário local) e a última observação cobre 16–22/11/2026. Ao usar em 23/11, trate o saldo como **referência antiga**, não como disponibilidade atual; confira o ERP para obter o saldo real.

## Segurança
- `demand_observation_review` usa `private.sync_source` com autorização Master e verifica correspondência exata de organização, fonte e loja.
- As tabelas continuam inacessíveis diretamente a `anon` e `authenticated`, usando RPC autenticada.
- A leitura não grava, reconcilia ou apaga nenhuma observação existente, e a função não combina períodos.
