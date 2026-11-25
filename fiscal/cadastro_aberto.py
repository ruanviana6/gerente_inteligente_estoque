"""Prepara o contrato de cadastro direto para homologação, sem realizar chamadas."""

import re
from dataclasses import dataclass

from fiscal.lojas import validar_lojas_fiscais
from fiscal.regras import EscolhasFiscais
from integracoes.gestaoclick import ContratoInvalido

_ID = re.compile(r"[1-9][0-9]{0,17}")
_NCM = re.compile(r"[0-9]{8}")


def _id_textual(valor: str) -> str:
    """Aceita somente IDs numéricos positivos para referência e reconciliação."""
    if type(valor) is not str or not _ID.fullmatch(valor):
        raise ContratoInvalido("Identificador fiscal inválido.")
    return valor


def _inteiro_positivo(valor: int) -> int:
    """Recusa conversões que poderiam trocar a identidade ou arredondar quantidade."""
    if type(valor) is not int or not 1 <= valor < 10**18:
        raise ContratoInvalido("Informe identificadores e quantidades inteiras positivas.")
    return valor


@dataclass(frozen=True)
class ItemFiscalDestino:
    """Identidade de produto e NCM já conferidos no cadastro da loja de destino."""

    produto_id: int
    quantidade: int
    ncm: str
    variacao_id: int | None = None

    def corpo(self) -> dict:
        """Projeta a linha apenas com campos documentados para a NF-e direta."""
        if type(self.ncm) is not str or not _NCM.fullmatch(self.ncm) or self.ncm == "00000000":
            raise ContratoInvalido("Informe o NCM conferido do item.")
        corpo = {"produto_id": _inteiro_positivo(self.produto_id),
                 "quantidade": _inteiro_positivo(self.quantidade), "NCM": self.ncm}
        if self.variacao_id is not None:
            corpo["variacao_id"] = _inteiro_positivo(self.variacao_id)
        return corpo


@dataclass(frozen=True, repr=False)
class PreparoCadastroAberto:
    """Separa o corpo da API das escolhas que ainda exigem conferência no ERP."""

    corpo: dict
    referencia_origem: str
    escolhas: EscolhasFiscais


def preparar_cadastro_aberto(
    *, lojas: list[dict[str, str]], origem_id: str, destino_id: str,
    venda_id: str, destinatario_destino_id: int, itens: tuple[ItemFiscalDestino, ...],
    escolhas: EscolhasFiscais,
) -> PreparoCadastroAberto:
    """Monta um candidato para homologação a partir de dados fiscais conferidos.

    A referência acompanha a nota para conferência. Ela só pode servir para
    reconciliação automática depois que a busca exata for validada na API.
    Frete e contribuinte permanecem separados do corpo porque não possuem
    campos de gravação documentados para esta operação.
    """
    origem, destino = validar_lojas_fiscais(lojas, origem_id, destino_id)
    referencia = f"Flatter origem {_id_textual(origem)} venda {_id_textual(venda_id)}"
    if type(itens) is not tuple or not 1 <= len(itens) <= 100 or any(
        type(item) is not ItemFiscalDestino for item in itens
    ):
        raise ContratoInvalido("Informe os itens fiscais conferidos na loja de destino.")
    if type(escolhas) is not EscolhasFiscais:
        raise ContratoInvalido("Confirme o frete e o tipo de contribuinte.")
    return PreparoCadastroAberto(
        corpo={
            "loja_id": _id_textual(destino),
            "tipo_nf": 1,
            "envio_automatico": 0,
            "id_destinatario": _inteiro_positivo(destinatario_destino_id),
            "informacoes_complementares": referencia,
            "produtos": [item.corpo() for item in itens],
        },
        referencia_origem=referencia,
        escolhas=escolhas,
    )


def identificar_cadastro(resposta: object) -> str:
    """Lê o ID documentado sem tratar resposta ambígua como permissão para repetir."""
    dados = resposta.get("data") if isinstance(resposta, dict) else None
    if (isinstance(resposta, dict) and type(resposta.get("code")) is int
            and resposta["code"] == 200 and resposta.get("status") == "success"
            and isinstance(dados, dict) and dados.get("ok") is True
            and type(dados.get("codigo")) is int and dados["codigo"] == 200
            and type(dados.get("dados")) is str and _ID.fullmatch(dados["dados"])):
        return dados["dados"]
    raise ContratoInvalido("Cadastro não confirmado. Reconcilie no GestãoClick antes de tentar novamente.")
