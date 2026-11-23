"""Conciliação conservadora entre documentos de venda e fotografia do catálogo.

A demanda é uma observação comercial, não uma baixa física de estoque.
Sem linha estável e instante de alteração, não cria versões canônicas nem
presume que documentos ausentes foram cancelados/excluídos.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from integracoes.gestaoclick import ContratoInvalido
from integracoes.sales_audit import AuditoriaVendas

MAX_CATALOGO = 1000


@dataclass(frozen=True, repr=False)
class ConciliacaoVendas:
    total_documentos: int
    documentos_fechados: int
    documentos_separacao: int
    documentos_concretizados: int
    documentos_nao_classificados: int
    produtos_vistos: int
    produtos_correspondentes: int
    produtos_sem_correspondencia: tuple[str, ...]
    linhas_unidade_nao_confirmada: int
    linhas_unidade_nao_suportada: int
    itens_fechados: int
    unidades_fechadas_un: Decimal
    agrupamento: tuple[tuple[str, Decimal], ...]
    source_coverage_verified: bool = field(default=False, init=False)
    imported: bool = field(default=False, init=False)
    ready_for_replenishment: bool = field(default=False, init=False)


def _catalogo(valores: dict, loja: str) -> set[str]:
    if (not isinstance(valores, dict) or valores.get("store_id") != loja or
            not isinstance(valores.get("products"), list)):
        raise ContratoInvalido("A fotografia devolveu uma estrutura inválida.")
    linhas = valores["products"]
    total = valores.get("product_count")
    if (type(total) is not int or total != len(linhas) or
            total < 1 or total > MAX_CATALOGO):
        raise ContratoInvalido("A fotografia não contém contagem confiável.")
    ids: set[str] = set()
    for linha in linhas:
        # Fotografias reais incluem identificação opcional; o contrato antigo
        # de dois campos também permanece legível para históricos anteriores.
        campos_permitidos = {"id", "estoque", "codigo_interno", "nome", "categoria"}
        if (not isinstance(linha, dict) or
                not {"id", "estoque"}.issubset(linha) or
                not set(linha).issubset(campos_permitidos)):
            raise ContratoInvalido("A fotografia contém produto inválido.")
        for campo, limite in (("codigo_interno", 100), ("nome", 200), ("categoria", 200)):
            valor = linha.get(campo)
            if valor is not None and (
                    not isinstance(valor, str) or
                    not 1 <= len(valor.strip()) <= limite or
                    any(ord(c) < 32 or ord(c) == 127 or c in "<>" for c in valor)):
                raise ContratoInvalido("Identificação de produto inválida na fotografia.")
        identidade = linha["id"]
        if not isinstance(identidade, str) or not identidade.isascii() or not identidade.isdecimal():
            raise ContratoInvalido("A fotografia contém ID inválido.")
        try:
            saldo = Decimal(str(linha["estoque"]))
        except (InvalidOperation, ValueError, TypeError):
            raise ContratoInvalido("A fotografia contém saldo inválido.") from None
        if (saldo.is_nan() or not saldo.is_finite() or saldo < 0
                or saldo > Decimal("1000000000") or identidade in ids):
            raise ContratoInvalido("A fotografia contém saldo ou ID incompatível.")
        ids.add(identidade)
    return ids


def conciliar_vendas_com_catalogo(
    auditoria: AuditoriaVendas,
    fotografia: dict,
    *,
    separacao_id: str,
    concretizada_id: str,
) -> ConciliacaoVendas:
    """Agrega cada documento no seu estado atual, sem dobrar a passagem de status.

    Só converte "UN" como unidade confirmada. CAIXA, KG e unidades omitidas
    ficam pendentes de fator ou cadastro; não há conversão implícita.
    """
    if (not isinstance(auditoria, AuditoriaVendas) or
            not isinstance(separacao_id, str) or not isinstance(concretizada_id, str) or
            not separacao_id or not concretizada_id or separacao_id == concretizada_id):
        raise ContratoInvalido("As situações comerciais não foram mapeadas com segurança.")
    catalogo = _catalogo(fotografia, auditoria.loja_solicitada)
    vistos: set[str] = set()
    sem_relacao: set[str] = set()
    agrupado: dict[str, Decimal] = {}
    separacao = concretizadas = nao_classificados = desconhecida = nao_suportada = itens_fechados = 0
    for doc in auditoria.documentos:
        atual = doc.situacao_id
        if atual == separacao_id:
            separacao += 1
        elif atual == concretizada_id:
            concretizadas += 1
        else:
            nao_classificados += 1
        for item in doc.itens:
            vistos.add(item.produto_id)
            if item.produto_id not in catalogo:
                sem_relacao.add(item.produto_id)
            if atual not in {separacao_id, concretizada_id}:
                continue
            itens_fechados += 1
            if item.sigla_unidade is None:
                desconhecida += 1
                continue
            if item.sigla_unidade.upper() != "UN":
                nao_suportada += 1
                continue
            if item.produto_id not in catalogo:
                continue
            agrupado[item.produto_id] = agrupado.get(item.produto_id, Decimal(0)) + item.quantidade
    linhas = tuple(sorted(agrupado.items(), key=lambda par: (-par[1], int(par[0]))))
    return ConciliacaoVendas(
        total_documentos=len(auditoria.documentos),
        documentos_fechados=separacao + concretizadas,
        documentos_separacao=separacao,
        documentos_concretizados=concretizadas,
        documentos_nao_classificados=nao_classificados,
        produtos_vistos=len(vistos),
        produtos_correspondentes=len(vistos - sem_relacao),
        produtos_sem_correspondencia=tuple(sorted(sem_relacao, key=int)),
        linhas_unidade_nao_confirmada=desconhecida,
        linhas_unidade_nao_suportada=nao_suportada,
        itens_fechados=itens_fechados,
        unidades_fechadas_un=sum(agrupado.values(), Decimal(0)),
        agrupamento=linhas,
    )


def rotular_agrupamento(
    resultado: ConciliacaoVendas, fotografia: dict, loja_esperada: str | None = None,
) -> tuple[dict, ...]:
    """Mostra nomes e grupos reais da mesma fotografia conciliada, sem deduzir dados.

    Nenhum rótulo é persistido com a observação e os saldos não são exportados.
    Produtos da venda ausentes na fotografia são bloqueados, não inventados.
    """
    if not isinstance(resultado, ConciliacaoVendas):
        raise TypeError("É necessária uma conciliação de vendas validada.")
    if not isinstance(fotografia, dict) or not isinstance(fotografia.get("store_id"), str):
        raise ContratoInvalido("Fotografia de catálogo inválida para identificar produtos.")
    if loja_esperada is not None and fotografia["store_id"] != loja_esperada:
        raise ContratoInvalido("A fotografia pertence a outra loja.")
    identificadores = _catalogo(fotografia, fotografia["store_id"])
    por_id = {linha["id"]: linha for linha in fotografia["products"]}
    linhas = []
    for produto_id, quantidade in resultado.agrupamento:
        if (not isinstance(produto_id, str) or produto_id not in identificadores or
                not isinstance(quantidade, Decimal) or not quantidade.is_finite() or
                quantidade < 0):
            raise ContratoInvalido("O produto observado não confere com a fotografia.")
        produto = por_id[produto_id]
        linhas.append({
            "Produto": produto.get("nome") or "Não informado",
            "Categoria": produto.get("categoria") or "Não informada",
            "Código interno": produto.get("codigo_interno") or "Não informado",
            "Produto (ID)": produto_id,
            "Quantidade observada (UN)": format(quantidade, "f"),
        })
    return tuple(linhas)


def _celula_csv(valor: str | None) -> str:
    """Escapa fórmulas em rótulos externos que serão abertos no Excel."""
    if valor is None:
        return ""
    return "'" + valor if valor.startswith(("=", "+", "-", "@")) else valor


def csv_demanda_observada(
    resultado: ConciliacaoVendas, fotografia: dict | None = None,
    loja_esperada: str | None = None,
) -> bytes:
    """Exporta somente totais UN por produto; nunca dados de clientes, saldo ou linha."""

    if not isinstance(resultado, ConciliacaoVendas):
        raise TypeError("É necessária uma conciliação de vendas validada.")
    arquivo = io.StringIO(newline="")
    tabela = csv.writer(arquivo, delimiter=";", lineterminator="\n")
    if fotografia is not None:
        tabela.writerow(("produto_id", "nome_produto", "categoria", "codigo_interno",
                         "quantidade_observada_un"))
        for linha in rotular_agrupamento(resultado, fotografia, loja_esperada):
            tabela.writerow((
                linha["Produto (ID)"],
                _celula_csv(linha["Produto"] if linha["Produto"] != "Não informado" else None),
                _celula_csv(linha["Categoria"] if linha["Categoria"] != "Não informada" else None),
                _celula_csv(linha["Código interno"] if linha["Código interno"] != "Não informado" else None),
                linha["Quantidade observada (UN)"].replace(".", ","),
            ))
        return arquivo.getvalue().encode("utf-8-sig")
    tabela.writerow(("produto_id", "quantidade_observada_un"))
    for produto_id, quantidade in resultado.agrupamento:
        # A origem já valida IDs do catálogo; conferimos novamente na fronteira de exportação.
        if (not isinstance(produto_id, str) or not produto_id.isascii() or not produto_id.isdecimal()
                or not isinstance(quantidade, Decimal) or not quantidade.is_finite() or quantidade < 0):
            raise ContratoInvalido("Dados de exportação incompatíveis.")
        tabela.writerow((produto_id, format(quantidade, "f").replace(".", ",")))
    return arquivo.getvalue().encode("utf-8-sig")
