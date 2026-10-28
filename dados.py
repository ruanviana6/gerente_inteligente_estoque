"""Camada de entrada de dados. Nenhuma função aqui altera o GestãoClick.

Os CSVs devem representar um catálogo completo e um histórico de vendas por
produto. Uma nova importação substitui as vendas anteriores para não duplicar
transações. Relatórios parciais/incrementais precisam de adaptador específico.
"""
from __future__ import annotations

import csv
import re
from datetime import date, datetime
from io import BytesIO, StringIO
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import config

COLUNAS_PRODUTOS = ["codigo", "nome", "estoque", "fornecedor", "custo_unitario",
                    "prazo_entrega_dias", "estoque_seguranca", "pedidos_pendentes",
                    "reservas", "cobertura_alvo_dias"]
COLUNAS_VENDAS = ["data", "codigo", "quantidade"]
COLUNAS_SNAPSHOTS = ["data", "codigo", "estoque"]
MAX_CSV_BYTES = 10 * 1024 * 1024
MAX_CSV_LINHAS = 200_000
MAX_INTEIRO = 2_147_483_647


def hoje_local() -> pd.Timestamp:
    """Retorna a data civil de Fortaleza, independente do fuso do servidor."""
    return pd.Timestamp(datetime.now(ZoneInfo("America/Fortaleza")).date())


def _validar_colunas(df: pd.DataFrame) -> None:
    """Rejeita colunas repetidas antes que pandas escolha campos ambiguamente."""
    if df.columns.duplicated().any():
        raise ValueError("Colunas repetidas não são aceitas.")


def _numero(df: pd.DataFrame, coluna: str, inteiro: bool = False) -> pd.Series:
    """Exige números finitos e não negativos, sem booleanos ou estouro de inteiro.

    O limite explícito evita divergências entre pandas, SQLite e o futuro
    PostgreSQL. A mesma validação é repetida depois de agregar vendas diárias.
    """
    if df[coluna].map(lambda valor: isinstance(valor, (bool, np.bool_))).any():
        raise ValueError("Booleanos não são números válidos em " + coluna + ".")
    valores = pd.to_numeric(df[coluna], errors="coerce")
    if valores.isna().any() or not np.isfinite(valores.to_numpy(dtype=float)).all():
        raise ValueError("Há valores inválidos em " + coluna + ".")
    if (valores < 0).any() or (inteiro and (valores % 1 != 0).any()):
        raise ValueError("O campo " + coluna + " exige valores não negativos"
                         + (" e inteiros." if inteiro else "."))
    if inteiro and valores.gt(MAX_INTEIRO).any():
        raise ValueError("Valor acima do limite permitido em " + coluna + ".")
    return valores.astype("int64" if inteiro else float)


def _datas_civis(valores: pd.Series, contexto: str) -> pd.Series:
    """Lê datas ISO ou brasileiras sem inferir dia/mês nem descartar horários.

    Vendas agregadas e snapshots representam dias, não instantes. Datas com
    horário/fuso devem ser normalizadas explicitamente na origem autorizada.
    """
    resultado = []
    try:
        for valor in valores:
            if isinstance(valor, str):
                valor = valor.strip()
                if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", valor):
                    data = pd.Timestamp(datetime.strptime(valor, "%Y-%m-%d"))
                elif re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", valor):
                    data = pd.Timestamp(datetime.strptime(valor, "%d/%m/%Y"))
                else:
                    raise ValueError("Formato de data não reconhecido.")
            elif isinstance(valor, (date, datetime, pd.Timestamp, np.datetime64)):
                data = pd.Timestamp(valor)
            else:
                raise ValueError("Data vazia ou de tipo inválido.")
            if pd.isna(data) or data.tzinfo is not None or data != data.normalize():
                raise ValueError("Informe uma data civil sem horário ou fuso.")
            resultado.append(data)
        datas = pd.Series(resultado, index=valores.index, dtype="datetime64[ns]")
    except (ValueError, TypeError, OverflowError) as erro:
        raise ValueError("Datas de " + contexto + " inválidas; use AAAA-MM-DD ou DD/MM/AAAA.") from erro
    if datas.gt(hoje_local()).any():
        raise ValueError("Datas futuras não são aceitas em " + contexto + ".")
    return datas


def validar_produtos(bruto: pd.DataFrame) -> pd.DataFrame:
    """Valida catálogo completo e preenche parâmetros operacionais opcionais.

    Uma linha representa um SKU único. Custo ausente vira NaN (desconhecido),
    nunca zero: isso evita exibir valores monetários inventados.
    """
    df = bruto.copy()
    _validar_colunas(df)
    faltantes = {"codigo", "nome", "estoque"} - set(df.columns)
    if faltantes:
        raise ValueError("Produtos sem colunas: " + ", ".join(sorted(faltantes)))
    if df.empty:
        raise ValueError("A planilha de produtos não pode estar vazia.")
    for campo in ("codigo", "nome"):
        df[campo] = df[campo].astype("string").str.strip()
        if df[campo].isna().any() or df[campo].eq("").any():
            raise ValueError("Produto com " + campo + " vazio.")
        if df[campo].str.contains("\x00", regex=False).any():
            raise ValueError("Produto com caractere nulo em " + campo + ".")
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
            ausente = df[campo].isna() | df[campo].astype(str).str.strip().eq("")
            df[campo] = df[campo].where(~ausente, padrao)
    df["fornecedor"] = df["fornecedor"].astype("string").str.strip()
    if df["fornecedor"].str.contains("\x00", regex=False).any():
        raise ValueError("Fornecedor com caractere nulo.")
    for campo in ("estoque", "prazo_entrega_dias", "estoque_seguranca",
                  "pedidos_pendentes", "reservas", "cobertura_alvo_dias"):
        df[campo] = _numero(df, campo, inteiro=True)
    if df["cobertura_alvo_dias"].eq(0).any():
        raise ValueError("Cobertura-alvo precisa ser maior que zero.")
    if "custo_unitario" not in df:
        df["custo_unitario"] = float("nan")
    else:
        preenchido = df["custo_unitario"].notna() & df["custo_unitario"].astype(str).str.strip().ne("")
        custo = pd.Series(float("nan"), index=df.index, dtype=float)
        custo.loc[preenchido] = _numero(df.loc[preenchido], "custo_unitario")
        df["custo_unitario"] = custo
    return df[COLUNAS_PRODUTOS].reset_index(drop=True)


def validar_vendas(bruto: pd.DataFrame, produtos: pd.DataFrame) -> pd.DataFrame:
    """Converte e agrega vendas por SKU/dia, rejeitando estouros e datas futuras.

    Quantidades negativas (por exemplo devoluções) exigem tratamento explícito
    na origem. Linhas zero são aceitas, mas não provam que houve uma venda.
    """
    df = bruto.copy()
    _validar_colunas(df)
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
    if not set(df["codigo"]).issubset(set(produtos["codigo"])):
        # Não ecoar conteúdo não confiável em mensagens exibidas na interface.
        raise ValueError("Há vendas de SKUs ausentes no catálogo.")
    df["data"] = _datas_civis(df["data"], "vendas")
    df["quantidade"] = _numero(df, "quantidade", inteiro=True)
    # Somar inteiros Python evita wraparound do int64 antes da validação final.
    agregado = df.assign(quantidade=df["quantidade"].astype(object)).groupby(
        ["data", "codigo"], as_index=False,
    )["quantidade"].sum()
    agregado["quantidade"] = _numero(agregado, "quantidade", inteiro=True)
    return agregado.sort_values(["data", "codigo"]).reset_index(drop=True)


def validar_snapshots(bruto: pd.DataFrame, produtos: pd.DataFrame) -> pd.DataFrame:
    """Valida fotos únicas por dia/SKU, sem inventar disponibilidade passada."""
    _validar_colunas(bruto)
    if bruto.empty:
        return pd.DataFrame({
            "data": pd.Series(dtype="datetime64[ns]"),
            "codigo": pd.Series(dtype="str"),
            "estoque": pd.Series(dtype="int64"),
        })
    df = bruto.copy()
    if set(COLUNAS_SNAPSHOTS) - set(df.columns):
        raise ValueError("Snapshot precisa de data, codigo, estoque.")
    df["codigo"] = df["codigo"].astype("string").str.strip()
    if (df["codigo"].isna().any() or df["codigo"].eq("").any()
            or not set(df["codigo"]).issubset(set(produtos["codigo"]))):
        raise ValueError("Snapshot com SKU desconhecido ou vazio.")
    df["data"] = _datas_civis(df["data"], "snapshots")
    df["estoque"] = _numero(df, "estoque", inteiro=True)
    if df.duplicated(["data", "codigo"]).any():
        raise ValueError("Snapshots repetidos para SKU e dia.")
    return df[COLUNAS_SNAPSHOTS].reset_index(drop=True)


def _ler_csv(arquivo: bytes | BytesIO, separador: str, decimal: str) -> pd.DataFrame:
    """Lê UTF-8 com limites e cabeçalho inequívoco, preservando SKUs textuais."""
    if isinstance(arquivo, bytes):
        conteudo = arquivo
    elif isinstance(arquivo, BytesIO):
        # Inspecionar o tamanho antes de copiar evita duplicar um arquivo excessivo.
        if arquivo.getbuffer().nbytes > MAX_CSV_BYTES:
            raise ValueError("CSV excede o limite de 10 MiB.")
        conteudo = arquivo.getvalue()
    else:
        raise ValueError("O CSV precisa ser enviado como bytes ou BytesIO.")
    if len(conteudo) > MAX_CSV_BYTES:
        raise ValueError("CSV excede o limite de 10 MiB.")
    try:
        texto = conteudo.decode("utf-8-sig")
        if "\x00" in texto:
            raise ValueError("CSV contém caractere nulo.")
        leitor = csv.reader(StringIO(texto, newline=""), delimiter=separador, strict=True)
        cabecalho = next(leitor, None)
        if not cabecalho or any(not campo.strip() for campo in cabecalho):
            raise ValueError("CSV sem cabeçalho válido.")
        cabecalho = [campo.strip() for campo in cabecalho]
        if len(set(cabecalho)) != len(cabecalho):
            raise ValueError("CSV com colunas repetidas.")
        linhas = []
        for linha in leitor:
            if not linha:
                continue
            if len(linha) != len(cabecalho):
                raise ValueError("CSV com quantidade de campos diferente do cabeçalho.")
            if len(linhas) >= MAX_CSV_LINHAS:
                raise ValueError("CSV excede o limite de linhas.")
            linhas.append(linha)
        df = pd.DataFrame(linhas, columns=cabecalho)
        # Somente números são normalizados. Código '001' ou 'NA' não é convertido.
        numericas = set(COLUNAS_PRODUTOS) - {"codigo", "nome", "fornecedor"}
        for campo in numericas | {"quantidade"}:
            if campo in df and decimal == ",":
                df[campo] = df[campo].str.replace(",", ".", regex=False)
        return df
    except (UnicodeError, csv.Error) as erro:
        raise ValueError("Não foi possível ler um dos arquivos CSV.") from erro


def carregar_de_csv(
    arquivo_produtos: bytes | BytesIO, arquivo_vendas: bytes | BytesIO,
    separador: str = ";", decimal: str = ",",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Lê CSVs completos com limites, valida relações e agrega vendas por dia.

    Aceita UTF-8 com ou sem BOM. Não infere SKU numérico nem formatos de data;
    campos vazios opcionais recebem somente os padrões documentados.
    """
    if separador not in (";", ","):
        raise ValueError("Separador deve ser ponto e vírgula ou vírgula.")
    if decimal not in (",", "."):
        raise ValueError("Separador decimal inválido.")
    produtos = validar_produtos(_ler_csv(arquivo_produtos, separador, decimal))
    vendas = validar_vendas(_ler_csv(arquivo_vendas, separador, decimal), produtos)
    return produtos, vendas


def exportar_csv_seguro(tabela: pd.DataFrame) -> bytes:
    """Neutraliza fórmulas em textos exportados, preservando valores numéricos.

    Planilhas podem executar células iniciadas por =, +, - ou @. O apóstrofo
    força texto, inclusive quando o prefixo perigoso vem depois de espaços.
    """
    def neutralizar(valor: object) -> object:
        """Protege apenas texto com prefixo interpretável como fórmula."""
        if isinstance(valor, str) and valor.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + valor
        return valor

    segura = tabela.map(neutralizar)
    segura.columns = [neutralizar(str(coluna)) for coluna in segura.columns]
    return segura.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


def gerar_dados_exemplo(
    hoje: object | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Gera cenário REPRODUTÍVEL, integralmente fictício, para avaliação do MVP.

    Simula tendências, fornecedores, itens parados, excesso e rupturas
    documentadas nos últimos três dias de um SKU. Nenhuma integração externa.
    """
    atual = pd.Timestamp(hoje if hoje is not None else hoje_local()).normalize()
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
                                  "estoque": 0 if ruptura else max(1, estoque)})
            if d < parada or ruptura:
                continue
            taxa = media * (fator if d < 30 else 1)
            quantidade = int(rng.poisson(taxa))
            if quantidade:
                registros.append({"data": data, "codigo": c, "quantidade": quantidade})
    vendas = validar_vendas(pd.DataFrame(registros), produtos)
    return produtos, vendas, validar_snapshots(pd.DataFrame(snapshots), produtos)

