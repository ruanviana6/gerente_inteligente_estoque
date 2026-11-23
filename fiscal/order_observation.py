"""Observação pontual de venda de atacado, sem cadastro fiscal ou dados pessoais.

Esta etapa NÃO confirma a loja efetiva do token, o destino CE, a duplicidade
de notas ou a segurança de cadastrar uma NF-e. Nunca libera emissão.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from integracoes.gestaoclick import ContratoInvalido
from integracoes.sales_audit import AuditoriaVendas


@dataclass(frozen=True, repr=False)
class ObservacaoPedidoFiscal:
    numero_solicitado: str
    loja_solicitada: str
    inicio: date
    fim: date
    documentos_consultados: int
    encontrado_na_janela: bool
    situacao: str | None
    situacao_id: str | None
    data_venda: date | None
    quantidade_linhas: int
    loja_efetiva_comprovada: bool = False
    destino_ce_comprovado: bool = False
    nf_preexistente_verificada: bool = False
    autorizacao_cadastro: bool = False


def observar_pedido_fiscal(
    auditoria: AuditoriaVendas,
    numero: str,
    situacoes: dict[str, str],
) -> ObservacaoPedidoFiscal:
    """Filtra por igualdade exata, nunca por parte do número ou por ID interno."""
    if (not isinstance(auditoria, AuditoriaVendas) or
            not isinstance(numero, str) or not re.fullmatch(r"[0-9]{1,20}", numero) or
            not isinstance(situacoes, dict) or
            any(not isinstance(k, str) or not isinstance(v, str) for k, v in situacoes.items())):
        raise ContratoInvalido("Confira o número de venda e as situações autorizadas.")
    encontrados = [doc for doc in auditoria.documentos if doc.codigo == numero]
    if len(encontrados) > 1:
        raise ContratoInvalido(
            "Mais de uma venda usa este número no período. "
            "A correspondência não é única; confira no ERP antes de continuar."
        )
    doc = encontrados[0] if encontrados else None
    if doc is not None and doc.situacao_id not in situacoes:
        raise ContratoInvalido("A situação desta venda não pertence ao catálogo conferido.")
    return ObservacaoPedidoFiscal(
        numero_solicitado=numero,
        loja_solicitada=auditoria.loja_solicitada,
        inicio=auditoria.inicio,
        fim=auditoria.fim,
        documentos_consultados=auditoria.total_informado,
        encontrado_na_janela=doc is not None,
        situacao=situacoes[doc.situacao_id] if doc else None,
        situacao_id=doc.situacao_id if doc else None,
        data_venda=doc.data if doc else None,
        quantidade_linhas=len(doc.itens) if doc else 0,
    )


def situacao_para_conferencia_fiscal(nome: str | None) -> tuple[str, str]:
    """Reconhece situação comercial, sem transformá-la em autorização de NF-e.

    A API de consulta não fornece aqui prova da UF, nota já existente,
    tributação ou efeitos do cadastro: qualquer situação permanece bloqueada
    para POST real até a homologação independente.
    """
    if nome is None:
        return "NAO_OBSERVADO", "Pedido não localizado nesta janela; não comprova ausência no ERP."
    if not isinstance(nome, str) or len(nome) > 200:
        raise ContratoInvalido("Situação comercial fora do contrato de conferência.")
    normal = unicodedata.normalize("NFKD", nome)
    chave = "".join(x for x in normal if not unicodedata.combining(x)).casefold().strip()
    if chave in {"em separacao", "concretizada"}:
        return "SITUACAO_COMPATIVEL", (
            "Situação comercial compatível com análise de cadastro, mas a criação "
            "real de NF-e continua bloqueada até confirmar UF CE, nota anterior, "
            "destinatário, impostos, loja do token e emissão manual."
        )
    if chave == "em andamento":
        return "APROVACAO_NECESSARIA", (
            "Em andamento exige autorização identificada e auditável antes "
            "da análise fiscal; a consulta não concede essa autorização."
        )
    return "SITUACAO_BLOQUEADA", (
        "Situação comercial não elegível para cadastro pela regra vigente. "
        "Confirme o status oficial no GestãoClick."
    )
