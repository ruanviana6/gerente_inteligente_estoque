"""Interfaces para o OpenAPI futuro; nenhuma rota ou operação ERP é presumida."""

from typing import Protocol


class IntegracaoRealBloqueada(RuntimeError):
    """Informa que a preparação não representa uma autorização fiscal produtiva."""


class LeitorAtacado(Protocol):
    """Contrato de leitura com credencial exclusiva do Atacado."""

    def consultar_pedido(self, identificador: str) -> dict:
        """Deverá resolver número/ID com contrato e conta oficialmente conferidos."""
        ...


class EmissorVarejo(Protocol):
    """Contrato restrito à NF-e direta, sem criação de pedido ou cadastros."""

    def consultar_existente(self, identidade: dict) -> dict:
        """Deverá reconciliar a identidade completa antes de qualquer criação."""
        ...

    def criar_nfe_direta(self, previa: dict) -> dict:
        """Depende da comprovação de NF-e sem pedido na conta Varejo."""
        ...

    def emitir_nfe(self, identificador: str) -> dict:
        """Deverá executar uma única tentativa autorizada, sem retry de POST."""
        ...

    def consultar_resultado(self, identificador: str) -> dict:
        """Deverá consultar a situação fiscal oficial, sem modificar a nota."""
        ...


def mapear_para_api_real(*_args, **_kwargs):
    """Bloqueia o mapeamento até receber OpenAPI, regras e autorização reais."""
    raise IntegracaoRealBloqueada('API fiscal real desativada. OpenAPI e homologação ainda não fornecidos.')
