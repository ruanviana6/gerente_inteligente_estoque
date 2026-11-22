"""Testes de escopo e abertura dos snapshots reais sem chamadas ao ERP."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_observational_review import payload

from integracoes.gestaoclick import ContratoInvalido
from web.errors import AuthorizationError
from web.real_home_status import consultar_status_real, selecionar_fonte_real

_ID = "10000000-0000-4000-8000-000000000001"
_FONTE = {"id": _ID, "name": "GestãoClick: loja 416023",
          "stores": ["416023"], "active": True}
_DEMO = {"id": "10000000-0000-4000-8000-000000000002",
         "name": "DEMO: vendas fictícias", "stores": ["DEMO"], "active": True}


class AuthFake:
    def __init__(self, *, role="Master", invalidar=False, resposta=None):
        self.context = SimpleNamespace(
            profile=SimpleNamespace(
                user_id="user1", organization_id="org1", role=role
            ),
            permissions=frozenset({"integrations.manage", "trends.view"}),
        )
        self.generation = 1
        self.invalidar = invalidar
        self.calls = []
        self.response = resposta if resposta is not None else payload()
        self.response["store_id"] = "416023"

    @contextmanager
    def operation(self):
        yield

    def require(self, permission):
        assert permission in self.context.permissions
        return self.context

    def rpc(self, name, args, permission):
        self.calls.append((name, deepcopy(args), permission))
        if name == "integration_sources":
            return [deepcopy(_DEMO), deepcopy(_FONTE)]
        if name == "demand_observation_review":
            assert args["p_store_id"] == "416023"
            if self.invalidar:
                self.generation += 1
            return deepcopy(self.response)
        raise AssertionError("RPC não autorizada.")


def test_fonte_real_nao_usa_demo_ou_duas_lojas():
    assert selecionar_fonte_real([_DEMO, _FONTE]) == (_ID, "416023")
    assert selecionar_fonte_real([_DEMO]) is None
    assert selecionar_fonte_real([_FONTE, deepcopy(_FONTE)]) is None
    assert selecionar_fonte_real([{**_FONTE, "stores": ["416023", "424409"]}]) is None


def test_dashboard_usa_apenas_duas_rpcs_autorizadas_e_foto_salva():
    auth = AuthFake()
    result = consultar_status_real(auth)
    assert result.store_id == "416023"
    assert result.closed_documents == 3
    assert [name for name, _, _ in auth.calls] == [
        "integration_sources", "demand_observation_review",
    ]
    assert all(permission in {"trends.view", "integrations.manage"}
               for _, _, permission in auth.calls)


def test_nao_entrega_resultado_apos_revogacao():
    with pytest.raises(AuthorizationError):
        consultar_status_real(AuthFake(invalidar=True))


def test_nao_entrega_resultado_de_loja_errada():
    item = payload()
    item["store_id"] = "424409"
    with pytest.raises(ContratoInvalido):
        consultar_status_real(AuthFake(resposta=item))


def test_nao_entrega_ao_nao_master():
    auth = AuthFake(role="Operador")
    with pytest.raises(AuthorizationError):
        consultar_status_real(auth)
    assert not auth.calls


@pytest.mark.parametrize("bad", [
    None, {}, "texto", [None], [{"id": _ID, "active": True, "stores": ["416023"]}],
])
def test_fonte_malformada_e_bloqueada(bad):
    with pytest.raises(ContratoInvalido):
        selecionar_fonte_real(bad)
