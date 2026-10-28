"""Regressões de integridade, isolamento transacional e backup do MVP local."""

import sqlite3

import pandas as pd
import pytest

from dados import validar_produtos, validar_vendas
from storage import backup, carregar_base, listar_decisoes, registrar_decisao, salvar_importacao


def base_ficticia():
    """Cria um catálogo pequeno com vendas anteriores ao snapshot de teste."""
    produtos = validar_produtos(pd.DataFrame([
        {"codigo": "A", "nome": "Produto fictício", "estoque": 10},
        {"codigo": "B", "nome": "Outro produto fictício", "estoque": 20},
    ]))
    vendas = validar_vendas(pd.DataFrame([
        {"data": "20/01/2020", "codigo": "A", "quantidade": 2},
        {"data": "20/01/2020", "codigo": "B", "quantidade": 4},
    ]), produtos)
    return produtos, vendas


def assinatura(caminho):
    """Lê o conteúdo persistido, inclusive metadados e decisões, para detectar perda."""
    with sqlite3.connect(caminho) as banco:
        return {
            tabela: banco.execute(f"SELECT * FROM {tabela}").fetchall()
            for tabela in ("produtos", "vendas_diarias", "snapshots", "decisoes", "metadados")
        }


def test_importacao_vazia_nao_apaga_catalogo(tmp_path):
    """Uma chamada direta ao armazenamento precisa validar antes de excluir dados."""
    produtos, vendas = base_ficticia()
    caminho = tmp_path / "base.db"
    salvar_importacao(produtos, vendas, caminho, "2020-01-20")
    antes = assinatura(caminho)
    with pytest.raises(ValueError, match="vazia"):
        salvar_importacao(produtos.iloc[:0], vendas.iloc[:0], caminho, "2020-01-20")
    assert assinatura(caminho) == antes


def test_dados_invalidos_nao_criam_banco(tmp_path):
    """A validação deve ocorrer antes de qualquer criação ou escrita de banco."""
    produtos, vendas = base_ficticia()
    caminho = tmp_path / "inexistente.db"
    produtos.loc[0, "estoque"] = -1
    with pytest.raises(ValueError):
        salvar_importacao(produtos, vendas, caminho, "2020-01-20")
    assert not caminho.exists()


def test_vendas_desconhecidas_nao_modificam_base(tmp_path):
    """Uma venda fora do catálogo é rejeitada sem perder a importação anterior."""
    produtos, vendas = base_ficticia()
    caminho = tmp_path / "base.db"
    salvar_importacao(produtos, vendas, caminho, "2020-01-20")
    antes = assinatura(caminho)
    vendas.loc[0, "codigo"] = "INEXISTENTE"
    with pytest.raises(ValueError):
        salvar_importacao(produtos, vendas, caminho, "2020-01-20")
    assert assinatura(caminho) == antes


@pytest.mark.parametrize("data", ["invalida", "2999-01-01", "2020-01-19"])
def test_snapshot_invalido_nao_modifica_base(tmp_path, data):
    """Datas futuras, inválidas ou anteriores às vendas não podem fabricar histórico."""
    produtos, vendas = base_ficticia()
    caminho = tmp_path / "base.db"
    salvar_importacao(produtos, vendas, caminho, "2020-01-20")
    antes = assinatura(caminho)
    with pytest.raises(ValueError):
        salvar_importacao(produtos, vendas, caminho, data)
    assert assinatura(caminho) == antes


def test_falha_no_fim_da_transacao_preserva_importacao_anterior(tmp_path):
    """Uma falha após atualizar catálogo e vendas deve reverter toda a importação."""
    produtos, vendas = base_ficticia()
    caminho = tmp_path / "base.db"
    salvar_importacao(produtos, vendas, caminho, "2020-01-20")
    registrar_decisao("A", 3, "Ignorar", caminho=caminho)
    antes = assinatura(caminho)
    with sqlite3.connect(caminho) as banco:
        banco.execute("""
            CREATE TRIGGER falha_planejada BEFORE INSERT ON metadados
            BEGIN SELECT RAISE(ABORT, 'falha fictícia para testar rollback'); END
        """)
    produtos.loc[0, "estoque"] = 99
    vendas.loc[0, "quantidade"] = 50
    with pytest.raises(sqlite3.IntegrityError, match="falha fictícia"):
        salvar_importacao(produtos, vendas, caminho, "2020-01-21")
    assert assinatura(caminho) == antes


def test_reimportacao_preserva_fotos_e_decisoes_apos_reabrir(tmp_path):
    """Conexões independentes leem o mesmo histórico sem duplicar vendas ou fotos."""
    produtos, vendas = base_ficticia()
    caminho = tmp_path / "base.db"
    salvar_importacao(produtos, vendas, caminho, "2020-01-20")
    registrar_decisao("A", 3, "Ignorar", caminho=caminho)
    produtos.loc[0, "estoque"] = 8
    salvar_importacao(produtos, vendas, caminho, "2020-01-21")
    salvar_importacao(produtos, vendas, caminho, "2020-01-21")
    catalogo, importadas, fotos, atualizado = carregar_base(caminho)
    assert catalogo.loc[catalogo["codigo"] == "A", "estoque"].item() == 8
    assert importadas["quantidade"].sum() == 6
    assert len(fotos) == 4
    assert len(listar_decisoes(caminho)) == 1
    assert atualizado.endswith("-03:00")
    foto_antiga = fotos.loc[(fotos["codigo"] == "A") & (fotos["data"] == pd.Timestamp("2020-01-20"))]
    assert foto_antiga["estoque"].item() == 10


def test_leitura_consistente_durante_importacao_concorrente(tmp_path, monkeypatch):
    """Catálogo, vendas e fotos precisam vir da mesma versão do banco."""
    produtos, vendas = base_ficticia()
    caminho = tmp_path / "base.db"
    salvar_importacao(produtos, vendas, caminho, "2020-01-20")
    with sqlite3.connect(caminho) as banco:
        banco.execute("PRAGMA journal_mode=WAL")
    consulta_original = pd.read_sql_query
    importacao_aplicada = False

    def consultar_e_importar(sql, banco, *args, **kwargs):
        """Insere uma nova versão entre duas leituras para reproduzir a condição de corrida."""
        nonlocal importacao_aplicada
        resultado = consulta_original(sql, banco, *args, **kwargs)
        if not importacao_aplicada:
            importacao_aplicada = True
            novos_produtos = produtos.copy()
            novas_vendas = vendas.copy()
            novos_produtos.loc[0, "estoque"] = 99
            novas_vendas.loc[0, "quantidade"] = 50
            salvar_importacao(novos_produtos, novas_vendas, caminho, "2020-01-21")
        return resultado

    monkeypatch.setattr(pd, "read_sql_query", consultar_e_importar)
    catalogo, importadas, fotos, _ = carregar_base(caminho)
    assert catalogo.loc[catalogo["codigo"] == "A", "estoque"].item() == 10
    assert importadas["quantidade"].sum() == 6
    assert len(fotos) == 2
    monkeypatch.setattr(pd, "read_sql_query", consulta_original)
    nova_leitura = carregar_base(caminho)
    assert nova_leitura[0].loc[nova_leitura[0]["codigo"] == "A", "estoque"].item() == 99
    assert nova_leitura[1]["quantidade"].sum() == 54


@pytest.mark.parametrize("quantidade", [-1, 1.5, True, float("nan"), float("inf"), "3", 2**63])
def test_decisao_rejeita_quantidade_invalida(tmp_path, quantidade):
    """A decisão não pode guardar frações, booleanos, números não finitos ou texto."""
    produtos, vendas = base_ficticia()
    caminho = tmp_path / "base.db"
    salvar_importacao(produtos, vendas, caminho, "2020-01-20")
    with pytest.raises(ValueError):
        registrar_decisao("A", quantidade, "Ignorar", caminho=caminho)
    assert listar_decisoes(caminho).empty


@pytest.mark.parametrize("observacao", [None, 1, "x" * 501])
def test_decisao_rejeita_observacao_invalida(tmp_path, observacao):
    """A observação segue o mesmo contrato da interface e não é truncada em silêncio."""
    produtos, vendas = base_ficticia()
    caminho = tmp_path / "base.db"
    salvar_importacao(produtos, vendas, caminho, "2020-01-20")
    with pytest.raises(ValueError):
        registrar_decisao("A", 1, "Ignorar", observacao, caminho=caminho)
    assert listar_decisoes(caminho).empty


def test_decisao_sku_desconhecido_rejeitada_sem_sql_executavel(tmp_path):
    """O conteúdo do SKU é parâmetro, nunca parte executável de uma consulta."""
    produtos, vendas = base_ficticia()
    caminho = tmp_path / "base.db"
    salvar_importacao(produtos, vendas, caminho, "2020-01-20")
    with pytest.raises(ValueError, match="não encontrado"):
        registrar_decisao("A'; DROP TABLE produtos;--", 1, "Ignorar", caminho=caminho)
    assert len(carregar_base(caminho)[0]) == 2


def test_backup_nao_sobrescreve_banco_existente(tmp_path):
    """Um backup anterior jamais é substituído implicitamente por uma cópia nova."""
    produtos, vendas = base_ficticia()
    caminho = tmp_path / "base.db"
    destino = tmp_path / "backup.db"
    salvar_importacao(produtos, vendas, caminho, "2020-01-20")
    with sqlite3.connect(destino) as banco:
        banco.execute("CREATE TABLE anterior (id INTEGER)")
        banco.execute("INSERT INTO anterior VALUES (42)")
    antes = destino.read_bytes()
    with pytest.raises(FileExistsError):
        backup(caminho, destino)
    assert destino.read_bytes() == antes


def test_backup_nao_pode_ser_a_origem(tmp_path):
    """Rejeitar a mesma origem evita travamento ou corrupção na cópia."""
    produtos, vendas = base_ficticia()
    caminho = tmp_path / "base.db"
    salvar_importacao(produtos, vendas, caminho, "2020-01-20")
    antes = assinatura(caminho)
    with pytest.raises(ValueError, match="diferente"):
        backup(caminho, caminho)
    assert assinatura(caminho) == antes


def test_backup_invalido_remove_apenas_copia_incompleta(tmp_path):
    """A falha de cópia preserva a origem e não deixa um backup aparentemente válido."""
    caminho = tmp_path / "invalido.db"
    caminho.write_bytes(b"conteudo ficticio que nao e sqlite")
    destino = tmp_path / "copia.db"
    with pytest.raises(sqlite3.DatabaseError):
        backup(caminho, destino)
    assert not destino.exists()
    assert caminho.read_bytes() == b"conteudo ficticio que nao e sqlite"


def test_backup_restaura_catalogo_vendas_historico_e_decisoes(tmp_path):
    """A cópia pode ser aberta como base independente, sem tocar na origem."""
    produtos, vendas = base_ficticia()
    caminho = tmp_path / "base.db"
    salvar_importacao(produtos, vendas, caminho, "2020-01-20")
    registrar_decisao("A", 3, "Avaliar compra", "Conferir fornecedor", caminho=caminho)
    copia = backup(caminho, tmp_path / "restaurada.db")
    assert assinatura(copia) == assinatura(caminho)
    assert len(carregar_base(copia)[0]) == 2
    with sqlite3.connect(copia) as banco:
        assert banco.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert banco.execute("PRAGMA foreign_key_check").fetchall() == []
