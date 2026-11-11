"""Leitura limitada de embalagens para prévia e confirmação administrativa."""

import csv
import io
from datetime import date

from reposicao.planejamento import RegraProduto

LIMITE_ARQUIVO = 256 * 1024
LIMITE_LINHAS = 500
COLUNAS = {"sku", "unidades_por_caixa", "vigencia", "lote_minimo", "multiplo", "maximo", "observacao"}


def _inteiro(texto: str, campo: str, padrao: int | None = None) -> int | None:
    """Aceita apenas dígitos decimais, com limites de banco e sem notação ambígua."""
    valor = texto.strip()
    if not valor and padrao is not None:
        return padrao
    if not valor and campo == "máximo":
        return None
    if not valor.isascii() or not valor.isdigit() or len(valor) > 10:
        raise ValueError(f"{campo.capitalize()} deve ser um inteiro válido.")
    return int(valor)


def ler_embalagens(arquivo: bytes, produtos: set[str], revisoes: dict[str, str]) -> list[dict]:
    """Valida CSV completo antes de permitir qualquer gravação transacional."""
    if not isinstance(arquivo, bytes) or not 0 < len(arquivo) <= LIMITE_ARQUIVO:
        raise ValueError("O CSV precisa ter até 256 KiB e conter linhas de embalagem.")
    try:
        texto = arquivo.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("Use um CSV codificado em UTF-8.") from None
    cabecalho = texto.splitlines()[0] if texto.splitlines() else ""
    leitor = csv.DictReader(io.StringIO(texto, newline=""), delimiter=";" if ";" in cabecalho else ",")
    campos = leitor.fieldnames or []
    if len(campos) != len(set(campos)) or not {"sku", "unidades_por_caixa", "vigencia"}.issubset(campos) or set(campos) - COLUNAS:
        raise ValueError("Cabeçalhos inválidos. Use sku, unidades_por_caixa e vigencia.")
    linhas, vistos = [], set()
    for linha in leitor:
        if len(linhas) >= LIMITE_LINHAS or None in linha or any(valor is None for valor in linha.values()):
            raise ValueError("CSV excede 500 linhas ou contém uma linha incompleta.")
        sku = linha["sku"].strip()
        if not sku or sku in vistos or sku not in produtos:
            raise ValueError("Há SKU vazio, duplicado ou ausente do catálogo atual.")
        vistos.add(sku)
        try:
            vigencia = date.fromisoformat(linha["vigencia"].strip())
        except ValueError:
            raise ValueError("A vigência deve usar o formato AAAA-MM-DD.") from None
        unidades = _inteiro(linha["unidades_por_caixa"], "unidades por caixa")
        minimo = _inteiro(linha.get("lote_minimo", ""), "lote mínimo", 0)
        multiplo = _inteiro(linha.get("multiplo", ""), "múltiplo", 1)
        maximo = _inteiro(linha.get("maximo", ""), "máximo")
        observacao = linha.get("observacao", "").strip()
        if len(observacao) > 500:
            raise ValueError("Observação deve ter até 500 caracteres.")
        RegraProduto(0, 0, unidades_por_caixa=unidades, lote_minimo=minimo, multiplo=multiplo, maximo=maximo)
        linhas.append({"sku": sku, "effective_on": vigencia.isoformat(), "units_per_box": unidades,
                       "minimum_lot": minimo, "production_multiple": multiplo, "maximum_units": maximo,
                       "note": observacao, "expected": revisoes.get(sku)})
    if not linhas:
        raise ValueError("Inclua ao menos um produto no arquivo.")
    return linhas
