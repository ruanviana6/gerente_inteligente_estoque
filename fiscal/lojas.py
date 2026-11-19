"""Confere os papéis das duas lojas fiscais a partir do catálogo autorizado.

Sem credenciais, rede, persistência nem operações fiscais. A correspondência
do nome apenas orienta o operador; não comprova a loja efetiva do token.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence

from integracoes.gestaoclick import ContratoInvalido


def _nome_normalizado(nome: str) -> str:
    if not isinstance(nome, str) or not nome.strip():
        return ""
    texto = unicodedata.normalize("NFKD", nome)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"\\s*[-–—‐‑]\\s*", " - ", texto)
    return " ".join(texto.casefold().split())


def validar_lojas_fiscais(
    lojas: Sequence[Mapping[str, str]],
    origem_id: str | None,
    destino_id: str | None,
) -> tuple[str, str]:
    """Valida IDs distintos e os nomes FLT Comércio → Flagship - Flatter.

    Nenhum ID é fixado no código ou armazenado. O catálogo já deve ter passado
    pela validação de assinatura e autorização do Master na página chamadora.
    """
    if (not isinstance(lojas, list) or
            not isinstance(origem_id, str) or not origem_id or
            not isinstance(destino_id, str) or not destino_id):
        raise ContratoInvalido("Selecione as lojas de origem e destino do cadastro autorizado.")
    if origem_id == destino_id:
        raise ContratoInvalido("Origem e destino fiscal precisam ser lojas distintas.")
    por_id: dict[str, str] = {}
    for row in lojas:
        if (not isinstance(row, dict) or
                not isinstance(row.get("id"), str) or not row["id"] or
                not isinstance(row.get("nome"), str) or
                row["id"] in por_id):
            raise ContratoInvalido("O catálogo de lojas precisa ser consultado novamente.")
        por_id[row["id"]] = row["nome"]
    if origem_id not in por_id or destino_id not in por_id:
        raise ContratoInvalido("Uma loja selecionada não pertence ao catálogo autorizado.")
    if (_nome_normalizado(por_id[origem_id]) != "flt comercio" or
            _nome_normalizado(por_id[destino_id]) != "flagship - flatter"):
        raise ContratoInvalido(
            "O nome da origem ou do destino não corresponde ao fluxo FLT Comércio "
            "→ Flagship - Flatter. Confira a loja no GestãoClick."
        )
    return origem_id, destino_id
