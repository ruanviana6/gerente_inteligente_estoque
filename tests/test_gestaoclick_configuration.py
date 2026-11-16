"""Valida diagnósticos e identidade do contrato sem ler ERP, banco ou Secrets reais."""

import json
from copy import deepcopy

import pytest
from test_gestaoclick_runtime import secrets

from integracoes.configuration import contract_fingerprint, missing_configuration, validated_scope
from integracoes.gestaoclick import ContratoInvalido

ORGANIZATION = "10000000-0000-4000-8000-000000000001"
SOURCE = "20000000-0000-4000-8000-000000000001"


def configuracao_ficticia():
    """Completa o contrato fictício com identificadores válidos, sem dados comerciais."""
    return {**secrets(contract="read_contract"), "GESTAOCLICK_ORGANIZATION_ID": ORGANIZATION,
            "GESTAOCLICK_SOURCE_ID": SOURCE}


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    """A preparação local nunca pode inicializar uma consulta de qualquer provedor."""
    def bloquear(*args, **kwargs):
        """Transforma uma tentativa de acesso externo em falha explícita de teste."""
        raise AssertionError("Configuração não deve acessar a rede.")
    monkeypatch.setattr("urllib.request.build_opener", bloquear)
    monkeypatch.setattr("integracoes.gestaoclick.GestaoClickClient.consultar", bloquear)


def test_resumo_canonico_independe_de_espacos_e_ordem_dos_objetos():
    """A mesma semântica aprovada não perde aprovação por formatação do JSON."""
    first = configuracao_ficticia()
    second = dict(reversed(list(first.items())))
    for key, value in first.items():
        if key.endswith("_JSON"):
            parsed = json.loads(value)
            second[key] = json.dumps(dict(reversed(list(parsed.items()))), indent=4)
    assert contract_fingerprint(first) == contract_fingerprint(second)
    assert len(contract_fingerprint(first)) == 64


def test_troca_de_tokens_nao_altera_resumo_e_nao_expoe_segredo():
    """A rotação de autenticação não muda o contrato de dados aprovado."""
    first = configuracao_ficticia()
    second = {**first, "GESTAOCLICK_ACCESS_TOKEN": "novo-token-ficticio",
              "GESTAOCLICK_SECRET_ACCESS_TOKEN": "novo-segredo-ficticio",
              "SUPABASE_BACKEND_SECRET": "nao-deve-entrar-no-resumo"}
    assert contract_fingerprint(first) == contract_fingerprint(second)
    assert "novo-token" not in contract_fingerprint(second)


@pytest.mark.parametrize(("name", "value"), [
    ("GESTAOCLICK_ORGANIZATION_ID", "10000000-0000-4000-8000-000000000002"),
    ("GESTAOCLICK_SOURCE_ID", "20000000-0000-4000-8000-000000000002"),
    ("GESTAOCLICK_STORE_ID", "outra-loja-ficticia"),
    ("GESTAOCLICK_STATUS_MAP_JSON", '{"concluida_ficticia":"cancelled"}'),
    ("GESTAOCLICK_FIXED_FILTERS_JSON", '{"tipo_ficticio":"produtos"}'),
])
def test_mudanca_de_escopo_ou_semantica_exige_nova_aprovacao(name, value):
    """Trocas de conta, loja, estados ou filtros nunca conservam a aprovação antiga."""
    first = configuracao_ficticia()
    assert contract_fingerprint(first) != contract_fingerprint({**first, name: value})


def test_mudanca_de_mapeamento_altera_identidade_do_contrato():
    """Usar outro campo de quantidade precisa ser conferido novamente pelo Master."""
    first = configuracao_ficticia()
    second = deepcopy(first)
    fields = json.loads(second["GESTAOCLICK_FIELD_MAP_JSON"])
    fields["line_fields"]["quantity"] = ["outra_quantidade_ficticia"]
    second["GESTAOCLICK_FIELD_MAP_JSON"] = json.dumps(fields)
    assert contract_fingerprint(first) != contract_fingerprint(second)


def test_filtro_opcional_vazio_equivale_a_ausencia():
    """A configuração opcional omitida e o objeto vazio não mudam a consulta."""
    values = configuracao_ficticia()
    assert contract_fingerprint(values) == contract_fingerprint({**values, "GESTAOCLICK_FIXED_FILTERS_JSON": "{}"})


@pytest.mark.parametrize("invalid_json", [
    '{"concluida_ficticia":"completed","concluida_ficticia":"cancelled"}',
    '{"concluida_ficticia":NaN}',
    "RESPOSTA-PROTEGIDA-JSON-INVALIDO",
])
def test_json_invalido_ou_ambiguo_nao_produz_aprovacao(invalid_json):
    """A falha apresenta diagnóstico fixo, nunca repete conteúdo de configuração."""
    values = {**configuracao_ficticia(), "GESTAOCLICK_STATUS_MAP_JSON": invalid_json}
    with pytest.raises(ContratoInvalido) as error:
        contract_fingerprint(values)
    assert invalid_json not in str(error.value)


def test_diagnostico_incompleto_retorna_apenas_nomes_de_configuracoes():
    """Tokens presentes não mascaram ausência de fonte, loja, filtros e contrato."""
    result = missing_configuration({"GESTAOCLICK_ACCESS_TOKEN": "NAO-EXIBIR-ACCESS",
                                    "GESTAOCLICK_SECRET_ACCESS_TOKEN": "NAO-EXIBIR-SECRET"})
    assert "GESTAOCLICK_SOURCE_ID" in result
    assert "GESTAOCLICK_STORE_ID" in result
    assert any("CONTRACT_JSON" in name for name in result)
    assert "NAO-EXIBIR" not in repr(result)
    assert missing_configuration(configuracao_ficticia()) == []


@pytest.mark.parametrize(("name", "value"), [
    ("GESTAOCLICK_ORGANIZATION_ID", "invalido"), ("GESTAOCLICK_ORGANIZATION_ID", None),
    ("GESTAOCLICK_SOURCE_ID", "invalido"), ("GESTAOCLICK_SOURCE_ID", []),
    ("GESTAOCLICK_STORE_ID", ""), ("GESTAOCLICK_STORE_ID", " \t"),
    ("GESTAOCLICK_STORE_ID", "loja\nforjada"), ("GESTAOCLICK_STORE_ID", 1),
])
def test_escopo_invalido_impede_uso_da_credencial(name, value):
    """Identificadores inválidos são recusados antes de chamar fonte ou banco."""
    with pytest.raises(ContratoInvalido):
        validated_scope({**configuracao_ficticia(), name: value})


def test_organizacao_ajustada_na_sessao_nao_reutiliza_secrets_de_outra():
    """O vínculo do servidor não é sobrescrito por uma escolha da interface."""
    values = configuracao_ficticia()
    assert validated_scope(values, ORGANIZATION) == (ORGANIZATION, SOURCE, "loja-ficticia")
    with pytest.raises(ContratoInvalido, match="não pertence"):
        validated_scope(values, "10000000-0000-4000-8000-000000000002")
