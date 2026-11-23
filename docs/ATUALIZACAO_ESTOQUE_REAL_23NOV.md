# Atualização identificada da fotografia de Estoque — 23/11/2026

## Resultado operacional

Na aba **Estoque e histórico**, o Master pode consultar o catálogo real da **mesma loja da fotografia salva**, conferi-lo e salvar um novo snapshot analítico. A tela mostra nome, categoria, código interno, ID de produto e quantidade, sem preencher campos ausentes com dados fictícios.

1. Abrir **Estoque e histórico** e localizar **Atualizar produtos, categorias e saldos da loja**.
2. Autorizar a conferência de lojas e situações e clicar em **Conferir conexão e loja do estoque**.
3. Verificar o nome e ID fixos da loja. A ausência da loja no retorno bloqueia a operação.
4. Abrir **Conferir catálogo completo e histórico de estoque da loja**. Autorizar os GETs completos e clicar em **Conferir catálogo completo**.
5. Conferir no GestãoClick a abrangência, nomes, categorias, quantidade de produtos, saldos e avisos. Confirmar expressamente as duas caixas de autorização para salvar a nova fotografia no Supabase.
6. A tela atualiza o snapshot com `st.rerun()`, sem solicitar outro login, sem recarregar o navegador e sem outro GET ao ERP.
7. Usuários com permissão `inventory.export` podem baixar **Exportar catálogo real identificado (CSV)**, com referência explícita da loja e data da fotografia. O CSV é UTF-8/BOM e protege os rótulos contra fórmulas em planilhas.

A fotografia no banco só muda com consentimento explícito e após todos os dados serem reconciliados. Nenhum produto, estoque, preço, venda ou NF-e muda no GestãoClick; o motor operacional continua separado.

## Limitações e segurança

- A fotografia é **histórica**, não saldo ao vivo; cada nova consulta é um lote de leitura integral. A abertura da página nunca aciona consultas ao ERP.
- Não misturar estoque de diferentes lojas; o fluxo da aba Estoque usa exclusivamente a loja da fonte real já selecionada.
- Identificações ausentes no ERP continuam `Não informado`. Não inventar categoria pelo código interno.
- A atualização não habilita automaticamente reposição por demanda, emissão, cadastro real de nota ou vendas consolidadas. Para isso seguem necessárias validações de cobertura e contratos.
- Consulta e salvamento exigem identidade Master, `integrations.manage`, autorização explícita e assinatura de sessão válida. Outros perfis não veem o fluxo.
- A edição do login após F5 está pendente na Issue #63, fora desta entrega. `st.rerun()` é uma atualização interna do Streamlit, não F5.
