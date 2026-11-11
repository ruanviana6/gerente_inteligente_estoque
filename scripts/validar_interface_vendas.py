"""Valida páginas novas em Chromium a 1440px e 390px, usando apenas RPC/dados DEMO."""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from validar_interface_flatter import aguardar_tela, verificar_layout

RAIZ = Path(__file__).resolve().parents[1]


@contextmanager
def servidor(log: Path, *, fixture="sales_browser_app.py", flag="GERENTE_SALES_BROWSER_FIXTURE"):
    """Hospeda somente em loopback e encerra o processo inclusive em falha."""
    with socket.socket() as reserva:
        reserva.bind(("127.0.0.1", 0))
        port = reserva.getsockname()[1]
    env = {**os.environ, flag: "1", "PYTHONPATH": str(RAIZ)}
    with log.open("w") as output:
        process = subprocess.Popen([
            sys.executable, "-m", "streamlit", "run", str(RAIZ / "tests/fixtures" / fixture),
            "--server.address=127.0.0.1", f"--server.port={port}", "--server.headless=true",
            "--browser.gatherUsageStats=false",
        ], cwd=RAIZ, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            client = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError("A fixture terminou antes de iniciar.")
                try:
                    with client.open(f"http://127.0.0.1:{port}/_stcore/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except (URLError, TimeoutError):
                    time.sleep(0.2)
            else:
                raise AssertionError("A fixture não iniciou no prazo.")
            yield f"http://127.0.0.1:{port}/"
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def fechar_menu(page):
    """Libera a área útil do celular antes das capturas e ações."""
    if page.viewport_size["width"] >= 700:
        return
    control = page.get_by_test_id("stSidebarCollapseButton").get_by_role("button")
    if control.count() and control.first.is_visible():
        control.first.click()
        page.wait_for_function("() => document.querySelector('[data-testid=stSidebar]')?.getBoundingClientRect().x < -100")


def integrar(page):
    """Abre o menu e aguarda a animação antes de navegar no celular."""
    sidebar = page.get_by_test_id("stSidebar")
    if page.viewport_size["width"] < 700 and sidebar.get_attribute("data-collapsed") != "false":
        for control in (page.get_by_test_id("stSidebarCollapsedControl").get_by_role("button"),
                        page.get_by_role("button", name=re.compile("Open sidebar|Expand sidebar", re.I))):
            if control.count() and control.first.is_visible():
                control.first.click()
                break
        page.wait_for_function("() => document.querySelector('[data-testid=stSidebar]')?.getBoundingClientRect().x >= -1")
    page.get_by_role("radiogroup", name="Navegação DEMO").get_by_text("Integrações", exact=True).click()
    page.get_by_role("heading", name="Integrações", exact=True).wait_for()
    aguardar_tela(page)
    fechar_menu(page)


def verificar(browser, base: str, destino: Path, strict: bool):
    """Executa análise, staging, cancelamento e confirmação explícita nas duas larguras."""
    from playwright.sync_api import expect

    result = {"telas": {}, "fluxos": {}}
    for width in (1440, 390):
        context = browser.new_context(viewport={"width": width, "height": 1000}, locale="pt-BR")
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base)
                      or route.request.url.startswith(("data:", "blob:")) else route.abort())
        try:
            page = context.new_page()
            page.goto(base, wait_until="domcontentloaded")
            page.get_by_role("heading", name="Inteligência de Vendas", exact=True).wait_for()
            # O título pode chegar antes do restante da árvore transmitida pelo
            # Streamlit. Aguarde a última ação do formulário antes de medir.
            expect(page.get_by_role("button", name="Analisar vendas", exact=True)).to_be_visible(timeout=30000)
            aguardar_tela(page)
            fechar_menu(page)
            result["telas"][f"vendas-inicial-{width}"] = verificar_layout(
                page, f"vendas-inicial-{width}", destino, strict, False)
            page.get_by_role("button", name="Analisar vendas", exact=True).click()
            expect(page.get_by_role("heading", name="Produtos e crescimento observado", exact=True)).to_be_visible(timeout=30000)
            # notRunning sinaliza o servidor, mas o navegador ainda pode estar
            # conciliando a árvore React. A asserção com espera verifica a UI
            # efetivamente visível, sem sleeps ou cliques forçados.
            expect(page.get_by_text("Unidades líquidas", exact=True)).to_be_visible(timeout=30000)
            expect(page.get_by_test_id("stDataFrame").first).to_be_visible(timeout=30000)
            aguardar_tela(page)
            expect(page.get_by_text("Unidades líquidas", exact=True)).to_be_visible(timeout=30000)
            result["telas"][f"vendas-analisadas-{width}"] = verificar_layout(
                page, f"vendas-analisadas-{width}", destino, strict, False)
            integrar(page)
            result["telas"][f"integracoes-inicial-{width}"] = verificar_layout(
                page, f"integracoes-inicial-{width}", destino, strict, False)
            expect(page.get_by_text("Controle DEMO: confirmações=0; cancelamentos=0", exact=True)).to_be_visible()
            page.get_by_role("button", name="Preparar prévia", exact=True).click()
            expect(page.get_by_role("heading", name="Conferir prévia", exact=True)).to_be_visible(timeout=30000)
            expect(page.get_by_role("button", name="Cancelar esta prévia", exact=True)).to_be_visible(timeout=30000)
            aguardar_tela(page)
            expect(page.get_by_role("button", name="Confirmar sincronização interna", exact=True)).to_be_disabled()
            expect(page.get_by_text("Controle DEMO: confirmações=0; cancelamentos=0", exact=True)).to_be_visible()
            result["telas"][f"integracoes-previa-{width}"] = verificar_layout(
                page, f"integracoes-previa-{width}", destino, strict, False)
            page.get_by_role("button", name="Cancelar esta prévia", exact=True).click()
            expect(page.get_by_text("Controle DEMO: confirmações=0; cancelamentos=1", exact=True)).to_be_visible()
            aguardar_tela(page)
            page.get_by_role("button", name="Preparar prévia", exact=True).click()
            expect(page.get_by_role("heading", name="Conferir prévia", exact=True)).to_be_visible(timeout=30000)
            expect(page.get_by_role("button", name="Cancelar esta prévia", exact=True)).to_be_visible(timeout=30000)
            aguardar_tela(page)
            page.get_by_text("Conferi a amostra, quantidades, totais e abrangência completa do período", exact=True).click()
            expect(page.get_by_role("checkbox", name="Conferi a amostra, quantidades, totais e abrangência completa do período")).to_be_checked()
            expect(page.get_by_role("button", name="Confirmar sincronização interna", exact=True)).to_be_enabled()
            page.get_by_role("button", name="Confirmar sincronização interna", exact=True).click()
            expect(page.get_by_text("Controle DEMO: confirmações=1; cancelamentos=1", exact=True)).to_be_visible()
            aguardar_tela(page)
            result["fluxos"][str(width)] = {"analise_vendas": True, "previa_sem_commit_automatico": True,
                                           "cancelamento": True, "confirmacao_explicita": True}
        finally:
            context.close()
    return result


def main():
    """Salva JSON e capturas fictícias; não consulta a aplicação publicada."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("evidencias/vendas"))
    parser.add_argument("--strict-layout", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = {"aprovado": False, "natureza": "Chromium/páginas/cálculos reais, RPC e dados DEMO.",
              "limitacoes": ["Não verifica produção, ERP, Auth remoto ou RLS."]}
    try:
        from playwright.sync_api import sync_playwright

        with servidor(output / "streamlit-demo.log") as base, sync_playwright() as tool:
            browser = tool.chromium.launch()
            try:
                result.update(verificar(browser, base, output, args.strict_layout))
                result["aprovado"] = True
            finally:
                browser.close()
    except Exception as error:
        result["falha_tipo"] = type(error).__name__
        raise
    finally:
        (output / "validacao-vendas.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print("RESULTADO_VENDAS=" + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
