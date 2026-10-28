"""Regras configuráveis do gerente: valores padrão, não dados do ERP.

Altere estas constantes para ajustar as políticas do protótipo. Os parâmetros
individuais de cada produto (prazo, segurança, reservas etc.) têm prioridade.
"""
from dataclasses import dataclass

JANELA_VENDA_DIAS = 30
JANELA_TENDENCIA_DIAS = 90
DIAS_SEM_VENDA_PARADO = 60
COBERTURA_EXCESSO_DIAS = 90
COBERTURA_ALVO_DIAS = 30
PRAZO_ENTREGA_PADRAO_DIAS = 10
ESTOQUE_SEGURANCA_PADRAO = 0
LIMITE_TENDENCIA_PCT = 20.0
# Nome, duração máxima de cobertura e ícone. A ordem define a prioridade.
NIVEIS_ALERTA = [
    ("Crítico", 7, "🔴"),
    ("Alto", 15, "🟠"),
    ("Atenção", 30, "🟡"),
]


@dataclass(frozen=True)
class Configuracao:
    """Agrupa parâmetros globais; facilita testes com políticas diferentes."""

    janela_venda: int = JANELA_VENDA_DIAS
    janela_tendencia: int = JANELA_TENDENCIA_DIAS
    dias_parado: int = DIAS_SEM_VENDA_PARADO
    excesso_dias: int = COBERTURA_EXCESSO_DIAS
    cobertura_alvo: int = COBERTURA_ALVO_DIAS
    limite_tendencia_pct: float = LIMITE_TENDENCIA_PCT

    def __post_init__(self) -> None:
        """Impede parâmetros sem sentido antes de processar indicadores."""
        if min(
            self.janela_venda, self.janela_tendencia, self.dias_parado,
            self.excesso_dias, self.cobertura_alvo,
        ) <= 0:
            raise ValueError("Janelas e coberturas devem ser positivas.")
        if self.janela_tendencia <= self.janela_venda:
            raise ValueError("A janela longa deve superar a curta.")
        if self.limite_tendencia_pct < 0:
            raise ValueError("O limite de tendência não pode ser negativo.")
