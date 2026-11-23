"""Simulação operacional manual com produto real e valores conferidos no ERP.

Nenhum número comercial é estimado a partir de fotografias históricas: estoque,
demanda e embalagem precisam ser fornecidos e confirmados pelo operador.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from reposicao.planejamento import LIMITE_UNIDADES, Cenario, RegraProduto, simular_cenario


@dataclass(frozen=True)
class PreviaReposicaoReal:
    """Mantém a origem da identificação e os valores conferidos da simulação."""

    loja_id: str
    produto_id: str
    produto_nome: str
    categoria: str
    fotografia_em: datetime
    conferencia_em: date
    estoque_conferido: int
    reservas_conferidas: int
    media_diaria_conferida: Decimal
    unidades_por_caixa_conferidas: int
    prazo_dias_conferido: int
    seguranca_conferida: int
    ciclo_dias: int
    resultado: Cenario


def _inteiro_informado(valor: object, nome: str, *, minimo: int = 0,
                       maximo: int = LIMITE_UNIDADES) -> int:
    """Rejeita campo vazio, sinais, frações e limites inviáveis."""
    if not isinstance(valor, str) or not re.fullmatch(r"[0-9]{1,10}", valor.strip()):
        raise ValueError(f"Informe {nome} como número inteiro verificado.")
    numero = int(valor.strip())
    if not minimo <= numero <= maximo:
        raise ValueError(f"{nome.capitalize()} fora do limite permitido.")
    return numero


def _media_informada(valor: object) -> Decimal:
    """Aceita UN/dia em formato simples, sem inferir vendas ou zeros ausentes."""
    if not isinstance(valor, str) or not re.fullmatch(r"[0-9]{1,9}(?:[.,][0-9]{1,4})?", valor.strip()):
        raise ValueError("Informe a média diária em UN, com até quatro casas decimais.")
    numero = Decimal(valor.strip().replace(",", "."))
    if numero > LIMITE_UNIDADES:
        raise ValueError("Média diária fora do limite permitido.")
    return numero


def preparar_reposicao_real(
    *, fotografia: dict, produto_id: str, estoque: str, reservas: str,
    media_diaria: str, unidades_por_caixa: str, prazo_dias: str, seguranca: str,
    ciclo_dias: int, referencia: date, conferencia_explicita: bool,
) -> PreviaReposicaoReal:
    """Aplica motor existente ao único produto da loja selecionada.

    Esta prévia é um cenário dependente das entradas humanas verificadas; não
    é uma venda validada nem atualiza estoque oficial ou gera pedido ao ERP.
    """
    if conferencia_explicita is not True:
        raise ValueError("Confirme que conferiu os números no GestãoClick antes de simular.")
    if (not isinstance(fotografia, dict) or not isinstance(fotografia.get("rows"), list)
            or not isinstance(fotografia.get("store_id"), str)
            or not isinstance(fotografia.get("created_at"), datetime)
            or fotografia["created_at"].utcoffset() is None
            or not isinstance(produto_id, str) or not produto_id.isascii()
            or not produto_id.isdigit() or type(referencia) is not date
            or type(ciclo_dias) is not int or ciclo_dias not in (14, 21)):
        raise ValueError("Loja, data ou produto da fotografia inválidos.")
    encontrados = [r for r in fotografia["rows"] if isinstance(r, dict) and r.get("Produto (ID)") == produto_id]
    if len(encontrados) != 1:
        raise ValueError("Produto não pertence de modo único à fotografia da loja.")
    item = encontrados[0]
    quantidade = _inteiro_informado(estoque, "o saldo atual do ERP")
    reservadas = _inteiro_informado(reservas, "as reservas conferidas")
    diaria = _media_informada(media_diaria)
    caixa = _inteiro_informado(unidades_por_caixa, "as unidades por caixa", minimo=1, maximo=100_000)
    prazo = _inteiro_informado(prazo_dias, "o prazo total de chegada", maximo=365)
    seguranca_un = _inteiro_informado(seguranca, "o estoque de segurança")
    regra = RegraProduto(prazo_dias=prazo, seguranca=seguranca_un,
                         unidades_por_caixa=caixa, cobertura_alvo_dias=30)
    cenario = simular_cenario(
        referencia=referencia, estoque=quantidade, reservas=reservadas,
        media_diaria=diaria, demanda_confiavel=True, regra=regra,
        ciclo_dias=ciclo_dias,
    )
    return PreviaReposicaoReal(
        loja_id=fotografia["store_id"], produto_id=produto_id,
        produto_nome=item["Produto"], categoria=item["Categoria"],
        fotografia_em=fotografia["created_at"], conferencia_em=referencia,
        estoque_conferido=quantidade, reservas_conferidas=reservadas,
        media_diaria_conferida=diaria, unidades_por_caixa_conferidas=caixa,
        prazo_dias_conferido=prazo, seguranca_conferida=seguranca_un,
        ciclo_dias=ciclo_dias, resultado=cenario,
    )
