"""Camada de entrada de dados. Nenhuma função aqui altera o GestãoClick.

Os CSVs devem representar um catálogo completo e um histórico de vendas por
produto. Uma nova importação substitui as vendas anteriores para não duplicar
transações. Relatórios parciais/incrementais precisam de adaptador específico.
"""
from __future__ import annotations

from io import BytesIO
import math

import numpy as np
import pandas as pd

import config

COLUNAS_PRODUTOS = ["codigo", "nome", "estoque", "fornecedor", "custo_unitario",
                    "prazo_entrega_dias", "estoque_seguranca", "pedidos_pendentes",
                    "reservas", "cobertura_alvo_dias"]
COLUNAS_VENDAS = ["data", "codigo", "quantidade"]
COLUNAS_SNAPSHOTS = ["data", "codigo", "estoque"]


def _numero(df: pd.DataFrame, coluna: str, inteiro: bool = False) -> pd.Series:
    """Converte campos numéricos sem aceitar valores inválidos ou negativos."""
    valores = pd.to_numeric(df[coluna], errors="coerce")
    if valores.isna().any() or not np.isfinite(valores.to_numpy(dtype=float)).all():
        raise ValueError("Há valores inválidos em " + coluna + ".")
    if (valores < 0).any() or (inteiro and (valores % 1 != 0).any()):
        raise ValueError("O campo " + coluna + " exige valores não negativos"
                         + (" e inteiros." if inteiro else "."))
    return valores.astype(int if inteiro else float)


def validar_produtos(bruto: pd.DataFrame) -> pd.DataFrame:
    """Valida catálogo e preenche parâmetros operacionais opcionais.

    Uma linha representa um SKU único. Custo ausente vira NaN (desconhecido),
    nunca zero: isso evita exibir valores monetários inventados.
    """
    df = bruto.copy()
    faltantes = {"codigo", "nome", "estoque"} - set(df.columns)
    if faltantes:
        raise ValueError("Produtos sem colunas: " + ", ".join(sorted(faltantes)))
    if df.empty:
        raise ValueError("A planilha de produtos não pode estar vazia.")
    for campo in ("codigo", "nome"):
        df[campo] = df[campo].astype("string").str.strip()
        if df[campo].isna().any() or df[campo].eq("").any():
            raise ValueError("Produto com " + campo + " vazio.")
    if df["codigo"].duplicated().any():
        raise ValueError("Códigos de produto duplicados no catálogo.")
    defaults = {
        "fornecedor": "Não informado",
        "prazo_entrega_dias": config.PRAZO_ENTREGA_PADRAO_DIAS,
        "estoque_seguranca": config.ESTOQUE_SEGURANCA_PADRAO,
        "pedidos_pendentes": 0, "reservas": 0,
        "cobertura_alvo_dias": config.COBERTURA_ALVO_DIAS,
    }
    for campo, padrao in defaults.items():
        if campo not in df:
            df[campo] = padrao
        else:
            df[campo] = df[campo].fillna(padrao)
    df["fornecedor"] = df["fornecedor"].astype("string").str.strip().replace("", "Não informado")
    for campo in ("estoque", "prazo_entrega_dias", "estoque_seguranca",
                  "pedidos_pendentes", "reservas", "cobertura_alvo_dias"):
        df[campo] = _numero(df, campo, inteiro=True)
    if df["cobertura_alvo_dias"].eq(0).any():
        raise ValueError("Cobertura-alvo precisa ser maior que zero.")
    if "custo_unitario" not in df:
        df["custo_unitario"] = float("nan")
    else:
        custo = pd.to_numeric(df["custo_unitario"], errors="coerce")
        # Valores não preenchidos são válidos; valores digitados incorretamente não.
        preenchido = df["custo_unitario"].notna() & df["custo_unitario"].astype(str).str.strip().ne("")
        if (preenchido & custo.isna()).any() or (custo.dropna() < 0).any():
            raise ValueError("Custo unitário deve ser numérico e não negativo.")
        df["custo_unitario"] = custo.astype(float)
    return df[COLUNAS_PRODUTOS].reset_index(drop=True)


def validar_vendas(bruto: pd.DataFrame, produtos: pd.DataFrame) -> pd.DataFrame:
    """Converte e agrega vendas por produto/dia sem multiplicar linhas.

    Entrada: data, codigo, quantidade. Quantidades não negativas; devoluções
    devem ser tratadas previamente na origem, não misturadas como 'venda'.
    """
    df = bruto.copy()
    faltantes = set(COLUNAS_VENDAS) - set(df.columns)
    if faltantes:
        raise ValueError("Vendas sem colunas: " + ", ".join(sorted(faltantes)))
    if df.empty:
        return pd.DataFrame({
            "data": pd.Series(dtype="datetime64[ns]"),
            "codigo": pd.Series(dtype="str"),
            "quantidade": pd.Series(dtype="int64"),
        })
    df["codigo"] = df["codigo"].astype("string").str.strip()
    if df["codigo"].isna().any() or df["codigo"].eq("").any():
        raise ValueError("Vendas com código de produto vazio.")
    desconhecidos = set(df["codigo"]) - set(produtos["codigo"])
    if desconhecidos:
        raise ValueError("Há vendas de SKUs ausentes no catálogo: "
                         + ", ".join(sorted(desconhecidos)[:5]))
    try:
        df["data"] = pd.to_datetime(df["data"], errors="raise", dayfirst=True).dt.normalize()
    except (TypeError, ValueError) as erro:
        raise ValueError("Datas de vendas inválidas.") from erro
    if df["data"].isna().any() or df["data"].gt(pd.Timestamp.today().normalize()).any():
        raise ValueError("Datas vazias ou futuras não são aceitas.")
    df["quantidade"] = _numero(df, "quantidade", inteiro=True)
    return (df.groupby(["data", "codigo"], as_index=False)["quantidade"]
              .sum().sort_values(["data", "codigo"]).reset_index(drop=True))


def validar_snapshots(bruto: pd.DataFrame, produtos: pd.DataFrame) -> pd.DataFrame:
    """Valida fotografias diárias sem inferir saldos de datas não observadas."""
    if bruto.empty:
        return pd.DataFrame(columns=COLUNAS_SNAPSHOTS)
    df = bruto.copy()
    if set(COLUNAS_SNAPSHOTS) - set(df.columns):
        raise ValueError("Snapshot precisa de data, codigo, estoque.")
    df["codigo"] = df["codigo"].astype("string").str.strip()
    if df["codigo"].isna().any() or not set(df["codigo"]).issubset(set(produtos["codigo"])):
        raise ValueError("Snapshot com SKU desconhecido.")
    df["data"] = pd.to_datetime(df["data"], errors="raise").dt.normalize()
    if df["data"].isna().any():
        raise ValueError("Snapshot com data vazia.")
    df["estoque"] = _numero(df, "estoque", inteiro=True)
    if df.duplicated(["data", "codigo"]).any():
        raise ValueError("Snapshots repetidos para SKU e dia.")
    return df[COLUNAS_SNAPSHOTS].reset_index(drop=True)


def carregar_de_csv(
    arquivo_produtos: bytes | BytesIO, arquivo_vendas: bytes | BytesIO,
    separador: str = ";", decimal: str = ",",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Lê exportações UTF-8/UTF-8 BOM e aplica os contratos de dados.

    O arquivo de vendas pode conter várias linhas por produto/dia; serão
    agregadas. Nunca execute macros ou código presente em arquivos externos.
    """
    if separador not in (";", ","):
        raise ValueError("Separador deve ser ponto e vírgula ou vírgula.")
    if decimal not in (",", "."):
        raise ValueError("Separador decimal inválido.")
    try:
        p = pd.read_csv(BytesIO(arquivo_produtos) if isinstance(arquivo_produtos, bytes) else arquivo_produtos, sep=separador, decimal=decimal, encoding="utf-8-sig")
        v = pd.read_csv(BytesIO(arquivo_vendas) if isinstance(arquivo_vendas, bytes) else arquivo_vendas, sep=separador, decimal=decimal, encoding="utf-8-sig")
    except (OSError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as erro:
        raise ValueError("Não foi possível ler um dos arquivos CSV.") from erro
    produtos = validar_produtos(p)
    return produtos, validar_vendas(v, produtos)


def gerar_dados_exemplo(
    hoje: object | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Gera cenário REPRODUTÍVEL, integralmente fictício, para avaliação do MVP.

    Simula tendências, fornecedores, itens parados, excesso e rupturas
    documentadas nos últimos três dias de um SKU. Nenhuma integração externa.
    """
    atual = pd.Timestamp(hoje if hoje is not None else pd.Timestamp.today()).normalize()
    rng = np.random.default_rng(42)
    catalogo = [
        ("P001", "Parafuso 8 mm (cx)", 20, 4.0, 0, 1.0, "Fixadores Ceará", 12, 18, 24.50),
        ("P002", "Porca M8 (cx)", 70, 4.0, 0, 1.0, "Fixadores Ceará", 8, 12, 16.90),
        ("P003", "Arruela lisa (cx)", 55, 4.0, 0, 1.0, "Fixadores Ceará", 10, 15, 9.90),
        ("P004", "Broca 6 mm", 300, 3.0, 0, 1.0, "Ferramentas Norte", 5, 10, 12.00),
        ("P005", "Fita veda rosca", 120, 3.0, 0, 1.0, "Hidráulica Local", 15, 20, 3.10),
        ("P006", "Luva de raspa", 80, 2.0, 70, 1.0, "Proteção Nordeste", 12, 10, 19.90),
        ("P007", "Disco de corte", 200, 2.5, 0, 2.0, "Ferramentas Norte", 7, 18, 7.20),
        ("P008", "Trena 5 m", 20, 2.0, 0, .3, "Ferramentas Norte", 9, 8, 22.90),
        ("P009", "Cadeado 40 mm", 0, 2.0, 0, 1.0, "Ferragens Brasil", 10, 8, 32.00),
        ("P010", "Produto novo", 40, 0.0, 120, 1.0, "Novos Produtos", 7, 5, float("nan")),
    ]
    produtos = validar_produtos(pd.DataFrame([
        {"codigo": c, "nome": n, "estoque": e, "fornecedor": f,
         "prazo_entrega_dias": prazo, "estoque_seguranca": segur,
         "custo_unitario": custo, "cobertura_alvo_dias": 30}
        for c, n, e, media, parada, fator, f, prazo, segur, custo in catalogo
    ]))
    registros = []
    snapshots = []
    for c, nome, estoque, media, parada, fator, fornecedor, prazo, segur, custo in catalogo:
        for d in range(120):
            data = atual - pd.Timedelta(days=d)
            # Exemplo de ruptura comprovada: 3 dias sem disponibilidade, sem vendas.
            ruptura = c == "P009" and d < 3
            if d < 14:
                snapshots.append({"codigo": c, "data": data,
                                  "estoque": 0 if ruptura else estoque})
            if d < parada or ruptura:
                continue
            taxa = media * (fator if d < 30 else 1)
            quantidade = int(rng.poisson(taxa))
            if quantidade:
                registros.append({"data": data, "codigo": c, "quantidade": quantidade})
    vendas = validar_vendas(pd.DataFrame(registros), produtos)
    return produtos, vendas, validar_snapshots(pd.DataFrame(snapshots), produtos)
