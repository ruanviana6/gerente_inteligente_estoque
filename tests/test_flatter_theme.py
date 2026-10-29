"""Verificações de contraste e de renderização segura da identidade Flatter."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from web import theme


def _luminance(hex_color: str) -> float:
    """Lineariza canais sRGB para calcular a luminância relativa do WCAG 2.2."""
    canais = [int(hex_color[indice:indice + 2], 16) / 255 for indice in (1, 3, 5)]
    lineares = [canal / 12.92 if canal <= 0.04045 else ((canal + 0.055) / 1.055) ** 2.4
                for canal in canais]
    return sum(canal * peso for canal, peso in zip(lineares, (0.2126, 0.7152, 0.0722)))


def _contrast(foreground: str, background: str) -> float:
    """Calcula a razão de contraste, sem arredondar o valor usado na aprovação."""
    escuro, claro = sorted((_luminance(foreground), _luminance(background)))
    return (claro + 0.05) / (escuro + 0.05)


@pytest.mark.parametrize("foreground", ["text", "secondary", "brand_text", "danger", "warning", "success", "info"])
@pytest.mark.parametrize("background", ["background", "sidebar", "surface"])
def test_textos_do_tema_atendem_contraste_aa(foreground, background):
    """Texto normal e estados mantêm pelo menos 4,5:1 nas superfícies utilizadas."""
    assert _contrast(theme.PALETTE[foreground], theme.PALETTE[background]) >= 4.5


@pytest.mark.parametrize("background", ["background", "sidebar", "surface"])
def test_bordas_de_controles_atendem_contraste_nao_textual(background):
    """Bordas dos campos alcançam os 3:1 necessários para identificar controles."""
    assert _contrast(theme.PALETTE["border"], theme.PALETTE[background]) >= 3


def test_texto_sobre_acao_terracota_atende_contraste_aa():
    """A marca usa branco e o botão usa o texto claro do tema, ambos legíveis."""
    assert _contrast("#FFFFFF", theme.PALETTE["brand"]) >= 4.5
    assert _contrast(theme.PALETTE["text"], theme.PALETTE["brand"]) >= 4.5


def test_configuracao_nativa_coincide_com_paleta_e_preserva_protecao():
    """Impede divergência entre o tema de widgets e as superfícies em CSS."""
    import tomllib

    arquivo = Path(__file__).parents[1] / ".streamlit" / "config.toml"
    config = tomllib.loads(arquivo.read_text(encoding="utf-8"))
    tema = config["theme"]
    for nome, chave in (("primaryColor", "brand"), ("backgroundColor", "background"),
                        ("secondaryBackgroundColor", "surface"), ("textColor", "text"),
                        ("grayTextColor", "secondary"), ("linkColor", "brand_text"),
                        ("borderColor", "border")):
        assert tema[nome] == theme.PALETTE[chave]
    assert tema["sidebar"]["backgroundColor"] == theme.PALETTE["sidebar"]
    assert config["server"]["enableXsrfProtection"] is True
    assert config["server"]["enableCORS"] is True


def test_dados_de_apresentacao_nao_sao_interpolados_em_html(monkeypatch):
    """Nomes vindos do banco passam pelos widgets nativos, nunca por st.html."""
    interface = MagicMock()
    monkeypatch.setattr(theme, "st", interface)
    texto = '<img src=x onerror="alert(1)">'
    theme.page_header(texto, texto)
    theme.metric_card(texto, texto, key="produto", help=texto, note=texto)
    theme.status_badge(texto, tone="warning")
    interface.html.assert_not_called()
    interface.title.assert_called_once_with(texto, anchor=False)
    interface.metric.assert_called_once_with(texto, texto, help=texto)
    interface.badge.assert_called_once_with(texto, color="yellow")


@pytest.mark.parametrize("key", ["", "../produto", 'produto\" onclick=\"alert(1)', "a" * 81])
def test_identificador_visual_recusa_conteudo_arbitrario(key):
    """Chaves de CSS não podem receber nomes ou HTML de dados externos."""
    with pytest.raises(ValueError):
        theme.surface(key)


def test_tom_nao_previsto_nao_cria_estilo_arbitrario():
    """Os estilos são limitados à paleta revisada."""
    with pytest.raises(ValueError):
        theme.surface("produto", tone="cor_injetada")


def test_html_do_tema_e_estatico_e_nao_carrega_recursos_externos(monkeypatch):
    """Somente CSS e duas versões fixas do nome Flatter chegam ao HTML."""
    interface = MagicMock()
    monkeypatch.setattr(theme, "st", interface)
    theme.apply_theme()
    theme.brand_header()
    theme.brand_header(compact=True)
    assert interface.html.call_count == 3
    for chamada in interface.html.call_args_list:
        html = chamada.args[0].lower()
        assert "<script" not in html
        assert "http://" not in html
        assert "https://" not in html
        assert "data-testid" not in html
        assert "@import" not in html
