"""A loja escolhida pela interface deve ser uma fonte real ainda autorizada."""
from types import SimpleNamespace

from web import real_store_selector as tela


def test_loja_unica_e_automaticamente_selecionada_sem_componente(monkeypatch):
    monkeypatch.setattr(tela, "consultar_fontes_reais", lambda auth: (("fonte-1", "416023"),))
    monkeypatch.setattr(tela, "st", SimpleNamespace(
        session_state={}, selectbox=lambda *a, **k: (_ for _ in ()).throw(AssertionError("sem seletor")),
    ))
    assert tela.escolher_loja_real(object()) == "416023"


def test_duas_lojas_nao_escolhem_uma_aleatoria_e_limpam_selecao_antiga(monkeypatch):
    eventos = []
    estado = {"_loja_real_selecionada": "999999"}
    monkeypatch.setattr(tela, "consultar_fontes_reais", lambda auth: (
        ("fonte-1", "416023"), ("fonte-2", "424409"),
    ))

    def selecionar(label, options, **kwargs):
        eventos.append((label, options, kwargs, dict(estado)))
        return None

    monkeypatch.setattr(tela, "st", SimpleNamespace(session_state=estado, selectbox=selecionar))
    assert tela.escolher_loja_real(object()) is None
    assert "_loja_real_selecionada" not in estado
    assert eventos[0][1] == ["416023", "424409"]
    assert eventos[0][2]["index"] is None


def test_sem_fontes_reais_gera_orientacao_sem_expor_dados(monkeypatch):
    avisos = []
    monkeypatch.setattr(tela, "consultar_fontes_reais", lambda auth: ())
    monkeypatch.setattr(tela, "st", SimpleNamespace(info=avisos.append))
    assert tela.escolher_loja_real(object()) is None
    assert "Administração" in avisos[0]
