"""Valida fluxo fiscal DEMO em Chromium no computador e celular, sem ERP."""

import argparse
import json
from pathlib import Path

from validar_interface_flatter import aguardar_tela, verificar_layout
from validar_interface_vendas import servidor


def verificar(browser, base, destino):
    """Usa widgets reais e transporte fictício; RLS/Auth são testados separadamente."""
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
            page.get_by_label('Número do pedido do Atacado', exact=True).fill('1001')
            page.get_by_role('button', name='Consultar pedido DEMO', exact=True).click()
            expect(page.get_by_text('Dados fiscais manuais', exact=True)).to_be_visible()
            expect(page.get_by_text('Selecione uma modalidade', exact=True)).to_be_visible()
            expect(page.get_by_text('Selecione a situação fiscal validada', exact=True)).to_be_visible()
            aguardar_tela(page)
            results['telas'][f'fiscal-selecoes-{width}'] = verificar_layout(page, f'fiscal-selecoes-{width}', destino, True, False)
            page.get_by_role('combobox', name='Modalidade do frete', exact=True).click()
            page.get_by_role('option', name='1 · FOB: por conta do destinatário', exact=True).click()
            page.get_by_role('combobox', name='Tipo de contribuinte', exact=True).click()
            page.get_by_role('option', name='2 · Contribuinte isento de inscrição estadual', exact=True).click()
            page.get_by_role('button', name='Gerar prévia DEMO', exact=True).click()
            expect(page.get_by_role('heading', name='Prévia da NF-e · DEMO', exact=True)).to_be_visible()
            confirm = page.get_by_role('button', name='Confirmar solicitação DEMO', exact=True)
            expect(confirm).to_be_disabled()
            expect(page.get_by_text('Controle fictício: execuções=0; solicitações=0', exact=True)).to_be_visible()
            page.get_by_text('Conferi a prévia e confirmo somente a solicitação DEMO, sem emissão real', exact=True).click()
            expect(confirm).to_be_enabled()
            confirm.click()
            expect(page.get_by_text('Controle fictício: execuções=1; solicitações=1', exact=True)).to_be_visible()
            expect(page.get_by_text('DEMO: Autorizada (DEMO)', exact=True)).to_be_visible()
            aguardar_tela(page)
            results['telas'][f'fiscal-resultado-{width}'] = verificar_layout(page, f'fiscal-resultado-{width}', destino, True, False)
            assert page.get_by_role('button', name='Baixar XML', exact=True).count() == 0
            assert page.get_by_role('button', name='Baixar PDF', exact=True).count() == 0
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
