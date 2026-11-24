"""Revalida duas leituras GET da mesma janela de vendas, sem criar livro fiscal.

As identidades documentais são resumidas com sal aleatório efêmero na sessão
do Master. Ausência na segunda leitura NÃO prova cancelamento ou exclusão.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from integracoes.gestaoclick import ContratoInvalido
from integracoes.native_preview import DocumentoPreviaVenda, ItemPreviaVenda
from integracoes.sales_audit import AuditoriaVendas


@dataclass(frozen=True, repr=False)
class AssinaturaJanela:
    loja_id: str
    inicio: date
    fim: date
    documentos: int
    assinaturas: tuple[tuple[str, str, str, str], ...] = field(repr=False)
    sal: str = field(repr=False)
    abrangencia_comprovada: bool = field(default=False, init=False)
    apta_reposicao: bool = field(default=False, init=False)


@dataclass(frozen=True)
class ComparacaoJanela:
    loja_id: str
    inicio: date
    fim: date
    documentos_antes: int
    documentos_depois: int
    novos_na_leitura: int
    nao_localizados_na_leitura: int
    situacoes_diferentes: int
    itens_diferentes: int
    datas_diferentes: int
    abrangencia_comprovada: bool = field(default=False, init=False)
    apta_reposicao: bool = field(default=False, init=False)


def _assinatura_itens(doc: DocumentoPreviaVenda) -> str:
    if not isinstance(doc.itens, tuple):
        raise ContratoInvalido("Os itens não podem ser revalidados.")
    linhas = []
    for item in doc.itens:
        if (not isinstance(item, ItemPreviaVenda)
                or not isinstance(item.produto_id, str)
                or not isinstance(item.quantidade, Decimal)
                or not item.quantidade.is_finite()
                or item.quantidade < 0):
            raise ContratoInvalido("Itens de venda inválidos na revalidação.")
        linhas.append((
            item.produto_id, item.variacao_id or "",
            format(item.quantidade, "f"), item.sigla_unidade or "",
        ))
    texto = json.dumps(sorted(linhas), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(texto.encode("ascii")).hexdigest()


def assinar_janela(auditoria: AuditoriaVendas, *, sal: str | None = None) -> AssinaturaJanela:
    """Conserva somente hashes de IDs/itens e situação; não armazena clientes."""
    if (not isinstance(auditoria, AuditoriaVendas)
            or not isinstance(auditoria.loja_solicitada, str)
            or not isinstance(auditoria.inicio, date)
            or not isinstance(auditoria.fim, date)
            or type(auditoria.total_informado) is not int
            or auditoria.total_informado != len(auditoria.documentos)
            or auditoria.total_informado > 1000
            or auditoria.inicio > auditoria.fim):
        raise ContratoInvalido("A leitura integral não possui a contagem e a janela esperadas.")
    chave = secrets.token_hex(24) if sal is None else sal
    if (not isinstance(chave, str) or len(chave) != 48
            or any(c not in "0123456789abcdef" for c in chave)):
        raise ContratoInvalido("A identidade protegida da sessão é inválida.")
    registros = []
    ids = set()
    for doc in auditoria.documentos:
        if (not isinstance(doc, DocumentoPreviaVenda)
                or not isinstance(doc.id, str) or not doc.id
                or not isinstance(doc.situacao_id, str)
                or type(doc.data) is not date):
            raise ContratoInvalido("Documento inconsistente na leitura integral.")
        identidade = hashlib.sha256((chave + ":" + doc.id).encode("utf-8")).hexdigest()
        if identidade in ids:
            raise ContratoInvalido("Documento duplicado na leitura integral.")
        ids.add(identidade)
        registros.append((
            identidade, doc.situacao_id, doc.data.isoformat(), _assinatura_itens(doc),
        ))
    return AssinaturaJanela(
        loja_id=auditoria.loja_solicitada, inicio=auditoria.inicio, fim=auditoria.fim,
        documentos=auditoria.total_informado,
        assinaturas=tuple(sorted(registros)), sal=chave,
    )


def comparar_janelas(antes: AssinaturaJanela, depois: AssinaturaJanela) -> ComparacaoJanela:
    """Divergência observada não equivale a evento fiscal ou atualização confirmada."""
    if (not isinstance(antes, AssinaturaJanela)
            or not isinstance(depois, AssinaturaJanela)
            or (antes.loja_id, antes.inicio, antes.fim, antes.sal)
            != (depois.loja_id, depois.inicio, depois.fim, depois.sal)):
        raise ContratoInvalido("Releitura exige a mesma loja, período e identidade da sessão.")
    x = {row[0]: row[1:] for row in antes.assinaturas}
    y = {row[0]: row[1:] for row in depois.assinaturas}
    if (len(x) != antes.documentos or len(y) != depois.documentos):
        raise ContratoInvalido("Assinaturas duplicadas ou contagens incompatíveis.")
    comuns = x.keys() & y.keys()
    return ComparacaoJanela(
        loja_id=antes.loja_id, inicio=antes.inicio, fim=antes.fim,
        documentos_antes=antes.documentos, documentos_depois=depois.documentos,
        novos_na_leitura=len(y.keys() - x.keys()),
        nao_localizados_na_leitura=len(x.keys() - y.keys()),
        situacoes_diferentes=sum(x[k][0] != y[k][0] for k in comuns),
        datas_diferentes=sum(x[k][1] != y[k][1] for k in comuns),
        itens_diferentes=sum(x[k][2] != y[k][2] for k in comuns),
    )
