# Revalidação de janela comercial por duas consultas GET — 24/11/2026

## Por que foi acrescentada

A tela de Reposição já exibe a consulta real dos **245 produtos** da loja 416023 e **116 com vendas fechadas observadas**, mas a API ainda não comprovou histórico de exclusões, devoluções, IDs estáveis de linhas ou datas de modificação. Uma fotografia de saldos e uma amostra de vendas não autorizam automaticamente a média diária para pedido à indústria.

## Fluxo de leitura Master

1. Em **Administração → Integrações → Auditar todas as vendas do período e conciliar produtos**, selecionar loja, período de até sete dias, situações de **Em separação** e **Concretizada**, autorizar e executar a auditoria GET integral existente.
2. A aplicação guarda **somente hashes de identidades e itens**, separados da situação e data, com sal aleatório e por até **15 minutos na sessão Master**. Não guarda números comerciais, nomes de clientes ou pedidos brutos no navegador, banco ou logs. Não persiste nova venda.
3. Na interação seguinte com a tela, abrir **Comparar novamente as vendas desta janela**, marcar a autorização específica e clicar **Reconsultar e comparar vendas**.
4. A API faz **outra leitura GET completa** da **mesma loja**, **mesmo período** e **mesma fonte**; identidade, permissões, configuração e prazo são revalidados.
5. O resultado mostra contagens de documentos que **apareceram**, **deixaram de aparecer**, mudaram de **situação**, tiveram **itens/quantidades/unidades alterados** ou **data alterada**. Não apresenta clientes nem identifica pedidos individualmente.
6. Mudanças de ordem dos itens ou das páginas não provocam falsa divergência. Nenhuma comparação é realizada entre lojas ou períodos diferentes.

## Limites críticos

- **Deixou de aparecer ≠ cancelado/excluído.** A causa pode ser alteração de data, permissão, visibilidade ou falha no contrato. Verificar no ERP.
- **Duas consultas iguais ≠ cobertura comprovada.** Ainda faltam devoluções, IDs de linha e timestamp de atualização; a conferência não cria ledger canônico e não prova ausência de pedidos ocultos.
- Não habilita estoque, previsão, recomendações, compra, nota fiscal nem escrita no ERP; não subtrai vendas do saldo atual.
- O GET adicional exige nova confirmação explícita e respeita limites de sete dias, mil documentos, paginação e revalidação em cada etapa.
- A referência expira e é descartada quando o usuário troca de loja, perde permissão ou muda de sessão.
- Sem banco novo, Secrets, cron, serviços pagos ou webhooks.

## Validação

Testes provam detecção de novas vendas, ausências, mudanças de status, item, unidade e data; igualdade independente da ordem, proteção por loja/período/sal, contagem íntegra e referência expirada. Somente após verificadores GitHub aprovados e publicação Streamlit confirmada o recurso estará utilizável em produção.

Próxima etapa para reposição realmente automatizada: homologar semântica das exclusões/devoluções e atualização de linhas na API, reconciliar dados por loja e unidade, confirmar reservas/embalagens/lead time e, só então, liberar sugestão operacional com justificativa. Cadastro real de NF-e segue exigindo homologação fiscal independente; webhooks ficam por último.
