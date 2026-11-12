"""Regras fiscais, autorização da fachada e fluxo real de widgets com dados DEMO."""

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from fiscal.adaptadores import IntegracaoRealBloqueada, mapear_para_api_real
from fiscal.regras import CONTRATO, EscolhasFiscais, elegibilidade, normalizar_uf
from web.auth import AccessContext, Profile
from web.errors import AuthorizationError
from web.fiscal_service import FiscalService


@pytest.mark.parametrize('frete', [0, 1, 2, 3, 4, 9])
@pytest.mark.parametrize('contribuinte', [1, 2, 9])
def test_escolhas_preservam_codigos_sem_inferencia(frete, contribuinte):
    """Os dezoito pares possíveis permanecem estruturados e distintos."""
    assert EscolhasFiscais(frete, contribuinte).registro() == {
        'modalidade_frete': frete, 'tipo_contribuinte': contribuinte}


@pytest.mark.parametrize('valor', [None, True, False, '1', 1.0, -1, 5, float('nan')])
def test_escolha_invalida_nao_tem_default(valor):
    """Ausências, booleanos e números aproximados nunca se tornam FOB."""
    with pytest.raises(ValueError):
        EscolhasFiscais(valor, 9)
    with pytest.raises(ValueError):
        EscolhasFiscais(1, valor)


@pytest.mark.parametrize('uf', ['CE', 'ce', ' Ceará ', 'Ceara', 'Ceara\u0301'])
def test_normalizacao_ceara(uf):
    assert normalizar_uf(uf) == 'CE'


@pytest.mark.parametrize('numero', ['1004', '1005', '1006', '1009', '1010'])
def test_pedidos_bloqueados(numero):
    assert not elegibilidade(CONTRATO['pedidos'][numero])[0]


def test_andamento_exige_autorizacao_e_adaptador_real_bloqueia():
    assert not elegibilidade(CONTRATO['pedidos']['1003'])[0]
    assert elegibilidade(CONTRATO['pedidos']['1003'], True)[0]
    with pytest.raises(IntegracaoRealBloqueada):
        mapear_para_api_real({}, EscolhasFiscais(1, 9))


class AuthFiscal:
    """Autorização inspecionável para testar a fachada, sem endpoints reais."""

    def __init__(self, permissions=None):
        self.permissions = frozenset(permissions or {'fiscal.view', 'fiscal.create_request'})
        self.profile = Profile(str(uuid4()), str(uuid4()), 'Pessoa DEMO', 'Operador', True)
        self.calls = []
        self.after = None
        self.session = SimpleNamespace(access_token='token-exclusivamente-ficticio')
        self.api = SimpleNamespace(fiscal_demo=self.execute)

    def operation(self):
        return nullcontext()

    def require(self, permission):
        if permission not in self.permissions:
            raise AuthorizationError('Acesso negado no cenário.')
        return AccessContext(self.profile, self.permissions)

    def rpc(self, name, payload, permission):
        self.calls.append((name, payload, permission))
        if self.after:
            self.after()
        return {'id': str(uuid4()), 'state': 'confirmed'}

    def execute(self, *args):
        self.calls.append(('executor', args))
        if self.after:
            self.after()
        return {'state': 'authorized', 'real_enabled': False}


def test_fachada_envia_apenas_campos_permitidos():
    auth = AuthFiscal()
    service = FiscalService(auth)
    service.preparar('1001', 1, 2)
    assert auth.calls[-1][1] == {'p_number': '1001', 'p_choices': {'modalidade_frete': 1, 'tipo_contribuinte': 2}}
    service.confirmar(str(uuid4()), True)
    assert set(auth.calls[-1][1]) == {'p_preview', 'p_confirm', 'p_approve_pending'}


@pytest.mark.parametrize('action', ['rpc', 'executor'])
@pytest.mark.parametrize('change', ['organization', 'permission', 'view'])
def test_revogacao_concorrente_nao_entrega_resultado(action, change):
    auth = AuthFiscal()
    def mudar():
        """Simula alteração externa depois da resposta e antes da entrega."""
        if change == 'organization':
            auth.profile = replace(auth.profile, organization_id=str(uuid4()))
        else:
            auth.permissions -= {'fiscal.view' if change == 'view' else 'fiscal.create_request'}
    auth.after = mudar
    with pytest.raises(AuthorizationError):
        if action == 'rpc':
            FiscalService(auth).preparar('1001', 1, 9)
        else:
            FiscalService(auth).executar(str(uuid4()))


@pytest.mark.parametrize('numero', ['', '../1001', '1001;drop', '<script>', '1'*21, 1001])
def test_numero_invalido_recusado_antes_da_rede(numero):
    auth = AuthFiscal()
    with pytest.raises(ValueError):
        FiscalService(auth).consultar(numero)
    assert not auth.calls


def test_sem_view_nao_pode_criar_nem_executar():
    auth = AuthFiscal({'fiscal.create_request'})
    for action in (lambda: FiscalService(auth).preparar('1001', 1, 9),
                   lambda: FiscalService(auth).executar(str(uuid4()))):
        with pytest.raises(AuthorizationError):
            action()
    assert not auth.calls


class GatewayFiscalDemo:
    """Simulador exclusivo da UI; segurança SQL tem suíte independente no CI."""

    def __init__(self, auth):
        self.auth = auth
        self.previews = {}
        self.requests = []
        self.executions = 0

    def estado(self):
        return {'demo_enabled': True}

    def consultar(self, numero):
        order = deepcopy(CONTRATO['pedidos'][numero])
        order['solicitacao_existente'] = next((r for r in self.requests if r['order_number'] == numero), None)
        return order

    def preparar(self, numero, frete, contribuinte):
        choices = EscolhasFiscais(frete, contribuinte)
        value = {'id': str(uuid4()), 'version': 1, 'choices': choices.registro(), 'order': self.consultar(numero)}
        self.previews[value['id']] = value
        return value

    def previa(self, identificador):
        return deepcopy(self.previews[identificador])

    def confirmar(self, identificador, confirmado, aprovar):
        assert confirmado is True
        preview = self.previews[identificador]
        request = {'id': str(uuid4()), 'order_number': preview['order']['numero'], 'state': 'confirmed',
                   'freight': preview['choices']['modalidade_frete'], 'taxpayer': preview['choices']['tipo_contribuinte'],
                   'created_at': '2026-11-11T17:00:00Z'}
        self.requests.append(request)
        return deepcopy(request)

    def executar(self, identificador, reconciliar=False):
        self.executions += 1
        request = next(r for r in self.requests if r['id'] == identificador)
        request['state'] = 'authorized'
        return deepcopy(request)

    def listar(self, pagina=0):
        return deepcopy(self.requests)


def abrir_fiscal(monkeypatch):
    """Executa widgets Streamlit verdadeiros, sem servidor público nem bypass produtivo."""
    import web.fiscal_page
    gateway = GatewayFiscalDemo(AuthFiscal())
    monkeypatch.setattr(web.fiscal_page, 'FiscalService', lambda auth: gateway)
    script = "from web.fiscal_page import fiscal\nfrom test_fiscal import AuthFiscal\nfrom types import SimpleNamespace\na=AuthFiscal()\nfiscal(SimpleNamespace(auth=a),a.require('fiscal.view'))\n"
    app = AppTest.from_string(script, default_timeout=30).run()
    assert not app.exception
    return app, gateway


def clicar(app, label):
    next(b for b in app.button if b.label == label).click().run()
    assert not app.exception


def test_interface_nao_emite_ao_abrir_e_exige_duas_escolhas(monkeypatch):
    app, gateway = abrir_fiscal(monkeypatch)
    assert gateway.executions == 0 and not app.selectbox
    app.text_input[0].set_value('1001')
    clicar(app, 'Consultar pedido DEMO')
    assert all(s.value is None for s in app.selectbox)
    assert not gateway.previews and not gateway.requests
    app.selectbox[0].set_value(1).run()
    assert app.selectbox[1].value is None and not gateway.requests
    app.selectbox[1].set_value(2).run()
    clicar(app, 'Gerar prévia DEMO')
    assert next(b for b in app.button if b.label == 'Confirmar solicitação DEMO').disabled
    next(c for c in app.checkbox if c.key == 'fiscal_confirmacao').check().run()
    clicar(app, 'Confirmar solicitação DEMO')
    assert gateway.executions == 1 and gateway.requests[0]['taxpayer'] == 2
    app.run()
    assert gateway.executions == 1
    assert all(s.value is None for s in app.selectbox)
    assert not app.get('download_button')


def test_editar_escolha_invalida_previa(monkeypatch):
    app, gateway = abrir_fiscal(monkeypatch)
    app.text_input[0].set_value('1001')
    clicar(app, 'Consultar pedido DEMO')
    app.selectbox[0].set_value(1).run()
    app.selectbox[1].set_value(2).run()
    clicar(app, 'Gerar prévia DEMO')
    assert any(b.label == 'Confirmar solicitação DEMO' for b in app.button)
    app.selectbox[1].set_value(9).run()
    assert all(b.label != 'Confirmar solicitação DEMO' for b in app.button)
    assert not gateway.requests


def test_contrato_sql_mesmo_conteudo_sem_campos_reais():
    """A semente SQL e a referência Python precisam representar o mesmo catálogo."""
    import json
    sql = (Path(__file__).parents[1] / 'supabase/migrations/20261111170000_fiscal_demo.sql').read_text()
    embedded = sql.split("as $catalogo$ select '", 1)[1].split("'::jsonb $catalogo$", 1)[0]
    assert json.loads(embedded) == CONTRATO
    assert all(p['ambiente'] == 'demo' for p in CONTRATO['pedidos'].values())
