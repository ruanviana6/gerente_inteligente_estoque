import sqlite3

import pandas as pd
import pytest

from dados import carregar_de_csv, gerar_dados_exemplo, validar_produtos
from storage import backup, carregar_base, registrar_decisao, salvar_importacao

def test_duplicidade():
    with pytest.raises(ValueError, match="duplicados"):
        validar_produtos(pd.DataFrame([
            {"codigo":"A","nome":"X","estoque":10},
            {"codigo":"A","nome":"Y","estoque":20}
        ]))

def test_csv():
    p,v = carregar_de_csv(
        b"codigo;nome;estoque\nP1;Produto;10\n",
        b"data;codigo;quantidade\n20/10/2026;P1;2\n"
    )
    assert p.iloc[0]["estoque"] == 10
    assert v.iloc[0]["quantidade"] == 2

def test_importacao_idempotente_e_backup(tmp_path):
    p,v,_ = gerar_dados_exemplo("2026-10-20")
    caminho = tmp_path / "gerente.db"
    salvar_importacao(p,v,caminho,"2026-10-20")
    salvar_importacao(p,v,caminho,"2026-10-20")
    catalogo,vendas,fotos,atualizado = carregar_base(caminho)
    assert len(catalogo) == len(p)
    assert vendas["quantidade"].sum() == v["quantidade"].sum()
    assert len(fotos) == len(p)
    registrar_decisao("P001",20,"Avaliar compra",caminho=caminho)
    copia = backup(caminho,tmp_path/"copia.db")
    with sqlite3.connect(copia) as db:
        assert db.execute("SELECT count(*) FROM decisoes").fetchone()[0] == 1

