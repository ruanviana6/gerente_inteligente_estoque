"""Valida as três visões comerciais a 1440px/390px com RPC e dados fictícios."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from validar_interface_flatter import aguardar_tela, verificar_layout
from validar_interface_vendas import servidor


def main():
    """Captura a UI verdadeira e bloqueia toda rede externa no contexto do teste."""
    from playwright.sync_api import expect, sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    destino = args.output.resolve()
    destino.mkdir(parents=True, exist_ok=True)
    resultado = {"aprovado": False, "natureza": "Páginas reais; dados e RPC fictícios; sem Auth produtivo.", "telas": {}}
    try:
        with servidor(destino / "streamlit-demo.log", fixture="commercial_browser_app.py",
                      flag="GERENTE_COMMERCIAL_BROWSER_FIXTURE") as base, sync_playwright() as tool:
            browser = tool.chromium.launch()
            try:
                for largura in (1440, 390):
                    contexto = browser.new_context(viewport={"width": largura, "height": 1000}, locale="pt-BR")
                    contexto.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base)
                                   or r.request.url.startswith(("data:", "blob:")) else r.abort())
                    pagina = contexto.new_page()
                    pagina.goto(base, wait_until="domcontentloaded")
                    for visao, evidencia in (("Clientes", "Volume de compras diminuiu"), ("Cidades", "Demanda da cidade diminuiu"),
                                              ("Produtos Juntos", "Produtos comprados no mesmo pedido")):
                        pagina.get_by_role("radiogroup", name="Visão comercial").get_by_text(visao, exact=True).click()
                        expect(pagina.get_by_text(evidencia, exact=True)).to_be_visible(timeout=30000)
                        aguardar_tela(pagina)
                        assert pagina.get_by_test_id("stException").count() == 0
                        nome = f"comercial-{visao.lower().replace(' ', '-')}-{largura}"
                        resultado["telas"][nome] = verificar_layout(pagina, nome, destino, True, False)
                    contexto.close()
                resultado["aprovado"] = True
            finally:
                browser.close()
    finally:
        (destino / "validacao-comercial.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=2))
        print("RESULTADO_COMERCIAL=" + json.dumps(resultado, ensure_ascii=False))


if __name__ == "__main__":
    main()
