# Quadro real da loja inteira — Reposição Inteligente (23/11/2026)

## Entrega

A Reposição passa a reunir, em um quadro único e com autorização explícita do Master, o **catálogo completo e os saldos consultados por GET no GestãoClick** com a **última observação comercial já salva no Supabase**. O sistema reaproveita as duas integrações homologadas em modo somente leitura, sem novos Secrets ou migrações.

### Fluxo

1. Abrir **Reposição Inteligente**, escolher a loja real autorizada e expandir **Conferir todos os produtos — saldo GET e vendas observadas**.
2. Marcar a autorização e clicar **Consultar todos os saldos da loja**.
3. O sistema confere a conta e a loja, consulta até 10 páginas do catálogo por GET, valida todos os produtos, e consulta a última observação armazenada para a mesma loja.
4. A tabela exibe nome, categoria, SKU interno, ID do produto, **saldo GET datado**, **unidades em vendas fechadas observadas no período**, e indicação de variações. Campos ausentes continuam como **Não informado** ou **Não observado no período** — jamais zero presumido.
5. Busca por nome, categoria, código e ID; filtro de produtos observados; CSV com rótulos sanitizados quando houver as permissões de exportação.
6. A consulta fica disponível **somente na sessão por até cinco minutos**, revalidando autorização, organização e loja. Se o usuário trocar de loja ou for revogado, o quadro anterior é descartado.

## Segurança e precisão

- Uma consulta por clique, **sem polling, POST, escrita no Supabase ou alteração de estoque/pedido/nota do GestãoClick**.
- A última observação de vendas ainda é parcial: o aplicativo **não calcula pedidos, não decreta cobertura, não subtrai vendas do saldo GET e não preenche demanda zero para produtos não observados**.
- A consulta completa do catálogo verifica paginação, contagem, IDs e acesso Master. Saldos podem mudar segundos após o GET; o rótulo usa o horário real da leitura.
- Produtos com variações são marcados para conferência da unidade. Observações de outras lojas são recusadas; vendas cujos IDs não estão no catálogo consultado ficam de fora e geram aviso.
- A tabela não apresenta clientes, CPFs, CNPJs, preços, pedidos ou chaves fiscais. Exportação protege fórmulas do Excel.
- A futura recomendação de quantidade em caixas depende da validação das linhas e atualizações de vendas, exclusões, devoluções, reservas, embalagens e prazo. Não foi habilitada automaticamente.
- Webhooks permanecem para o final e NF-e real permanece bloqueada até a homologação fiscal.

## Testes

Cobrir loja trocada, duplicidades, ausência de vendas, saldo zero, saldo desconhecido, variações, data de coleta, exportação e consentimento Master; verificar publicação pela ação de versão e validar a área autenticada com o operador. 
