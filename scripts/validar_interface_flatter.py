"""Valida a UI Streamlit em Chromium, exclusivamente com dados fictícios.

Execução: python scripts/validar_interface_flatter.py --output evidencias/interface
Instale Playwright e seu Chromium no ambiente de testes. O script abre somente
um servidor em loopback e usa a fábrica exclusiva de tests/fixtures. Não altera
o aplicativo publicado, suas configurações, o banco Supabase ou o ERP.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

RAIZ = Path(__file__).resolve().parents[1]
SITE_PUBLICADO = "https://estoqueinteligente-flatter.streamlit.app/"


@contextmanager
def servidor(controle: Path, log: Path):
    """Inicia a fixture em loopback e sempre encerra o processo ao sair do teste."""
    with socket.socket() as reserva:
        reserva.bind(("127.0.0.1", 0))
        porta = reserva.getsockname()[1]
    ambiente = os.environ.copy()
    ambiente.update(GERENTE_BROWSER_FIXTURE="1", GERENTE_BROWSER_CONTROL=str(controle), PYTHONPATH=str(RAIZ))
    with log.open("w") as saida:
        processo = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", str(RAIZ / "tests/fixtures/flatter_browser_app.py"),
             "--server.address=127.0.0.1", f"--server.port={porta}", "--server.headless=true",
             "--browser.gatherUsageStats=false"],
            cwd=RAIZ, env=ambiente, stdout=saida, stderr=subprocess.STDOUT,
        )
        try:
            cliente = build_opener(ProxyHandler({}))
            limite = time.monotonic() + 45
            while time.monotonic() < limite:
                if processo.poll() is not None:
                    raise AssertionError("O servidor da fixture terminou antes de iniciar.")
                try:
                    with cliente.open(f"http://127.0.0.1:{porta}/_stcore/health", timeout=1) as resposta:
                        if resposta.status == 200:
                            break
                except (URLError, TimeoutError):
                    time.sleep(0.2)
            else:
                raise AssertionError("O servidor da fixture não iniciou dentro do prazo.")
            yield f"http://127.0.0.1:{porta}/"
        finally:
            processo.terminate()
            try:
                processo.wait(timeout=5)
            except subprocess.TimeoutExpired:
                processo.kill()
                processo.wait(timeout=5)


def aguardar_tela(pagina) -> None:
    """Aguarda o fim do rerun e rejeita exceções visíveis antes de obter evidências."""
    pagina.wait_for_function(
        "() => document.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') === 'notRunning'",
        timeout=30000,
    )
    assert pagina.get_by_test_id("stException").count() == 0, "A UI apresentou uma exceção."


def abrir_menu(pagina) -> None:
    """Abre a sidebar no celular sem depender da posição dos elementos da página."""
    navegacao = pagina.get_by_role("radiogroup", name="Navegação", exact=True)
    barra = pagina.get_by_test_id("stSidebar")
    if barra.count() and barra.get_attribute("data-collapsed") == "false":
        return
    # Atributo de teste do componente nativo, restrito ao teste de navegador.
    for controle in (pagina.get_by_test_id("stSidebarCollapsedControl").get_by_role("button"),
                     pagina.get_by_role("button", name=re.compile("Open sidebar|Expand sidebar", re.I))):
        if controle.count() and controle.first.is_visible():
            controle.first.click()
            break
    navegacao.wait_for(state="visible")
    pagina.wait_for_function("() => document.querySelector('[data-testid=stSidebar]')?.getBoundingClientRect().x >= -1")


def fechar_menu(pagina) -> None:
    """Recolhe o menu no celular para conferir a área útil e impedir overlay na captura."""
    if pagina.viewport_size["width"] >= 700:
        return
    controle = pagina.get_by_test_id("stSidebarCollapseButton").get_by_role("button")
    if controle.count() and controle.first.is_visible():
        controle.first.click()
        pagina.wait_for_function("() => document.querySelector('[data-testid=stSidebar]')?.getBoundingClientRect().width <= 1")


def navegar(pagina, destino: str) -> None:
    """Aciona a navegação real da aplicação por rótulo acessível."""
    abrir_menu(pagina)
    pagina.get_by_role("radio", name=destino, exact=True).check()
    titulos = {"Painel": "Visão geral", "Estoque": "Estoque e histórico",
               "Reposição": "Reposição por fornecedor", "Relatórios": "Relatório mensal",
               "Administração": "Administração"}
    if destino in titulos:
        pagina.get_by_role("heading", name=titulos[destino], exact=True).wait_for()
    aguardar_tela(pagina)
    fechar_menu(pagina)


def verificar_layout(pagina, nome: str, destino: Path, estrito: bool, miniaturas: bool) -> dict:
    """Registra dimensões reais, captura fictícia e alvos principais sem registrar dados."""
    aguardar_tela(pagina)
    pagina.evaluate("document.fonts.ready")
    medidas = pagina.evaluate("""() => {
      const raiz = document.documentElement;
      const main = document.querySelector('[data-testid=stMain]') || document.body;
      const controles = [...document.querySelectorAll('button, input, [role=combobox]')]
        .filter(el => el.getClientRects().length && !el.disabled)
        .map(el => { const r=el.getBoundingClientRect(); return {tag:el.tagName, w:r.width, h:r.height}; });
      const acoes = [...document.querySelectorAll('[class*="st-key-flatter_"] button')]
        .filter(el => el.getClientRects().length && !el.disabled && el.innerText.trim().length > 3)
        .map(el => { const r=el.getBoundingClientRect(); return {w:r.width, h:r.height}; });
      return {viewport:innerWidth, documento:raiz.scrollWidth,
        principal_visivel:main.clientWidth, principal_conteudo:main.scrollWidth,
        alvos_total:controles.length,
        alvos_menores_44:controles.filter(r => r.w<44 || r.h<44).length,
        acoes_aplicacao_total:acoes.length,
        acoes_aplicacao_menores_44:acoes.filter(r => r.w<43.5 || r.h<43.5).length};
    }""")
    medidas["sem_rolagem_horizontal_pagina"] = (
        medidas["documento"] <= medidas["viewport"] + 2
        and medidas["principal_conteudo"] <= medidas["principal_visivel"] + 2
    )
    imagem = destino / f"{nome}.png"
    pagina.screenshot(path=str(imagem), full_page=False, animations="disabled")
    medidas["captura"] = imagem.name
    if miniaturas:
        from PIL import Image

        with Image.open(imagem) as origem:
            origem.thumbnail((720, 800))
            buffer = io.BytesIO()
            origem.convert("RGB").save(buffer, format="JPEG", quality=75, optimize=True)
        # São apenas pixels de dados fictícios; nenhum log de payload ou Secrets.
        print("FLATTER_SCREENSHOT_BEGIN " + imagem.stem)
        print(base64.b64encode(buffer.getvalue()).decode())
        print("FLATTER_SCREENSHOT_END")
    print("FLATTER_LAYOUT_RESULT=" + json.dumps({"tela": nome, **medidas}, ensure_ascii=False))
    if estrito:
        assert medidas["sem_rolagem_horizontal_pagina"], f"Há rolagem horizontal na página {nome}."
        assert medidas["acoes_aplicacao_menores_44"] == 0, f"Há ação com alvo menor que 44px em {nome}."
    return medidas


def entrar(pagina, perfil: str) -> None:
    """Percorre o formulário verdadeiro com um endereço reservado fictício."""
    pagina.get_by_label("E-mail", exact=True).fill(perfil.lower() + "@example.invalid")
    pagina.get_by_label("Senha", exact=True).fill("Senha-ficticia-local-2026!")
    pagina.get_by_role("button", name="Entrar", exact=True).click()
    pagina.get_by_role("radiogroup", name="Navegação", exact=True).wait_for(state="attached")
    aguardar_tela(pagina)
    fechar_menu(pagina)


def conferir_perfil(pagina, perfil: str) -> None:
    """Confirma os menus e valores permitidos para o perfil exibido no navegador."""
    menus = pagina.get_by_role("radiogroup", name="Navegação", exact=True)
    if perfil != "Master":
        assert menus.get_by_role("radio", name="Administração", exact=True, include_hidden=True).count() == 0
        assert menus.get_by_role("radio", name="Importação", exact=True, include_hidden=True).count() == 0
        assert "R$" not in pagina.locator("body").inner_text()
        assert pagina.get_by_text("Capital conhecido em estoque", exact=True).count() == 0
    if perfil == "Visualizador":
        assert menus.get_by_role("radio", include_hidden=True).count() == 1
        assert menus.get_by_role("radio", name="Painel", exact=True, include_hidden=True).count() == 1


def verificar_fluxos(browser, base: str, destino: Path, controle: Path, estrito: bool,
                     miniaturas: bool) -> dict:
    """Exercita telas, dois tamanhos, três perfis e revogação em sessões independentes."""
    resultados: dict = {"telas": {}, "fluxos": {}, "limitacoes": [
        "UI real com transporte fictício; Auth remoto e RLS são testados por suítes separadas.",
        "Contagem de alvos pequenos inclui controles nativos e não equivale a auditoria WCAG integral.",
        "Capturas não representam publicação nem dados empresariais.",
    ]}
    contextos = []
    try:
        for largura in (1440, 390):
            contexto = browser.new_context(viewport={"width": largura, "height": 1000}, locale="pt-BR")
            contextos.append(contexto)
            pagina = contexto.new_page()
            pagina.goto(base + "?perfil=Master", wait_until="domcontentloaded")
            pagina.get_by_role("button", name="Entrar", exact=True).wait_for()
            resultados["telas"][f"login-{largura}"] = verificar_layout(
                pagina, f"login-{largura}", destino, estrito, miniaturas,
            )
            # Teclado realmente alcança campos e o envio do formulário.
            pagina.get_by_label("E-mail", exact=True).focus()
            pagina.keyboard.press("Tab")
            assert pagina.get_by_label("Senha", exact=True).evaluate("el => el === document.activeElement")
            entrar(pagina, "Master")
            resultados["telas"][f"painel-{largura}"] = verificar_layout(
                pagina, f"painel-{largura}", destino, estrito, miniaturas,
            )
            navegar(pagina, "Estoque")
            pagina.get_by_label("Buscar por nome ou SKU", exact=True).fill("FT-00002")
            pagina.get_by_label("Buscar por nome ou SKU", exact=True).press("Enter")
            aguardar_tela(pagina)
            pagina.get_by_text("SKU FT-00002", exact=False).wait_for()
            graficos = pagina.get_by_label("Mostrar gráficos do produto", exact=True)
            graficos.check()
            pagina.get_by_text("Snapshots registrados", exact=True).wait_for()
            aguardar_tela(pagina)
            pagina.get_by_role("heading", name="Produto fictício 0002", exact=True).scroll_into_view_if_needed()
            resultados["telas"][f"estoque-detalhe-{largura}"] = verificar_layout(
                pagina, f"estoque-detalhe-{largura}", destino, estrito, miniaturas,
            )
            navegar(pagina, "Relatórios")
            gerar = pagina.get_by_role("button", name="Gerar relatório", exact=True)
            assert pagina.get_by_text("Unidades vendidas", exact=True).count() == 0
            gerar.click()
            pagina.get_by_text("Unidades vendidas", exact=True).wait_for()
            resultados["telas"][f"relatorios-{largura}"] = verificar_layout(
                pagina, f"relatorios-{largura}", destino, estrito, miniaturas,
            )
            if largura == 1440:
                with pagina.expect_download() as transferencia:
                    pagina.get_by_role("button", name="Baixar relatório mensal (CSV)", exact=True).click()
                caminho_csv = transferencia.value.path()
                assert caminho_csv and Path(caminho_csv).stat().st_size > 0
                resultados["fluxos"]["download-mensal-sob-demanda"] = "aprovado"
            navegar(pagina, "Administração")
            pagina.get_by_label("Usuário para editar", exact=True).wait_for()
            resultados["telas"][f"administracao-{largura}"] = verificar_layout(
                pagina, f"administracao-{largura}", destino, estrito, miniaturas,
            )
            resultados["fluxos"][f"master-{largura}"] = "aprovado"

        # Contextos simultâneos impedem um falso resultado causado por logout global do navegador.
        paginas = {}
        for perfil in ("Master", "Operador", "Visualizador"):
            contexto = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="pt-BR")
            contextos.append(contexto)
            pagina = contexto.new_page()
            pagina.goto(base + "?perfil=" + perfil, wait_until="domcontentloaded")
            pagina.get_by_role("button", name="Entrar", exact=True).wait_for()
            entrar(pagina, perfil)
            conferir_perfil(pagina, perfil)
            paginas[perfil] = pagina

        master = paginas["Master"]
        assert "R$" in master.locator("body").inner_text()
        controle.write_text(json.dumps({"sem_financeiro": ["Master"]}))
        # Espera o watcher produtivo; o teste não acelera nem desliga o monitor.
        master.wait_for_function("() => !document.body.innerText.includes('R$')", timeout=35000)
        assert master.get_by_role("radiogroup", name="Navegação", exact=True).count() == 1
        resultados["fluxos"]["revogacao-financeira-em-tela-aberta"] = "aprovado"

        master.get_by_role("button", name="Sair", exact=True).click()
        master.get_by_role("button", name="Entrar", exact=True).wait_for()
        navegar(paginas["Operador"], "Estoque")
        assert paginas["Operador"].get_by_label("Buscar por nome ou SKU", exact=True).is_visible()
        conferir_perfil(paginas["Visualizador"], "Visualizador")
        resultados["fluxos"]["logout-isolado-entre-contextos"] = "aprovado"

        controle.write_text(json.dumps({"desativados": ["Operador"]}))
        paginas["Operador"].get_by_role("button", name="Entrar", exact=True).wait_for(timeout=35000)
        assert paginas["Operador"].get_by_test_id("stDataFrame").count() == 0
        assert paginas["Operador"].get_by_test_id("stMetric").count() == 0
        conferir_perfil(paginas["Visualizador"], "Visualizador")
        resultados["fluxos"]["desativacao-sem-interacao"] = "aprovado"
        resultados["fluxos"]["perfis-e-financeiro"] = "aprovado"
    finally:
        for contexto in contextos:
            contexto.close()
    return resultados


def conferir_url_publicada(browser) -> dict:
    """Observa somente o login público, sem credenciais, cliques ou capturas de dados."""
    contexto = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="pt-BR")
    try:
        pagina = contexto.new_page()
        resposta = pagina.goto(SITE_PUBLICADO, wait_until="domcontentloaded", timeout=45000)
        try:
            pagina.get_by_label("E-mail", exact=True).wait_for(timeout=15000)
            pagina.get_by_label("Senha", exact=True).wait_for(timeout=15000)
            login = pagina.get_by_role("button", name="Entrar", exact=True).is_visible()
        except Exception:
            login = False
        return {"url": SITE_PUBLICADO, "http_status": resposta.status if resposta else None,
                "formulario_login_observado": login, "autenticado": False,
                "limite": "Somente abertura anônima; nenhuma operação na aplicação publicada."}
    except Exception:
        return {"url": SITE_PUBLICADO, "formulario_login_observado": False, "autenticado": False,
                "limite": "A URL publicada não pôde ser validada por este ambiente."}
    finally:
        contexto.close()


def main() -> None:
    """Coordena evidências reprodutíveis, com artefato JSON inclusive em falha."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("evidencias/interface"))
    parser.add_argument("--strict-layout", action="store_true")
    parser.add_argument("--emit-thumbnails", action="store_true")
    parser.add_argument("--check-public-login", action="store_true")
    argumentos = parser.parse_args()
    destino = argumentos.output.resolve()
    destino.mkdir(parents=True, exist_ok=True)
    resultados = {"aprovado": False, "natureza": "Navegador real, dados e transporte fictícios."}
    try:
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory(prefix="flatter-browser-") as temporario:
            controle = Path(temporario) / "controle.json"
            controle.write_text("{}")
            with servidor(controle, destino / "streamlit-ficticio.log") as base, sync_playwright() as ferramenta:
                navegador = ferramenta.chromium.launch()
                try:
                    resultados.update(verificar_fluxos(
                        navegador, base, destino, controle, argumentos.strict_layout, argumentos.emit_thumbnails,
                    ))
                    if argumentos.check_public_login:
                        resultados["publicado"] = conferir_url_publicada(navegador)
                    resultados["aprovado"] = True
                finally:
                    navegador.close()
    except Exception as erro:
        # Não inclui corpo de página, dados de widget, token ou mensagem de transporte.
        resultados["falha_tipo"] = type(erro).__name__
        raise
    finally:
        (destino / "validacao-interface.json").write_text(json.dumps(resultados, ensure_ascii=False, indent=2))
        print("RESULTADO_INTERFACE=" + json.dumps(resultados, ensure_ascii=False))


if __name__ == "__main__":
    main()
