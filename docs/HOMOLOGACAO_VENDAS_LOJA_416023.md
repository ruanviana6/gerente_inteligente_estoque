# Homologação da auditoria real de vendas — loja 416023

> Documento operacional. **Não é comprovante de homologação executada**. Não registrar credenciais, clientes, dados pessoais, valores comerciais ou respostas brutas da API no GitHub.

## Estado verificado no código

- PR #45: auditoria integral de vendas e conciliação com fotografia do catálogo.
- PR #47: `Em separação` e `Concretizada` contam na **demanda comercial fechada observada**, não em baixa física do estoque.
- Loja: `416023`; situações previamente conferidas: `6548068` (Em separação), `6548070` (Concretizada).
- O módulo de leitura **não importa vendas** nem atualiza `inventory_current`, `sales_lines_current` ou a Reposição Inteligente.
- Amostra de catálogo anteriormente informada: 245 produtos e saldo agregado 61.916. **Essas contagens não substituem conferência atual no ERP.**

## Operação autorizada pelo Master

1. Abrir a aplicação publicada no perfil Master. Acessar **Administração → Conferir lojas e situações da conta**; verificar que a loja é `416023`.
2. Confirmar no GestãoClick que os nomes exibidos correspondem aos IDs `6548068` e `6548070`; não presumir os mesmos IDs para outra loja.
3. Abrir **Auditar todas as vendas do período e conciliar produtos**. Usar janela de **até sete dias**, preferencialmente uma janela recente conferível no ERP, sem misturar lojas.
4. Marcar a autorização específica da consulta (somente GET), então executar **Conferir vendas e conciliar com catálogo**.
5. Conferir o período e as contagens de páginas, documentos e itens; verificar o total no ERP para o **mesmo filtro e intervalo**, registrando apenas agregados não sensíveis.
6. Conferir o número de documentos **Em separação**, **Concretizada** e os não classificados. O mesmo pedido que mudou de situação não pode ser contado duas vezes.
7. Examinar IDs de produtos sem correspondência e unidades não suportadas. Não converter `CX`, `KG`, `KIT` ou unidades em branco em `UN` sem fator aprovado.
8. Conferir uma pequena amostra de produtos conciliados entre vendas e fotografia do catálogo da **mesma loja**, incluindo código, identidade e unidade. Não subtrair venda do saldo exibido pelo ERP.
9. Se aparecer erro de limite, paginação, escopo ou autorização, interromper. Não aumentar permissões, não contornar o bloqueio e não tratar resultado parcial como completo.
10. Encerrar anotando o resultado abaixo **sem publicar dados reais identificáveis**. A consulta por si só não libera integração automática.

## Registro de evidência, sem dados sensíveis

| Campo | Resultado |
| --- | --- |
| Data/hora local da conferência | 18/11/2026, ~08:03–08:04 (capturas fornecidas pelo Master) |
| Perfil Master autorizado | Capturas mostram consentimento marcado; verificação independente do papel ainda pendente |
| Loja confirmada 416023 | 416023 exibida pelo aplicativo; correspondência operacional no ERP ainda deve ser conferida |
| Período efetivamente consultado (até sete dias) | 12/11/2026 a 18/11/2026 (sete dias inclusivos) |
| Páginas / documentos / itens (somente totais) | 2 / 169 / 1.759 |
| Documentos Em separação / Concretizada | 89 / 66 — total fechado 155 |
| Outras situações não classificadas | 14 |
| Produtos vistos / conciliados | 123 / 123 (por ID, dentro da fotografia de 245 produtos) |
| Quantidade UN observada | 3.740 (demanda observada, não saldo nem previsão) |
| Total no ERP para filtros equivalentes | **Pendente:** não houve comparação de totais diretamente no ERP |
| Produtos sem correspondência (quantidade) | Nenhum identificado na síntese (123 de 123 conciliados); demais validações ainda pendentes |
| Itens com unidades ausentes / não suportadas (quantidades) | Pendente: não aparecem nas capturas |
| Divergência observada e causa, sem PII | Pendente de comparação direta no ERP |
| Resultado | **Consulta real observada com sucesso; homologação operacional e fiscal ainda pendentes** |

## Bloqueios antes da sincronização real

1. **Identidade estável:** provar em detalhes/listagens se cada item de venda expõe um ID imutável entre reconsultas, alterações de quantidade e transições de situação. Se não existir, desenhar contrato alternativo auditável de snapshots; nunca usar posição na lista como ID.
2. **Atualização:** comprovar `source_updated_at` oficial ou versão equivalente, ou protocolar snapshots completos versionados com tratamento de concorrência e de janelas anteriores.
3. **Exclusões:** clientes podem apagar pedidos em vez de marcá-los como cancelados. A ausência em consulta posterior não prova exclusão; requer origem auditável, tombstone ou evento explícito. Não remover histórico por ausência.
4. **Devoluções:** validar fonte e granularidade de devolução antes de calcular quantidade líquida. Não presumir devolução zero.
5. **Cobertura:** demonstrar escopo completo por loja, tipo de venda, situações, paginação e permissões da chave. HTTP 200 não equivale a cobertura completa.
6. **Unidades:** registrar fatores por produto/embalagem sob aprovação do Master. A soma em UN só inclui itens explicitamente em UN ou convertidos com regra comprovada.
7. **Idempotência:** ensaiar reprocessamento, mudança Em separação → Concretizada, atualização de item, remoção confirmada, timeout pós-commit e concorrência, sem duplicar demanda.
8. **Segurança:** manter GestãoClick analítico exclusivamente GET; preservar RLS, autenticação Master, segredos, histórico, limites Free e a separação do módulo fiscal.

## Decisão de liberação

**Estado atual: BLOQUEADO PARA SINCRONIZAÇÃO OPERACIONAL.** A consulta observada foi bem-sucedida, mas não valida cobertura nem eventos de alteração/exclusão. Só considerar carregamento de vendas operacionais e atualização automática da Reposição Inteligente depois de:
- auditoria autenticada e conciliação com a fonte oficial documentadas;
- todos os bloqueios acima resolvidos e testados;
- testes de Python 3.11/3.12, SQL/RLS, autenticação, interface e regressão aprovados;
- aprovação explícita do Master para ativar o fluxo real, sem ativação indireta por merge.

Referências: `docs/AUDITORIA_VENDAS_GESTAOCLICK.md`, issues #33 e #38 e PRs #45 e #47.
