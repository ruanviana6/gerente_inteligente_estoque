"""Confere a identificação segura do iframe usado pelo Streamlit Cloud."""

from scripts.verificar_publicacao import URL, selecionar_aplicacao


class FrameFicticio:
    """Representa somente os sinais públicos necessários para localizar o app."""

    def __init__(self, url, marcador=False):
        """Define origem e presença do marcador sem dados ou tokens de autenticação."""
        self.url = url
        self.marcador = marcador

    def locator(self, seletor):
        """Retorna a existência do marcador solicitado pelo verificador."""
        assert seletor == '[data-flatter-release]'
        return self

    def count(self):
        """Informa se este quadro possui um marcador de versão."""
        return int(self.marcador)


def test_localiza_aplicacao_dentro_da_pagina_do_cloud():
    """A moldura externa não deve ser confundida com o formulário do aplicativo."""
    pagina = FrameFicticio(URL)
    aplicacao = FrameFicticio(URL + '~/+/', marcador=True)
    pagina.frames = [pagina, FrameFicticio('https://statuspage.io/embed/'), aplicacao]
    assert selecionar_aplicacao(pagina) is aplicacao


def test_ignora_marcador_em_origem_externa():
    """Um iframe de terceiro não pode provar a publicação do nosso código."""
    pagina = FrameFicticio(URL)
    externo = FrameFicticio('https://terceiro.example/~/+/', marcador=True)
    pagina.frames = [pagina, externo]
    assert selecionar_aplicacao(pagina) is pagina


def test_reconhece_iframe_antes_do_carregamento_do_streamlit():
    """O diagnóstico ainda acompanha a origem correta antes de surgir o marcador."""
    pagina = FrameFicticio(URL)
    aplicacao = FrameFicticio(URL + '~/+/')
    pagina.frames = [pagina, aplicacao]
    assert selecionar_aplicacao(pagina) is aplicacao


def test_funciona_com_aplicativo_sem_moldura():
    """Uma página Streamlit direta continua elegível para validação."""
    pagina = FrameFicticio(URL, marcador=True)
    pagina.frames = [pagina]
    assert selecionar_aplicacao(pagina) is pagina
