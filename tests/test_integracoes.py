"""Garante ausência de chamadas externas nos fluxos atualmente implementados."""

import socket

import pytest

from dados import carregar_de_csv, gerar_dados_exemplo
from integracoes.gestaoclick import sincronizar_somente_leitura
from motor import gerar_painel
from storage import carregar_base, salvar_importacao


@pytest.fixture
def rede_proibida(monkeypatch):
    """Falha antes de qualquer conexão; não usa URL, token ou ERP verdadeiro."""
    def bloquear(*args, **kwargs):
        """Uma chamada externa neste fluxo viola o escopo do MVP."""
        raise AssertionError("O fluxo local tentou abrir conexão de rede.")

    monkeypatch.setattr(socket, "create_connection", bloquear)
    monkeypatch.setattr(socket.socket, "connect", bloquear)


def test_conector_permanece_desabilitado_sem_rede(rede_proibida):
    """O placeholder não consulta nem modifica o GestãoClick."""
    with pytest.raises(NotImplementedError, match="não habilitada"):
        sincronizar_somente_leitura()


def test_importacao_e_calculo_nao_disparam_chamadas_externas(tmp_path, rede_proibida):
    """CSV, armazenamento e indicadores só utilizam os arquivos locais fictícios."""
    produtos, vendas = carregar_de_csv(
        b"codigo;nome;estoque\nA;Produto ficticio;10\n",
        b"data;codigo;quantidade\n2020-01-01;A;2\n",
    )
    caminho = tmp_path / "base.db"
    salvar_importacao(produtos, vendas, caminho, "2020-01-02")
    catalogo, historico, fotos, _ = carregar_base(caminho)
    painel = gerar_painel(catalogo, historico, fotos, hoje="2020-01-02")
    assert painel.loc[0, "estoque"] == 10


def test_demonstracao_nao_dispara_chamadas_externas(rede_proibida):
    """A geração fictícia é reproduzível e não depende de acesso ao ERP."""
    produtos, vendas, fotos = gerar_dados_exemplo("2020-01-02")
    assert len(gerar_painel(produtos, vendas, fotos, hoje="2020-01-02")) == 10
