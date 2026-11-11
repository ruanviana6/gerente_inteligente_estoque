"""Planejamento determinístico por ciclo, sem alterar estoques ou enviar pedidos."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from numbers import Integral
from typing import Iterable

LIMITE_UNIDADES = 2_147_483_647


def _inteiro(valor: object, nome: str, *, minimo: int = 0, maximo: int = LIMITE_UNIDADES) -> int:
    """Recusa valores fracionários, booleanos e quantidades fora do limite suportado."""
    if isinstance(valor, bool) or not isinstance(valor, Integral) or not minimo <= valor <= maximo:
        raise ValueError(f"{nome} deve ser inteiro entre {minimo} e {maximo}.")
    return int(valor)


def _demanda(valor: object) -> Decimal:
    """Converte a média já estimada pelo motor sem aceitar valores ausentes ou infinitos."""
    if isinstance(valor, bool):
        raise ValueError("A demanda diária precisa ser finita e não negativa.")
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("A demanda diária precisa ser finita e não negativa.") from None
    if not numero.is_finite() or numero < 0 or numero > LIMITE_UNIDADES:
        raise ValueError("A demanda diária precisa ser finita e não negativa.")
    return numero


@dataclass(frozen=True)
class Recebimento:
    """Quantidade e data comprovadas; pendências sem confirmação não entram no saldo."""

    data: date
    unidades: int

    def __post_init__(self) -> None:
        """Valida a informação antes de incluí-la na projeção."""
        if type(self.data) is not date:
            raise ValueError("A data do recebimento confirmado é inválida.")
        _inteiro(self.unidades, "Recebimento", minimo=1)


@dataclass(frozen=True)
class RegraProduto:
    """Condições internas da indústria e embalagem; ausência nunca inventa conversão."""

    prazo_dias: int
    seguranca: int
    unidades_por_caixa: int | None = None
    lote_minimo: int = 0
    multiplo: int = 1
    maximo: int | None = None
    cobertura_alvo_dias: int | None = None

    def __post_init__(self) -> None:
        """Verifica prazos e restrições sem supor capacidade física do caminhão."""
        _inteiro(self.prazo_dias, "Prazo", maximo=365)
        _inteiro(self.seguranca, "Estoque de segurança")
        if self.unidades_por_caixa is not None:
            _inteiro(self.unidades_por_caixa, "Unidades por caixa", minimo=1, maximo=100_000)
        _inteiro(self.lote_minimo, "Lote mínimo")
        _inteiro(self.multiplo, "Múltiplo", minimo=1)
        if self.cobertura_alvo_dias is not None:
            _inteiro(self.cobertura_alvo_dias, "Cobertura alvo", minimo=1, maximo=365)
        if self.maximo is not None:
            _inteiro(self.maximo, "Limite máximo", minimo=1)
            if self.maximo < self.lote_minimo:
                raise ValueError("O limite máximo não pode ser menor que o lote mínimo.")


@dataclass(frozen=True)
class Cenario:
    """Resultado de uma simulação com datas, limites e justificativa explícitos."""

    ciclo_dias: int
    data_solicitacao: date
    data_chegada: date
    data_limite: date | None
    primeira_ruptura: date | None
    risco_antes_chegada: bool
    demanda_30_dias: Decimal | None
    necessidade_liquida: int | None
    unidades: int | None
    caixas: int | None
    observacao: str


def _projetar(estoque: Decimal, demanda: Decimal, inicio: date, fim: date,
              recebimentos: dict[date, int], seguranca: int) -> tuple[Decimal, date | None, date | None]:
    """Percorre o calendário sem criar vendas perdidas após o saldo chegar a zero."""
    saldo = max(Decimal(0), estoque)
    ruptura = inicio if saldo <= 0 and demanda > 0 else None
    minimo = inicio if saldo <= seguranca and demanda > 0 else None
    dia = inicio
    while dia < fim:
        dia += timedelta(days=1)
        saldo += recebimentos.get(dia, 0)
        saldo = max(Decimal(0), saldo - demanda)
        if demanda > 0 and saldo <= 0 and ruptura is None:
            ruptura = dia
        if demanda > 0 and saldo <= seguranca and minimo is None:
            minimo = dia
    return saldo, ruptura, minimo


def simular_cenario(*, referencia: date, estoque: int, reservas: int, media_diaria: object,
                    demanda_confiavel: bool, regra: RegraProduto, ciclo_dias: int,
                    esperar_dias: int = 0, recebimentos: Iterable[Recebimento] = ()) -> Cenario:
    """Projeta chegada e ciclo usando demanda elegível e somente recebimentos confirmados.

    A sugestão arredonda para caixas e múltiplos por SKU. Sem histórico confiável,
    embalagem ou parâmetros coerentes, retorna explicação em vez de inventar valor.
    """
    if type(referencia) is not date or type(demanda_confiavel) is not bool:
        raise ValueError("Data de referência ou qualidade da demanda inválida.")
    estoque = _inteiro(estoque, "Estoque")
    reservas = _inteiro(reservas, "Reservas")
    ciclo = _inteiro(ciclo_dias, "Ciclo", minimo=1, maximo=365)
    espera = _inteiro(esperar_dias, "Espera", maximo=365)
    if not isinstance(regra, RegraProduto):
        raise ValueError("As regras do produto são inválidas.")
    pedido = referencia + timedelta(days=espera)
    chegada = pedido + timedelta(days=regra.prazo_dias)
    if not demanda_confiavel:
        return Cenario(ciclo, pedido, chegada, None, None, False, None, None, None, None,
                       "Histórico insuficiente ou disponibilidade incerta; revise antes de decidir.")
    demanda = _demanda(media_diaria)
    entradas: dict[date, int] = {}
    for entrada in recebimentos:
        if not isinstance(entrada, Recebimento) or entrada.data < referencia:
            raise ValueError("Recebimento confirmado inválido ou anterior à referência.")
        entradas[entrada.data] = entradas.get(entrada.data, 0) + entrada.unidades
        if entradas[entrada.data] > LIMITE_UNIDADES:
            raise ValueError("Recebimentos excedem o limite suportado.")
    inicial = Decimal(estoque - reservas + entradas.get(referencia, 0))
    saldo_chegada, ruptura, _ = _projetar(inicial, demanda, referencia, chegada, entradas, regra.seguranca)
    _, _, minimo = _projetar(inicial, demanda, referencia, referencia + timedelta(days=365), entradas, regra.seguranca)
    data_limite = minimo - timedelta(days=regra.prazo_dias) if minimo else None
    cobertura = max(ciclo, regra.cobertura_alvo_dias or ciclo)
    # Entradas confirmadas durante a cobertura também reduzem a necessidade,
    # mas uma entrada tardia não pode ocultar um déficit anterior a ela.
    acumulado, maior_deficit = Decimal(0), Decimal(0)
    for passo in range(1, cobertura + 1):
        acumulado += entradas.get(chegada + timedelta(days=passo), 0)
        maior_deficit = max(maior_deficit, demanda * passo - saldo_chegada - acumulado)
    falta_final = demanda * cobertura + regra.seguranca - saldo_chegada - acumulado
    liquida = max(0, int(max(maior_deficit, falta_final).to_integral_value(rounding=ROUND_CEILING)))
    if liquida > LIMITE_UNIDADES:
        raise ValueError("A necessidade calculada excede o limite suportado.")
    previsao = demanda * 30
    risco = ruptura is not None and ruptura <= chegada
    if regra.unidades_por_caixa is None:
        return Cenario(ciclo, pedido, chegada, data_limite, ruptura, risco, previsao, liquida, 0 if liquida == 0 else None, None,
                       "Cadastro de embalagem necessário para calcular caixas.")
    if liquida == 0:
        return Cenario(ciclo, pedido, chegada, data_limite, ruptura, risco, previsao, 0, 0, 0,
                       "A posição projetada cobre o ciclo e a segurança informada.")
    passo = math.lcm(regra.unidades_por_caixa, regra.multiplo)
    necessidade = max(liquida, regra.lote_minimo)
    unidades = ((necessidade + passo - 1) // passo) * passo
    if unidades > LIMITE_UNIDADES or (regra.maximo is not None and unidades > regra.maximo):
        return Cenario(ciclo, pedido, chegada, data_limite, ruptura, risco, previsao, liquida, None, None,
                       "As regras de lote, caixa e máximo exigem revisão; nenhuma quantidade foi cortada.")
    aviso = "Ruptura projetada antes ou na chegada; antecipar a análise." if risco else "Revise a sugestão antes de solicitar à indústria."
    aviso += f" Cobertura usada: {cobertura} dias (maior entre ciclo e alvo do produto)."
    return Cenario(ciclo, pedido, chegada, data_limite, ruptura, risco, previsao, liquida,
                   unidades, unidades // regra.unidades_por_caixa, aviso)
