"""Regressões de escopo Auth, downloads por clique e transporte isolado."""

import csv
import importlib.util
import io
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from web.api import SupabaseAPI, _HTTPTransport, _NoRedirect
from web.errors import AuthenticationError, AuthorizationError
from web.settings import PROJECT_URL, Settings

_spec = importlib.util.spec_from_file_location(
    "flatter_security_fixture", Path(__file__).parent / "fixtures/flatter_data.py",
)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
Scenario = _fixture.Scenario


class AuthExecutionTests(unittest.TestCase):
    """Otimização de Auth não transforma permissões em cache de sessão."""

    def setUp(self):
        """Fornece uma sessão exclusiva com catálogo inteiramente fictício."""
        self.scenario = Scenario(size=10)
        self.service = self.scenario.service_factory({})
        self.auth = self.service.auth

    def test_apenas_auth_e_reutilizado_dentro_da_execucao(self):
        """Cada consulta ainda verifica app_context, mas Auth ocorre uma vez por rerun."""
        with self.service.execution():
            for _ in range(3):
                self.service.context()
        self.assertEqual(self.scenario.calls["auth_user"], 1)
        self.assertEqual(self.scenario.calls["rpc_app_context"], 3)
        with self.service.execution():
            self.service.context()
        self.assertEqual(self.scenario.calls["auth_user"], 2)

    def test_fora_de_escopo_nunca_reutiliza_auth(self):
        """Chamadas normais conservam revalidação integral por padrão."""
        self.service.context()
        self.service.context()
        self.assertEqual(self.scenario.calls["auth_user"], 2)

    def test_excecao_descarta_validacao_da_execucao(self):
        """Uma exceção não deixa autorização reutilizável para a execução seguinte."""
        with self.assertRaises(ValueError), self.service.execution():
            self.service.context()
            raise ValueError("Falha fictícia")
        self.service.context()
        self.assertEqual(self.scenario.calls["auth_user"], 2)

    def test_revogacao_de_permissao_e_imediata_no_mesmo_escopo(self):
        """Mesmo com Auth validado, uma permissão revogada impede a próxima RPC."""
        with self.service.execution():
            self.auth.require("inventory.view")
            self.scenario.permissions.discard("inventory.view")
            with self.assertRaises(AuthorizationError):
                self.service.load_inventory("inventory.view")
        self.assertEqual(self.scenario.calls["rpc_load_inventory"], 0)

    def test_conta_desativada_e_recusada_no_mesmo_escopo(self):
        """O app_context recém consultado descarta sessão de uma conta desativada."""
        with self.service.execution():
            self.service.context()
            self.scenario.active = False
            with self.assertRaises(AuthorizationError):
                self.service.context()
        self.assertIsNone(self.auth.session)

    def test_duas_instancias_nao_compartilham_validacao_ou_permissoes(self):
        """Um segundo navegador nunca herda o contexto financeiro do Master."""
        viewer = Scenario(size=1, role="Visualizador")
        other = viewer.service_factory({})
        with self.service.execution(), other.execution():
            self.assertTrue(self.service.context().can("finance.view"))
            self.assertFalse(other.context().can("finance.view"))
        self.assertEqual(self.scenario.calls["auth_user"], 1)
        self.assertEqual(viewer.calls["auth_user"], 1)

    def test_thread_nova_precisa_validar_auth_novamente(self):
        """O worker de download não herda a validação da thread da interface."""
        with self.service.execution():
            self.service.context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(self.service.context).result(timeout=5)
        self.assertEqual(self.scenario.calls["auth_user"], 2)

    def test_refresh_concorrente_acontece_uma_vez(self):
        """Duas operações simultâneas não consomem o mesmo refresh token em paralelo."""
        self.auth._session = replace(self.auth.session, expires_at=time.time() + 10)
        barrier = threading.Barrier(2)
        generation = self.auth.generation

        def operation():
            """Inicia dois contextos independentes no mesmo instante."""
            barrier.wait(timeout=5)
            with self.service.execution():
                return self.service.context()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(operation) for _ in range(2)]
            results = [future.result(timeout=5) for future in futures]
        self.assertEqual(self.scenario.calls["auth_other"], 1)
        self.assertEqual(self.scenario.calls["auth_user"], 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.auth.generation, generation)

    def test_refresh_no_escopo_exige_nova_validacao_auth(self):
        """Uma nova versão do token não usa a validação feita para o token anterior."""
        with self.service.execution():
            self.service.context()
            self.auth._session = replace(self.auth.session, expires_at=time.time() + 10)
            self.service.context()
        self.assertEqual(self.scenario.calls["auth_user"], 2)

    def test_relatorio_remove_financeiro_revogado_durante_calculo(self):
        """O relatório exibido também filtra permissões depois do cálculo demorado."""
        def generate(*args, **kwargs):
            """Retorna bloco fictício depois de revogar o acesso financeiro."""
            self.scenario.permissions.discard("finance.view")
            return {"resumo": {"unidades_vendidas": 3}, "financeiro": {"capital_estoque": 100},
                    "tendencias": [{"codigo": "FICTICIO", "classe_abc": "A"}]}

        with patch("web.reports.gerar_relatorio_mensal", side_effect=generate):
            result = self.service.monthly_report("2026-09-01")
        self.assertNotIn("financeiro", result)
        self.assertNotIn("classe_abc", result["tendencias"][0])

    def test_relatorio_recusa_visualizacao_revogada_durante_calculo(self):
        """Uma geração já iniciada não entrega resultados após revogar reports.view."""
        def generate(*args, **kwargs):
            """Revoga o relatório antes de retornar um resumo fictício."""
            self.scenario.permissions.discard("reports.view")
            return {"resumo": {"unidades_vendidas": 3}}

        with patch("web.reports.gerar_relatorio_mensal", side_effect=generate):
            with self.assertRaises(AuthorizationError):
                self.service.monthly_report("2026-09-01")

    def test_modulo_revogado_durante_consulta_nao_retorna_dados(self):
        """A fachada exige a permissão também depois da consulta dos dados."""
        original = self.auth.api._transport

        def transport(method, url, headers, payload, timeout):
            """Revoga a permissão após o banco devolver o lote fictício."""
            result = original(method, url, headers, payload, timeout)
            if url.endswith("/load_inventory"):
                self.scenario.permissions.discard("inventory.view")
            return result

        self.auth.api._transport = transport
        with self.service.execution(), self.assertRaises(AuthorizationError):
            self.service.load_inventory("inventory.view")


class DeferredExportTests(unittest.TestCase):
    """Callbacks não entregam dados de uma sessão ou permissão anterior."""

    def setUp(self):
        """Cria o callback a partir de uma tela autorizada e zera contadores."""
        self.scenario = Scenario(size=10)
        self.service = self.scenario.service_factory({})
        self.context = self.service.context()
        self.scenario.reset_counts()

    def callback(self, kind="replenishment", params=None):
        """Prepara um download sem executar consultas ou gerar um CSV."""
        return self.service.deferred_export(kind, params, context=self.context)

    def test_criar_botao_nao_consulta_dados_nem_gera_csv(self):
        """O callback conserva somente parâmetros, sem trabalho antecipado."""
        with patch.object(self.service, "export_replenishment") as export:
            callback = self.callback()
            self.assertTrue(callable(callback))
            export.assert_not_called()
        self.assertFalse(self.scenario.calls)

    def test_cada_clique_reconsulta_dados_e_auth_no_inicio_e_fim(self):
        """Cliques repetidos não reutilizam bytes nem autorização da geração anterior."""
        callback = self.callback()
        first = callback()
        second = callback()
        self.assertEqual(first, second)
        self.assertGreater(len(first), 0)
        self.assertEqual(self.scenario.calls["rpc_load_inventory"], 2)
        self.assertEqual(self.scenario.calls["auth_user"], 4)

    def test_logout_invalida_callback_antes_de_consultar_dados(self):
        """Um link de download antigo não funciona depois de encerrar a sessão."""
        callback = self.callback()
        self.service.auth.logout()
        with self.assertRaises(AuthenticationError):
            callback()
        self.assertEqual(self.scenario.calls["rpc_load_inventory"], 0)

    def test_novo_login_mesmo_usuario_nao_reativa_callback_antigo(self):
        """A geração impede reutilizar o botão de uma vida anterior da sessão."""
        callback = self.callback()
        self.service.auth.login("ficticio@example.test", "senha_ficticia")
        with self.assertRaises(AuthenticationError):
            callback()
        self.assertEqual(self.scenario.calls["rpc_load_inventory"], 0)

    def test_organizacao_trocada_invalida_callback(self):
        """O vínculo de organização impede entregar o arquivo com outro escopo."""
        callback = self.callback()
        self.scenario.organization_id = "00000000-0000-0000-0000-000000000999"
        with self.assertRaises(AuthorizationError):
            callback()
        self.assertEqual(self.scenario.calls["rpc_load_inventory"], 0)

    def test_financeiro_revogado_antes_do_clique_invalida_callback(self):
        """Mudança de acesso financeiro exige reconstruir a tela com acesso atual."""
        callback = self.callback()
        self.scenario.permissions.discard("finance.view")
        with self.assertRaises(AuthorizationError):
            callback()
        self.assertEqual(self.scenario.calls["rpc_load_inventory"], 0)

    def test_exportacao_revogada_antes_do_clique_e_recusada(self):
        """Permissão de visualizar não concede exportação após revogação."""
        callback = self.callback()
        self.scenario.permissions.discard("replenishment.export")
        with self.assertRaises(AuthorizationError):
            callback()
        self.assertEqual(self.scenario.calls["rpc_load_inventory"], 0)

    def test_financeiro_revogado_na_serializacao_nao_retorna_bytes(self):
        """Nem um CSV já gerado é devolvido se os limites mudaram durante o cálculo."""
        callback = self.callback()

        def generate(_):
            """Simula alteração financeira simultânea à geração fictícia."""
            self.scenario.permissions.discard("finance.view")
            return b"conteudo_ficticio_nao_pode_sair"

        with patch.object(self.service, "export_replenishment", side_effect=generate):
            with self.assertRaises(AuthorizationError):
                callback()
        self.assertEqual(self.scenario.calls["auth_user"], 2)

    def test_conta_desativada_na_serializacao_nao_retorna_bytes(self):
        """Auth é consultado novamente antes de entregar o resultado calculado."""
        callback = self.callback()

        def generate(_):
            """Simula bloqueio da conta enquanto um CSV é produzido."""
            self.scenario.active = False
            return b"conteudo_ficticio_nao_pode_sair"

        with patch.object(self.service, "export_replenishment", side_effect=generate):
            with self.assertRaises(AuthorizationError):
                callback()
        self.assertIsNone(self.service.auth.session)

    def test_parametros_sao_copiados_sem_reter_tabela(self):
        """Mudar filtros da UI depois de criar o callback não altera seu escopo."""
        filters = {"fornecedor": ["Fornecedor fictício 1"]}
        callback = self.callback(params=filters)
        filters["fornecedor"].append("Fornecedor fictício 2")
        with patch.object(self.service, "export_replenishment", return_value=b"CSV") as export:
            callback()
        export.assert_called_once_with({"fornecedor": ["Fornecedor fictício 1"]})

    def test_relatorio_tambem_e_gerado_somente_no_clique(self):
        """O mesmo mecanismo protege o relatório mensal e revalida o acesso."""
        callback = self.callback("monthly", {"month": "2026-09-01"})
        with patch.object(self.service, "export_monthly_report", return_value=b"CSV") as export:
            export.assert_not_called()
            self.assertEqual(callback(), b"CSV")
            export.assert_called_once_with("2026-09-01")
        self.assertEqual(self.scenario.calls["auth_user"], 2)

    def test_csv_respeita_prioridade_aplicada_na_tela(self):
        """Ruptura, risco e qualidade selecionam as mesmas linhas da tabela visível."""
        painel = self.service.load_inventory("replenishment.view")["panel"]
        expected = {
            "ruptura": painel.loc[painel["status"].eq("Ruptura"), "codigo"],
            "risco": painel.loc[painel["risco_prazo"], "codigo"],
            "qualidade": painel.loc[painel["requer_revisao"], "codigo"],
        }
        for priority, codes in expected.items():
            with self.subTest(priority=priority):
                content = self.callback(params={"prioridade": priority, "somente_sugestao": False})()
                rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig")), delimiter=";"))
                self.assertEqual({row["codigo"] for row in rows}, set(codes))

    def test_tabela_vazia_nao_pula_revalidacao_final(self):
        """Nem uma saída vazia pode ignorar a revogação de permissão de exportar."""
        painel = self.service.load_inventory("replenishment.view")["panel"].iloc[:0]

        def load(_):
            """Revoga o acesso entre a consulta e a produção do CSV vazio."""
            self.scenario.permissions.discard("replenishment.export")
            return {"panel": painel}

        with patch.object(self.service, "load_inventory", side_effect=load):
            with self.assertRaises(AuthorizationError):
                self.service.export_replenishment()

    def test_tipo_e_parametros_desconhecidos_sao_rejeitados(self):
        """O callback não permite escolher funções ou filtros arbitrários."""
        for kind, params in (("admin", {}), ("monthly", {}), ("replenishment", {"p_backend_secret": "x"})):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.callback(kind, params)
        self.assertFalse(self.scenario.calls)

    def test_usuario_sem_exportacao_nao_prepara_download(self):
        """A própria preparação do callback exige view e export, sem confiar no botão."""
        context = replace(self.context, permissions=frozenset({"replenishment.view"}))
        with self.assertRaises(AuthorizationError):
            self.service.deferred_export("replenishment", context=context)


class OpenerIsolationTests(unittest.TestCase):
    """Reuso de configuração não compartilha identidade, cookies ou respostas."""

    def test_opener_e_exclusivo_por_cliente_e_headers_sao_novos(self):
        """Cada API reutiliza apenas seus handlers e envia o JWT da chamada atual."""
        openers, calls = [], []

        class Response:
            """Representa uma resposta vazia sem qualquer conexão externa."""
            status = 200

            def __enter__(self):
                """Retorna o recurso fictício dentro do contexto HTTP."""
                return self

            def __exit__(self, *args):
                """Encerra a resposta fictícia."""
                return False

            def read(self, limit):
                """Retorna JSON fixo, respeitando o contrato do leitor."""
                return b"{}"

        class Opener:
            """Captura apenas headers fictícios para verificar ausência de herança."""

            def open(self, request, timeout):
                """Registra a chamada sem criar sockets."""
                calls.append(dict(request.header_items()))
                return Response()

        def build(handler):
            """Cria handlers distintos mantendo bloqueio de redirecionamento."""
            self.assertIsInstance(handler, _NoRedirect)
            opener = Opener()
            openers.append(opener)
            return opener

        config = Settings(PROJECT_URL, "sb_publishable_ficticio")
        first, second = SupabaseAPI(config), SupabaseAPI(config)
        with patch("urllib.request.build_opener", side_effect=build):
            first.rpc("app_context", {}, "token_primeiro")
            first.rpc("app_context", {}, "token_renovado")
            first.auth("POST", "recover", {"email": "ficticio@example.test"})
            second.rpc("app_context", {}, "token_segundo")
        self.assertEqual(len(openers), 2)
        self.assertEqual([call.get("Authorization") for call in calls],
                         ["Bearer token_primeiro", "Bearer token_renovado", None, "Bearer token_segundo"])
        self.assertNotIn("token_", repr(first._transport))

    def test_transportes_nao_possuem_estado_global(self):
        """A estrutura não inicia nem compartilha opener na construção de clientes."""
        first, second = _HTTPTransport(), _HTTPTransport()
        self.assertIsNone(first._opener)
        self.assertIsNone(second._opener)
        self.assertIsNot(first._lock, second._lock)


if __name__ == "__main__":
    unittest.main()
