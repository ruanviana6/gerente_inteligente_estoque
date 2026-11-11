"""Confere a versão real no Streamlit, sem credenciais nem sessão Master."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

URL = "https://estoqueinteligente-flatter.streamlit.app/"


def observar(pagina, esperado):
    """Registra sinais de infraestrutura, nunca texto de negócios ou respostas HTTP."""
    marcador = pagina.locator('[data-flatter-release]').first
    versao = marcador.get_attribute('data-flatter-release') if marcador.count() else None
    login = pagina.get_by_role('button', name='Entrar', exact=True)
    return {"host": urlsplit(pagina.url).hostname, "version": versao, "expected_version": esperado,
            "login_visible": login.count() == 1 and login.is_visible(),
            "tables_without_login": pagina.get_by_test_id('stDataFrame').count(),
            "metrics_without_login": pagina.get_by_test_id('stMetric').count(),
            "signals": pagina.evaluate("""() => {
              const t = document.body?.innerText || '';
              return {root_children: document.querySelector('#root')?.childElementCount || 0,
                private_gate: /this app is private|request access|you do not have access/i.test(t),
                sleeping: /app (has gone to sleep|is sleeping|is hibernating)|wake (it|this app) up/i.test(t),
                human_check: /verify you are human|checking your browser|check.*connection.*secure/i.test(t),
                missing_config: /configura[çc][ãa]o pendente/i.test(t),
                exception_visible: !!document.querySelector('[data-testid="stException"]')};
            }""")}


def verificar(browser, esperado, destino):
    """Espera a atualização e valida acesso anônimo/credencial fictícia inválida."""
    resultado = {"url": URL, "aprovado": False, "authenticated_master_tested": False, "attempts": []}
    for _ in range(3):
        contexto = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="pt-BR")
        pagina = contexto.new_page()
        falhas = Counter()
        recursos = []
        def registrar_falha(request):
            """Registra somente host/caminho de scripts públicos, sem query ou corpo."""
            falhas.update([request.resource_type])
            if request.resource_type == 'script':
                url = urlsplit(request.url)
                recursos.append({'host': url.hostname, 'path': url.path[:200], 'failure': (request.failure or '')[:100]})
        pagina.on('requestfailed', registrar_falha)
        try:
            resposta = pagina.goto(URL, wait_until='domcontentloaded', timeout=45000)
            try:
                pagina.wait_for_function("""esperado => document.querySelector('[data-flatter-release]')
                  ?.getAttribute('data-flatter-release') === esperado""", arg=esperado, timeout=60000)
                pagina.get_by_label('E-mail', exact=True).wait_for(timeout=20000)
                pagina.get_by_role('button', name='Entrar', exact=True).wait_for(timeout=20000)
            except Exception:
                pass
            observacao = observar(pagina, esperado)
            observacao.update(http_status=resposta.status if resposta else None, failed_resource_types=dict(falhas), failed_scripts=recursos)
            resultado['attempts'].append(observacao)
            if observacao['version'] != esperado or not observacao['login_visible']:
                continue
            assert observacao['tables_without_login'] == observacao['metrics_without_login'] == 0
            assert not observacao['signals']['exception_visible']
            pagina.screenshot(path=str(destino / 'login-publicado-computador.png'), full_page=True)
            pagina.set_viewport_size({"width": 390, "height": 1000})
            pagina.get_by_label('E-mail', exact=True).wait_for()
            assert pagina.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
            pagina.screenshot(path=str(destino / 'login-publicado-celular.png'), full_page=True)
            # Conta deliberadamente inexistente; não cria usuário, não envia
            # e-mail e não utiliza a conta nem a sessão do Master.
            pagina.get_by_label('E-mail', exact=True).fill('verificacao-invalida@example.invalid')
            pagina.get_by_label('Senha', exact=True).fill('Senha-ficticia-invalida-2026!')
            pagina.get_by_role('button', name='Entrar', exact=True).click()
            pagina.get_by_text('Não foi possível entrar. Confira os dados e se sua conta está autorizada.', exact=True).wait_for(timeout=30000)
            assert pagina.get_by_test_id('stDataFrame').count() == 0
            assert pagina.get_by_test_id('stMetric').count() == 0
            resultado.update(aprovado=True, invalid_login_denied=True, responsive_login_verified=True)
            break
        except Exception as erro:
            resultado['attempts'].append({"failure_type": type(erro).__name__})
        finally:
            contexto.close()
    return resultado


def main():
    """Falha se não observar a versão; HTTP 200 isoladamente nunca é aprovação."""
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-version', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as tool:
        browser = tool.chromium.launch()
        try:
            resultado = verificar(browser, args.expected_version, args.output)
        finally:
            browser.close()
    resultado['limitations'] = 'Sem login Master, criação de contas ou acesso a dados empresariais. Não comprova fluxos autenticados.'
    (args.output / 'verificacao-publicada.json').write_text(json.dumps(resultado, ensure_ascii=False, indent=2))
    print('RESULTADO_PUBLICADO=' + json.dumps(resultado, ensure_ascii=False))
    return 0 if resultado['aprovado'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
