"""Validação e autorização da sincronização, sempre com origem fictícia."""
from contextlib import nullcontext
from copy import deepcopy
from uuid import UUID

import pytest

from sincronizacao.service import MAX_ROWS, SyncCoordinator, reconcile
from web.auth import AccessContext, Profile
from web.errors import AuthorizationError, ServiceUnavailable

SOURCE = '30000000-0000-4000-8000-000000000001'
REQUEST = '40000000-0000-4000-8000-000000000001'
RUN = '50000000-0000-4000-8000-000000000001'
TOKEN = '60000000-0000-4000-8000-000000000001'


def line(**values):
    """Monta linha canônica sem clientes, credenciais ou valores financeiros."""
    return {'source_id': SOURCE, 'store_id': 'loja1', 'document_id': '0001', 'line_id': '001',
            'product_id': '001', 'sku': '001', 'product_name': 'Produto fictício',
            'category': 'Categoria fictícia', 'supplier': 'Fornecedor fictício',
            'sale_date': '2020-01-10', 'status': 'completed', 'quantity': 5,
            'returned_quantity': 1, 'source_updated_at': '2020-01-11T00:00:00Z', **values}


class FakeAuth:
    """Simula só a fronteira RPC; integridade real fica nos testes PostgreSQL."""

    def __init__(self):
        """Inicia uma sessão Master isolada e sem tokens."""
        self.context = AccessContext(Profile('user1', 'org1', 'Master fictício', 'Master', True),
                                     frozenset({'integrations.manage'}))
        self.calls = []
        self.generation = 0
        self.fail_stage = False
        self.after = None

    def operation(self):
        """Permite usar a mesma interface do lock por sessão."""
        return nullcontext()

    def require(self, permission):
        """Nega por padrão, sem inferir permissão do nome do papel."""
        if not self.context.can(permission):
            raise AuthorizationError('Acesso negado.')
        return self.context

    def rpc(self, name, payload, permission):
        """Captura chamadas para comprovar ordem e geração sob demanda."""
        self.require(permission)
        self.calls.append((name, deepcopy(payload)))
        if self.fail_stage and name == 'sync_stage':
            raise ServiceUnavailable('Origem temporariamente indisponível.')
        if self.after:
            self.after(name)
        if name == 'sync_begin':
            return {'id': RUN, 'claim_token': TOKEN, 'status': 'staging', 'checkpoint': 0}
        return {'id': RUN, 'claim_token': TOKEN, 'status': name.removeprefix('sync_')}


def prepare(auth, rows=None):
    """Executa a primeira fase, sem confirmação automática."""
    return SyncCoordinator(auth).prepare(source_id=SOURCE, store_id='loja1', request_key=REQUEST,
        start='2020-01-01', end='2020-01-31', read_all=lambda: [line()] if rows is None else rows)


def test_preparar_nao_confirma_automaticamente():
    """Prévia persiste staging e nenhuma chamada de commit é enviada."""
    auth = FakeAuth()
    prepare(auth)
    assert [name for name, _ in auth.calls] == ['sync_authorize_source', 'sync_begin', 'sync_stage', 'sync_preview']
    assert auth.calls[1][1]['p_expected_quantity'] == '4'
    assert auth.calls[2][1]['p_rows'][0]['sku'] == '001'


def test_paginacao_staging_e_reexecucao():
    """A mesma chave e páginas reproduzíveis permitem retomar sem pular dados."""
    auth = FakeAuth()
    rows = [line(line_id=str(i)) for i in range(201)]
    prepare(auth, rows)
    stages = [payload for name, payload in auth.calls if name == 'sync_stage']
    assert [len(p['p_rows']) for p in stages] == [100, 100, 1]
    assert [p['p_page'] for p in stages] == [1, 2, 3]
    auth.calls.clear()
    prepare(auth, rows)
    assert [payload for name, payload in auth.calls if name == 'sync_stage'] == stages


def test_falha_interrompida_nao_confirma():
    """Falha ao gravar uma página impede prévia e promoção ao analítico."""
    auth = FakeAuth()
    auth.fail_stage = True
    with pytest.raises(ServiceUnavailable):
        prepare(auth)
    assert [name for name, _ in auth.calls] == ['sync_authorize_source', 'sync_begin', 'sync_stage']


@pytest.mark.parametrize('role', ['Administrador', 'Operador', 'Visualizador'])
def test_permissao_isolada_nao_substitui_master(role):
    """Mesmo uma concessão indevida no contexto não libera sincronização."""
    auth = FakeAuth()
    auth.context = AccessContext(Profile('user1', 'org1', 'Fictício', role, True),
                                 frozenset({'integrations.manage'}))
    with pytest.raises(AuthorizationError):
        prepare(auth)
    assert auth.calls == []


@pytest.mark.parametrize('which', ['user_id', 'organization_id', 'permission', 'active'])
def test_contexto_alterado_durante_rpc_nao_entrega(which):
    """Troca de conta/organização ou revogação interrompe a operação."""
    auth = FakeAuth()
    def after(name):
        """Altera o contexto somente após a primeira chamada ao backend."""
        if name != 'sync_begin':
            return
        values = {'user_id': 'user1', 'organization_id': 'org1', 'display_name': 'Fictício', 'role': 'Master', 'active': True}
        permissions = frozenset({'integrations.manage'})
        if which in ('user_id', 'organization_id'):
            values[which] = 'outra-conta'
        elif which == 'permission':
            permissions = frozenset()
        else:
            values['active'] = False
        auth.context = AccessContext(Profile(**values), permissions)
    auth.after = after
    with pytest.raises(AuthorizationError):
        prepare(auth)
    assert len(auth.calls) == 2


@pytest.mark.parametrize('override', [
    {'source_id': 'outra'}, {'store_id': 'outra'}, {'customer_name': 'PII proibida'},
    {'revenue': 10}, {'quantity': True}, {'quantity': float('inf')},
    {'quantity': -1}, {'returned_quantity': 6}, {'returned_quantity': None},
    {'sale_date': '2020-02-01'}, {'sale_date': '10/01/2020'}, {'status': 'fechada'},
    {'line_id': 1}, {'quantity': 1e-7},
])
def test_linha_invalida_nao_chega_ao_banco(override):
    """A fronteira rejeita dados inesperados sem iniciar lote ou emitir GET."""
    auth = FakeAuth()
    with pytest.raises((ValueError, TypeError)):
        prepare(auth, [line(**override)])
    assert [name for name, _ in auth.calls] == ['sync_authorize_source']


def test_documentos_duplicados_recusados():
    """Não soma duas vezes o mesmo documento/linha na extração."""
    with pytest.raises(ValueError, match='repetidos'):
        prepare(FakeAuth(), [line(), line()])


def test_cestas_status_nao_realizam_baixa():
    """Somente concretizadas líquidas entram na reconciliação; saldo não existe no contrato."""
    rows = [line(line_id=str(i), status=status) for i, status in enumerate(
        ['potential', 'separating', 'completed', 'cancelled', 'returned', 'unknown'])]
    result, net = reconcile(rows, SOURCE, 'loja1', '2020-01-01', '2020-01-31')
    assert net == 4
    assert len(result) == 6
    assert all('stock' not in item for item in result)


def test_limite_precede_gravacao():
    """Volume maior que o limite não cria uma execução parcial."""
    auth = FakeAuth()
    with pytest.raises(ValueError, match='vinte mil'):
        prepare(auth, (line(line_id=str(i)) for i in range(MAX_ROWS + 1)))
    assert [name for name, _ in auth.calls] == ['sync_authorize_source']


def test_confirmar_e_cancelar_sao_acoes_explicitas():
    """Confirmação e cancelamento exigem identificadores e autorização novos."""
    auth = FakeAuth()
    coordinator = SyncCoordinator(auth)
    coordinator.commit(RUN, TOKEN, coverage_confirmed=True)
    coordinator.cancel(RUN, TOKEN)
    assert [name for name, _ in auth.calls] == ['sync_commit', 'sync_cancel']
    assert auth.calls[0][1]['p_coverage_confirmed'] is True
    assert UUID(auth.calls[1][1]['p_run_id']) == UUID(RUN)
    with pytest.raises(ValueError):
        coordinator.commit(RUN, TOKEN, coverage_confirmed='true')


def test_novo_login_mesma_identidade_invalida_extracao():
    """Um novo login não reutiliza uma operação iniciada pela sessão anterior."""
    auth = FakeAuth()
    def read_all():
        """Simula substituição da sessão por outra vida da mesma identidade."""
        auth.generation += 1
        return [line()]
    with pytest.raises(AuthorizationError):
        SyncCoordinator(auth).prepare(source_id=SOURCE, store_id='loja1', request_key=REQUEST,
            start='2020-01-01', end='2020-01-31', read_all=read_all)
    assert [name for name, _ in auth.calls] == ['sync_authorize_source']
