"""Confere que a interface fiscal de produção não abre cadastros DEMO."""

import argparse
import json
from pathlib import Path

from validar_interface_flatter import aguardar_tela, verificar_layout
from validar_interface_vendas import servidor


def verificar(browser, base, destino):
    """Usa fixture de teste isolada; nenhum botão deve criar notas fictícias."""
    from playwright.sync_api import expect
    results = {'aprovado': False, 'natureza': 'Navegador real, gateway exclusivamente fictício', 'telas': {}}
    for width in [1440, 390]:
        context = browser.new_context(viewport={'width': width, 'height': 1000}, locale='pt-BR')
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base)
                      or route.request.url.startswith(('data:', 'blob:')) else route.abort())
        try:
            page = context.new_page()
            page.goto(base, wait_until='domcontentloaded')
            expect(page.get_by_role('heading', name='Automação / Notas Fiscais', exact=True)).to_be_visible()
            expect(page.get_by_text('Controle fictício: execuções=0; solicitações=0', exact=True)).to_be_visible()
            # A aplicação produtiva NÃO deve mais aceitar pedidos fiscais DEMO.
            # O gateway da fixture continua existindo apenas para provar ausência de chamadas.
            expect(page.get_by_text(
                'Os pedidos fictícios e o cadastro DEMO foram retirados da operação.',
                exact=False,
            )).to_be_visible()
            expect(page.get_by_role('button', name='Consultar pedido DEMO', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='Gerar prévia DEMO', exact=True)).to_have_count(0)
            expect(page.get_by_role('button', name='Confirmar cadastro da NF-e · DEMO', exact=True)).to_have_count(0)
            expect(page.get_by_text('Controle fictício: execuções=0; solicitações=0', exact=True)).to_be_visible()
            aguardar_tela(page)
            results['telas'][f'fiscal-sem-demo-{width}'] = verificar_layout(
                page, f'fiscal-sem-demo-{width}', destino, True, False,
            )
            assert page.get_by_role('button', name='Baixar XML', exact=True).count() == 0
            assert page.get_by_role('button', name='Baixar PDF', exact=True).count() == 0
        except Exception:
            # A fixture contém somente dados fictícios, sem Auth produtivo.
            # Preservar o estado da falha permite diagnosticar o CI sem retry
            # silencioso, força de clique ou remoção das verificações.
            page.screenshot(path=str(destino / f'fiscal-falha-{width}.png'), full_page=True)
            (destino / f'fiscal-falha-{width}.txt').write_text(page.locator('body').aria_snapshot())
            raise
        finally:
            context.close()
    results['aprovado'] = True
    return results


def main():
    """Guarda somente evidências DEMO sem credenciais ou dados empresariais."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('evidencias/fiscal'))
    args = parser.parse_args()
    destino = args.output.resolve()
    destino.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    with servidor(destino / 'servidor.log', fixture='fiscal_browser_app.py', flag='GERENTE_FISCAL_BROWSER_FIXTURE') as base, sync_playwright() as driver:
        browser = driver.chromium.launch()
        try:
            result = verificar(browser, base, destino)
        finally:
            browser.close()
    (destino / 'fiscal.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print('RESULTADO_FISCAL=' + json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
