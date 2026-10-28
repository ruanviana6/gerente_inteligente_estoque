"""Persistência SQLite local: o ERP nunca é escrito por esta camada.

Uma importação substitui atomicamente o catálogo/vendas já importados, mas
mantém snapshots históricos de SKUs existentes. O arquivo .db deve ficar fora
do Git, com acesso restrito ao usuário autorizado.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from numbers import Integral
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from dados import (
    COLUNAS_PRODUTOS,
    COLUNAS_SNAPSHOTS,
    COLUNAS_VENDAS,
    validar_produtos,
    validar_snapshots,
    validar_vendas,
)

CAMINHO_PADRAO = Path("data/gerente.db")


@contextmanager
def conexao(caminho: Path = CAMINHO_PADRAO):
    """Abre conexão com integridade referencial, commit/rollback e fechamento."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with con:
            yield con
    finally:
        con.close()


def inicializar(caminho: Path = CAMINHO_PADRAO) -> None:
    """Cria tabelas caso não existam; nunca apaga registros existentes."""
    with conexao(caminho) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS produtos (
            codigo TEXT PRIMARY KEY,
            nome TEXT NOT NULL,
            estoque INTEGER NOT NULL CHECK(estoque >= 0),
            fornecedor TEXT NOT NULL,
            custo_unitario REAL,
            prazo_entrega_dias INTEGER NOT NULL,
            estoque_seguranca INTEGER NOT NULL,
            pedidos_pendentes INTEGER NOT NULL,
            reservas INTEGER NOT NULL,
            cobertura_alvo_dias INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vendas_diarias (
            data TEXT NOT NULL,
            codigo TEXT NOT NULL REFERENCES produtos(codigo) ON DELETE CASCADE,
            quantidade INTEGER NOT NULL CHECK(quantidade >= 0),
            PRIMARY KEY(data, codigo)
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            data TEXT NOT NULL,
            codigo TEXT NOT NULL REFERENCES produtos(codigo) ON DELETE CASCADE,
            estoque INTEGER NOT NULL CHECK(estoque >= 0),
            PRIMARY KEY(data, codigo)
        );
        CREATE TABLE IF NOT EXISTS decisoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_hora TEXT NOT NULL,
            codigo TEXT NOT NULL REFERENCES produtos(codigo) ON DELETE CASCADE,
            quantidade_sugerida INTEGER NOT NULL,
            acao TEXT NOT NULL CHECK(acao IN ('Avaliar compra','Ignorar')),
            observacao TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS metadados (
            chave TEXT PRIMARY KEY,
            valor TEXT NOT NULL
        );
        """)


def salvar_importacao(
    produtos: pd.DataFrame, vendas: pd.DataFrame,
    caminho: Path = CAMINHO_PADRAO, data_snapshot: object | None = None,
) -> None:
    """Aplica importação completa e snapshot atual numa única transação.

    O replace de vendas é idempotente: importar os mesmos CSVs de novo não
    duplica quantidades. Exclui SKUs ausentes do novo catálogo explicitamente;
    o usuário deve importar o catálogo inteiro, nunca só uma página.
    """
    # A camada de persistência também valida: scripts não precisam passar pela UI.
    produtos = validar_produtos(produtos)
    vendas = validar_vendas(vendas, produtos)
    hoje = data_snapshot if data_snapshot is not None else datetime.now(ZoneInfo("America/Fortaleza")).date()
    fotos = validar_snapshots(produtos[["codigo", "estoque"]].assign(data=hoje), produtos)
    if not vendas.empty and vendas["data"].max() > fotos["data"].iloc[0]:
        raise ValueError("O snapshot não pode ser anterior às vendas importadas.")
    inicializar(caminho)
    with conexao(caminho) as db:
        # A tabela temporária evita o limite de parâmetros do SQLite em catálogos grandes.
        db.execute("CREATE TEMP TABLE catalogo_importado (codigo TEXT PRIMARY KEY)")
        db.executemany("INSERT INTO catalogo_importado VALUES (?)", [(c,) for c in produtos["codigo"]])
        db.execute("DELETE FROM produtos WHERE codigo NOT IN (SELECT codigo FROM catalogo_importado)")
        for p in produtos.itertuples(index=False):
            valores = [None if pd.isna(getattr(p, c)) else getattr(p, c)
                       for c in COLUNAS_PRODUTOS]
            db.execute("""
                INSERT INTO produtos (
                  codigo,nome,estoque,fornecedor,custo_unitario,prazo_entrega_dias,
                  estoque_seguranca,pedidos_pendentes,reservas,cobertura_alvo_dias
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(codigo) DO UPDATE SET
                  nome=excluded.nome, estoque=excluded.estoque,
                  fornecedor=excluded.fornecedor, custo_unitario=excluded.custo_unitario,
                  prazo_entrega_dias=excluded.prazo_entrega_dias,
                  estoque_seguranca=excluded.estoque_seguranca,
                  pedidos_pendentes=excluded.pedidos_pendentes,
                  reservas=excluded.reservas,
                  cobertura_alvo_dias=excluded.cobertura_alvo_dias
            """, valores)
        db.execute("DELETE FROM vendas_diarias")
        db.executemany(
            "INSERT INTO vendas_diarias(data,codigo,quantidade) VALUES (?,?,?)",
            [(str(v.data.date()), v.codigo, int(v.quantidade))
             for v in vendas.itertuples(index=False)],
        )
        db.executemany(
            """INSERT INTO snapshots(data,codigo,estoque) VALUES (?,?,?)
               ON CONFLICT(data,codigo) DO UPDATE SET estoque=excluded.estoque""",
            [(str(f.data.date()), f.codigo, int(f.estoque))
             for f in fotos.itertuples(index=False)],
        )
        db.execute(
            "INSERT OR REPLACE INTO metadados(chave,valor) VALUES ('importado_em',?)",
            (datetime.now(ZoneInfo("America/Fortaleza")).isoformat(timespec="seconds"),),
        )


def carregar_base(
    caminho: Path = CAMINHO_PADRAO,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str | None]:
    """Carrega uma cópia independente dos dados para cálculos e interface."""
    inicializar(caminho)
    with conexao(caminho) as db:
        # Todas as leituras pertencem à mesma versão, mesmo durante outra importação.
        db.execute("BEGIN")
        p = pd.read_sql_query("SELECT * FROM produtos ORDER BY codigo", db)
        v = pd.read_sql_query("SELECT data,codigo,quantidade FROM vendas_diarias", db)
        s = pd.read_sql_query("SELECT data,codigo,estoque FROM snapshots", db)
        atualizado = db.execute(
            "SELECT valor FROM metadados WHERE chave='importado_em'"
        ).fetchone()
    v = v.reindex(columns=COLUNAS_VENDAS)
    s = s.reindex(columns=COLUNAS_SNAPSHOTS)
    v["data"] = pd.to_datetime(v["data"])
    s["data"] = pd.to_datetime(s["data"])
    return p.reindex(columns=COLUNAS_PRODUTOS), v, s, atualizado[0] if atualizado else None


def registrar_decisao(
    codigo: str, quantidade: int, acao: str, observacao: str = "",
    caminho: Path = CAMINHO_PADRAO,
) -> None:
    """Grava feedback humano; não efetua compra nem modifica o estoque."""
    if (
        acao not in ("Avaliar compra", "Ignorar")
        or isinstance(quantidade, bool)
        or not isinstance(quantidade, Integral)
        or not 0 <= quantidade <= 2_147_483_647
    ):
        raise ValueError("Decisão inválida.")
    if not isinstance(codigo, str) or not codigo.strip():
        raise ValueError("Informe um código de produto válido.")
    if not isinstance(observacao, str) or len(observacao) > 500:
        raise ValueError("A observação deve ser texto com até 500 caracteres.")
    codigo = codigo.strip()
    inicializar(caminho)
    with conexao(caminho) as db:
        if not db.execute("SELECT 1 FROM produtos WHERE codigo=?", (codigo,)).fetchone():
            raise ValueError("Produto não encontrado para registrar a decisão.")
        db.execute(
            "INSERT INTO decisoes(data_hora,codigo,quantidade_sugerida,acao,observacao) "
            "VALUES (?,?,?,?,?)",
            (datetime.now(ZoneInfo("America/Fortaleza")).isoformat(timespec="seconds"),
             codigo, int(quantidade), acao, observacao),
        )


def listar_decisoes(caminho: Path = CAMINHO_PADRAO) -> pd.DataFrame:
    """Retorna trilha de decisões sem inferir que uma compra foi realizada."""
    inicializar(caminho)
    with conexao(caminho) as db:
        return pd.read_sql_query(
            "SELECT data_hora,codigo,quantidade_sugerida,acao,observacao "
            "FROM decisoes ORDER BY id DESC LIMIT 200", db
        )


def backup(caminho: Path = CAMINHO_PADRAO, destino: Path | None = None) -> Path:
    """Cria cópia consistente, recusando sobrescrita e acesso amplo ao arquivo.

    A origem é aberta em modo somente leitura. A criação exclusiva do destino
    evita perder backups anteriores; em caso de falha, remove só a cópia nova.
    """
    caminho = Path(caminho)
    if not caminho.is_file():
        raise FileNotFoundError("Banco inexistente.")
    destino = Path(destino) if destino is not None else Path("backups") / (
        "estoque_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".db"
    )
    if caminho.resolve() == destino.resolve():
        raise ValueError("O backup precisa de um destino diferente da origem.")
    destino.parent.mkdir(parents=True, exist_ok=True)
    # Não use exists() antes de abrir: outra execução poderia criar o arquivo nesse intervalo.
    descritor = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descritor)
    try:
        with closing(sqlite3.connect(caminho.resolve().as_uri() + "?mode=ro", uri=True)) as origem:
            with closing(sqlite3.connect(destino)) as copia:
                origem.backup(copia)
    except Exception:
        destino.unlink(missing_ok=True)
        raise
    return destino

