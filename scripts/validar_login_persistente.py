"""Prova cookie, F5, reabertura, logout e isolamento em Chromium com dados DEMO."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from validar_interface_vendas import servidor


def entrar(pagina, lembrar=True):
    """Usa credenciais públicas fictícias que o transporte nunca envia ao Supabase."""
    pagina.get_by_label("E-mail", exact=True).fill("operador@example.test")
    pagina.get_by_label("Senha", exact=True).fill("Senha-ficticia-12345")
    if not lembrar:
        pagina.get_by_label("Manter acesso neste navegador por até 30 dias", exact=True).uncheck()
    pagina.get_by_role("button", name="Entrar", exact=True).click()
    pagina.get_by_role("radiogroup", name="Navegação", exact=True).wait_for(state="attached", timeout=30000)


def main():
    """Mantém todas as verificações isoladas da aplicação publicada e do banco real."""
    from playwright.sync_api import expect, sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    control = output / "controle-ficticio.json"
    control.write_text("{}")
    os.environ["GERENTE_PERSISTENT_CONTROL"] = str(control)
    result = {"aprovado": False, "natureza": "UI e criptografia reais; transporte e credenciais DEMO.", "fluxos": {}}
    try:
        with servidor(output / "streamlit-demo.log", fixture="persistent_browser_app.py",
                      flag="GERENTE_PERSISTENT_BROWSER_FIXTURE") as original, sync_playwright() as tool:
            # Chromium admite cookies Secure de desenvolvimento no localhost.
            base = original.replace("127.0.0.1", "localhost")
            browser = tool.chromium.launch()
            try:
                ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = ctx.new_page()
                page.goto(base, wait_until="domcontentloaded")
                entrar(page)
                page.wait_for_function("document.cookie.includes('__Host-flatter_session=')", timeout=20000)
                cookie = next(c for c in ctx.cookies() if c["name"] == "__Host-flatter_session")
                assert cookie["secure"] and cookie["sameSite"] == "Strict" and cookie["path"] == "/"
                assert len(cookie["value"]) == 76 and "refresh" not in cookie["value"]
                expect(page.get_by_text("DEMO: entradas por senha = 1", exact=True)).to_be_visible()
                page.reload(wait_until="domcontentloaded")
                page.get_by_role("radiogroup", name="Navegação", exact=True).wait_for(state="attached", timeout=30000)
                expect(page.get_by_text("DEMO: entradas por senha = 1", exact=True)).to_be_visible()
                assert page.get_by_role("button", name="Entrar", exact=True).count() == 0
                result["fluxos"]["f5-sem-novo-login"] = "aprovado"
                page.close()
                page = ctx.new_page()
                page.goto(base, wait_until="domcontentloaded")
                page.get_by_role("radiogroup", name="Navegação", exact=True).wait_for(state="attached", timeout=30000)
                expect(page.get_by_text("DEMO: entradas por senha = 1", exact=True)).to_be_visible()
                result["fluxos"]["fechar-e-reabrir-aba"] = "aprovado"
                outro = browser.new_context()
                anonimo = outro.new_page()
                anonimo.goto(base, wait_until="domcontentloaded")
                expect(anonimo.get_by_role("button", name="Entrar", exact=True)).to_be_visible()
                assert anonimo.get_by_role("radiogroup", name="Navegação", exact=True).count() == 0
                outro.close()
                result["fluxos"]["isolamento-entre-navegadores"] = "aprovado"
                page.get_by_role("button", name="Sair", exact=True).click()
                expect(page.get_by_role("button", name="Entrar", exact=True)).to_be_visible()
                page.wait_for_function("!document.cookie.includes('__Host-flatter_session=')", timeout=20000)
                page.reload(wait_until="domcontentloaded")
                expect(page.get_by_role("button", name="Entrar", exact=True)).to_be_visible()
                result["fluxos"]["sair-invalida-retomada"] = "aprovado"
                entrar(page)
                page.wait_for_function("document.cookie.includes('__Host-flatter_session=')", timeout=20000)
                control.write_text('{"deactivated":true}')
                page.reload(wait_until="domcontentloaded")
                expect(page.get_by_role("button", name="Entrar", exact=True)).to_be_visible(timeout=30000)
                page.wait_for_function("!document.cookie.includes('__Host-flatter_session=')", timeout=20000)
                result["fluxos"]["desativacao-bloqueia-retomada"] = "aprovado"
                control.write_text("{}")
                entrar(page, lembrar=False)
                page.reload(wait_until="domcontentloaded")
                expect(page.get_by_role("button", name="Entrar", exact=True)).to_be_visible(timeout=30000)
                result["fluxos"]["opcao-desmarcada-nao-persiste"] = "aprovado"
                ctx.close()
                mobile = browser.new_context(viewport={"width": 390, "height": 1000})
                page = mobile.new_page()
                page.goto(base, wait_until="domcontentloaded")
                entrar(page)
                page.wait_for_function("document.cookie.includes('__Host-flatter_session=')", timeout=20000)
                page.reload(wait_until="domcontentloaded")
                page.get_by_role("radiogroup", name="Navegação", exact=True).wait_for(state="attached", timeout=30000)
                assert page.get_by_test_id("stException").count() == 0
                result["fluxos"]["f5-no-celular"] = "aprovado"
                mobile.close()
                result["aprovado"] = True
            finally:
                browser.close()
    finally:
        (output / "login-persistente.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print("RESULTADO_LOGIN_PERSISTENTE=" + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
