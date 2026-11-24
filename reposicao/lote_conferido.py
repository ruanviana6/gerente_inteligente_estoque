"""Planejamento em lote com entradas conferidas; não consolida vendas parciais."""
from __future__ import annotations

import csv
import io
from datetime import date

from reposicao.cenario_manual_real import _inteiro_informado, _media_informada
from reposicao.planejamento import RegraProduto, simular_cenario
from reposicao.quadro_real import QuadroReposicaoReal

MAX_PRODUTOS_LOTE = 100


def calcular_lote_conferido(
    quadro: QuadroReposicaoReal, entradas: list[dict], *,
    referencia: date, prazo_dias: str, ciclo_dias: int, confirmado: bool,
) -> list[dict]:
    """Aplica o motor existente, apenas a produtos identificados da leitura atual.

    Cada linha exige saldo disponível em UN (sem deduzir reservas duas vezes),
    demanda média líquida verificada, embalagem e segurança. Não usa totais
    observados como demanda e não pressupõe recebimentos em trânsito.
    """
    if confirmado is not True:
        raise ValueError("Confirme as entradas operacionais antes de calcular.")
    if (not isinstance(quadro, QuadroReposicaoReal) or type(referencia) is not date
            or type(ciclo_dias) is not int or ciclo_dias not in (14, 21)
            or not isinstance(entradas, list) or not 1 <= len(entradas) <= MAX_PRODUTOS_LOTE):
        raise ValueError("Loja, período ou seleção do lote inválidos.")
    prazo = _inteiro_informado(prazo_dias, "prazo de entrega", maximo=365)
    catalogo = {linha["Produto (ID)"]: linha for linha in quadro.linhas}
    if len(catalogo) != len(quadro.linhas):
        raise ValueError("O catálogo contém produtos repetidos.")
    vistos = set()
    resultados = []
    for entrada in entradas:
        if not isinstance(entrada, dict):
            raise ValueError("Linha do lote inválida.")
        ident = entrada.get("Produto (ID)")
        if not isinstance(ident, str) or ident in vistos or ident not in catalogo:
            raise ValueError("Produto repetido ou fora do catálogo da loja.")
        vistos.add(ident)
        item = catalogo[ident]
        if (not item["Produto"] or item["Produto"] == "Não informado pelo ERP"
                or item["Variações"] != "Sem variações no retorno"):
            raise ValueError("Produto sem identificação ou com variações exige conferência individual.")
        try:
            saldo = _inteiro_informado(entrada.get("Disponível (UN)"), "saldo disponível em UN")
            media = _media_informada(entrada.get("Saída líquida (UN/dia)"))
            caixa = _inteiro_informado(entrada.get("UN/CX"), "unidades por caixa", minimo=1, maximo=100_000)
            seguranca = _inteiro_informado(entrada.get("Segurança (UN)"), "estoque de segurança")
            regra = RegraProduto(prazo_dias=prazo, seguranca=seguranca,
                                 unidades_por_caixa=caixa, cobertura_alvo_dias=30)
            resultado = simular_cenario(
                referencia=referencia, estoque=saldo, reservas=0,
                media_diaria=media, demanda_confiavel=True, regra=regra, ciclo_dias=ciclo_dias,
            )
        except ValueError as erro:
            raise ValueError(f"Produto {ident}: {erro}") from None
        resultados.append({
            "Loja (ID)": quadro.loja_id, "Produto (ID)": ident,
            "Produto": item["Produto"], "Disponível conferido (UN)": saldo,
            "Saída líquida conferida (UN/dia)": format(media, "f"),
            "UN/CX": caixa, "Segurança (UN)": seguranca,
            "Prazo (dias)": prazo, "Ciclo (dias)": ciclo_dias,
            "Necessidade (UN)": resultado.necessidade_liquida,
            "Sugestão (UN)": resultado.unidades, "Sugestão (CX)": resultado.caixas,
            "Chegada prevista": resultado.data_chegada.isoformat(),
            "Risco antes da chegada": "Sim" if resultado.risco_antes_chegada else "Não",
            "Justificativa": resultado.observacao,
        })
    return resultados


def exportar_lote_conferido(linhas: list[dict]) -> bytes:
    """Exportação para decisão humana; protege rótulos externos no Excel."""
    if not linhas:
        raise ValueError("Não há sugestões para exportar.")
    arquivo = io.StringIO(newline="")
    writer = csv.DictWriter(arquivo, fieldnames=list(linhas[0]), delimiter=";")
    writer.writeheader()
    for linha in linhas:
        writer.writerow({
            chave: "'" + valor if isinstance(valor, str) and valor.lstrip().startswith(("=", "+", "-", "@"))
            else valor for chave, valor in linha.items()
        })
    return arquivo.getvalue().encode("utf-8-sig")
