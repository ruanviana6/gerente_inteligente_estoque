"""Diagnóstico autorizado de NF-e por GET. Nunca cadastra, emite ou persiste nota."""

from __future__ import annotations

import hmac
import re
from typing import Any
from urllib.parse import urlencode

from fiscal.lojas import _nome_normalizado, validar_lojas_fiscais
from integracoes.account_setup import (
    ContaGestaoClick,
    _configuracao,
    _vinculo_configuracao,
    validar_conta,
)
from integracoes.diagnostico import _stamp
from integracoes.gestaoclick import (
    ContratoInvalido,
    GestaoClickError,
    TransporteConsulta,
    TransporteHTTPS,
    parse_json_seguro,
)
from integracoes.native_preview import _id_produto
from sincronizacao.service import RPCAuth
from web.errors import AuthorizationError


def _consulta_fiscal_get(
    auth: RPCAuth,
    carregar_configuracao,
    conta: ContaGestaoClick,
    loja_id: str,
    caminho: str,
    *,
    autorizado: bool,
    conferencia_valores: bool = False,
    transporte: TransporteConsulta | None = None,
) -> dict[str, Any]:
    """Revalida acesso e configuração antes e depois do GET fiscal fixo."""
    with auth.operation():
        stamp = _stamp(auth)
        if autorizado is not True:
            raise AuthorizationError("Confirme explicitamente a consulta fiscal somente leitura.")
        if conferencia_valores:
            auth.require("fiscal.view")
            auth.require("finance.view")
        validar_conta(auth, conta)
        if (not isinstance(loja_id, str) or loja_id not in {loja["id"] for loja in conta.lojas}):
            raise AuthorizationError("A loja fiscal não pertence ao catálogo autorizado.")
        config = _configuracao(auth, carregar_configuracao, stamp, True)
        if not hmac.compare_digest(conta._config_stamp, _vinculo_configuracao(config)):
            raise AuthorizationError("A configuração de acesso mudou. Consulte as lojas novamente.")
        base = config.validar_conexao()

        def revalidar() -> None:
            if conferencia_valores:
                auth.require("fiscal.view")
                auth.require("finance.view")
            validar_conta(auth, conta)
            atual = _configuracao(auth, carregar_configuracao, stamp, True)
            if atual != config:
                raise AuthorizationError("A configuração fiscal mudou durante a consulta.")

        url = base + caminho
        revalidar()
        try:
            resposta = (transporte or TransporteHTTPS()).get(url, {
                "access-token": config.access_token,
                "secret-access-token": config.secret_access_token,
                "Accept": "application/json",
            }, min(config.timeout_seconds, 15))
        except Exception:
            revalidar()
            raise GestaoClickError("Consulta fiscal indisponível. Nenhuma nota foi alterada.") from None
        revalidar()
        status = resposta.status
        if type(status) is not int or not 100 <= status <= 599:
            raise GestaoClickError("O GestãoClick retornou um status HTTP inválido.")
        if status != 200:
            orientacoes = {
                401: "Confira as credenciais de leitura.",
                402: "Confira a disponibilidade da API no plano atual.",
                403: "Confira as permissões da chave para o recurso e a loja.",
                404: "O recurso não foi localizado com os dados e o acesso utilizados. Confira a loja e o identificador.",
                429: "Limite de requisições atingido. Aguarde antes de consultar novamente.",
            }
            orientacao = orientacoes.get(status, "Consulte novamente mais tarde." if status >= 500
                                         else "Confira os parâmetros da consulta.")
            raise GestaoClickError(f"Consulta GestãoClick retornou HTTP {status}. {orientacao}")
        payload = parse_json_seguro(resposta.body)
        if (type(payload.get("code")) is not int or payload["code"] != 200
                or payload.get("status") != "success"):
            raise ContratoInvalido("Formato do retorno de NF-e divergente; leitura não comprovada.")
        return payload


def consultar_recurso_nfe(
    auth: RPCAuth, carregar_configuracao, conta: ContaGestaoClick, loja_id: str,
    *, autorizado: bool, transporte: TransporteConsulta | None = None,
) -> dict[str, Any]:
    """Verifica uma página mínima sem expor dados ou afirmar cobertura."""
    caminho = "/notas_fiscais_produtos?" + urlencode({"loja_id": loja_id, "pagina": 1, "limite": 1})
    payload = _consulta_fiscal_get(auth, carregar_configuracao, conta, loja_id, caminho,
                             autorizado=autorizado, transporte=transporte)
    rows = payload.get("data")
    if not isinstance(rows, list) or len(rows) > 1 or any(not isinstance(row, dict) for row in rows):
        raise ContratoInvalido("Formato do retorno de NF-e divergente; leitura não comprovada.")
    # O token pode fixar a loja e ignorar ?loja_id=. Um registro que
    # declara explicitamente loja_id permite apenas comparar essa amostra.
    # Não inferir a loja pelo ID da nota, pelo retorno 200 ou por lista vazia.
    loja_registro_confere: bool | None = None
    if len(rows) == 1:
        valor_loja = rows[0].get("loja_id")
        if type(valor_loja) is int and valor_loja > 0:
            loja_registro_confere = hmac.compare_digest(str(valor_loja), loja_id)
        elif (isinstance(valor_loja, str) and valor_loja.isascii() and
                valor_loja.isdecimal() and valor_loja):
            loja_registro_confere = hmac.compare_digest(valor_loja, loja_id)
    # Não retorna nem guarda linha, chave, nota, destinatário, valor ou metadados brutos.
    return {
        "get_autorizado": True,
        "recurso": "notas_fiscais_produtos",
        "loja_solicitada": loja_id,
        "itens_amostra": len(rows),
        "loja_efetiva_comprovada": False,
        "loja_no_registro_confere": loja_registro_confere,
        "cobertura_comprovada": False,
        "escrita_habilitada": False,
        "resultado": "GET_RESPONDEU_SEM_HOMOLOGAR_CADASTRO",
    }

_STATUS_NFE = frozenset({"Em aberto", "Aprovada", "Reprovada", "Denegada",
                        "Cancelada", "Enviando", "Corrigida"})


def conferir_nfe_por_numero(
    auth: RPCAuth, carregar_configuracao, conta: ContaGestaoClick, loja_id: str, numero: str,
    *, autorizado: bool, transporte: TransporteConsulta | None = None,
) -> dict[str, str]:
    """Consulta uma saída modelo 55 por número na loja, recusando resultados ambíguos."""
    if not isinstance(numero, str) or not re.fullmatch(r"[0-9]{1,9}", numero) or int(numero) == 0:
        raise ContratoInvalido("Informe o número da NF-e com até nove algarismos.")
    numero = str(int(numero))
    filtros = urlencode({"numero": numero, "loja_id": loja_id, "tipo": 1, "pagina": 1, "limite": 2})
    payload = _consulta_fiscal_get(auth, carregar_configuracao, conta, loja_id,
                                  "/notas_fiscais_produtos?" + filtros, autorizado=autorizado, transporte=transporte)
    registros, meta = payload.get("data"), payload.get("meta")
    if not isinstance(registros, list) or not isinstance(meta, dict):
        raise ContratoInvalido("A API não confirmou os resultados da pesquisa por número.")
    total = meta.get("total_registros")
    if type(total) is int and total == 0 and not registros:
        raise ContratoInvalido("A consulta não encontrou esse número entre as notas de saída acessíveis na loja.")
    if (type(total) is not int or total != 1 or len(registros) != 1
            or meta.get("proxima_pagina") is not None or meta.get("total_paginas") != 1):
        raise ContratoInvalido("A consulta não confirmou uma única nota. Confira a série e consulte pelo ID interno.")
    nota = registros[0]
    if (not isinstance(nota, dict) or nota.get("loja_id") != loja_id
            or nota.get("tipo_nf") != "1" or nota.get("modelo") != "55"
            or not re.fullmatch(r"0*" + numero, str(nota.get("numero_nf")))
            or not isinstance(nota.get("id"), str) or not re.fullmatch(r"[1-9][0-9]{0,17}", nota["id"])
            or not isinstance(nota.get("situacao_nf"), str) or nota["situacao_nf"] not in _STATUS_NFE):
        raise ContratoInvalido("A resposta não confirmou a identidade e a situação da NF-e de saída nessa loja.")
    return {"id": nota["id"], "situacao": nota["situacao_nf"]}


def conferir_nfe_por_id(
    auth: RPCAuth, carregar_configuracao, conta: ContaGestaoClick,
    loja_id: str, nota_id: str, referencia: str | None = None,
    *, autorizado: bool, transporte: TransporteConsulta | None = None,
) -> dict[str, Any]:
    """Confere ID, loja, situação e referência de nota conhecida em um único GET.

    A API não faz busca por referência aqui. Nota não encontrada, fora de escopo
    ou de formato divergente não comprova que seja seguro repetir um cadastro.
    """
    if type(nota_id) is not str or not re.fullmatch(r"[1-9][0-9]{0,17}", nota_id):
        raise ContratoInvalido("Informe o ID interno numérico da nota.")
    if referencia is not None and (
        type(referencia) is not str
        or not re.fullmatch(r"Flatter origem [1-9][0-9]{0,17} venda [1-9][0-9]{0,17}", referencia)
    ):
        raise ContratoInvalido("A referência da venda de origem não está no formato do Gerente.")
    payload = _consulta_fiscal_get(
        auth, carregar_configuracao, conta, loja_id, f"/notas_fiscais_produtos/{nota_id}",
        autorizado=autorizado, transporte=transporte,
    )
    nota = payload.get("data")
    if not isinstance(nota, dict) or nota.get("id") != nota_id or nota.get("loja_id") != loja_id:
        raise ContratoInvalido("A resposta não confirma o ID e a loja esperados da nota.")
    situacao = nota.get("situacao_nf")
    if not isinstance(situacao, str) or situacao not in _STATUS_NFE:
        raise ContratoInvalido("A situação retornada pela API não pôde ser reconhecida.")
    return {"situacao": situacao, "em_aberto": situacao == "Em aberto",
            "referencia_confere": (nota.get("informacoes_complementares") == referencia)
            if referencia is not None else None}


def _valor_comercial(produto: dict, campo: str, linha: int, *, opcional: bool = False) -> str | None:
    """Preserva o decimal de origem e identifica divergências sem expor o pedido."""
    valor = produto.get(campo)
    if opcional and (valor is None or valor == ""):
        return None
    if type(valor) is int and 0 <= valor < 10**12:
        return str(valor)
    if isinstance(valor, str) and re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,8}0{0,10})?", valor):
        return valor
    motivo = "ausente ou vazio" if valor is None or valor == "" else "em formato incompatível"
    raise ContratoInvalido(f"Na linha {linha}, o campo {campo} está {motivo}. Confira o item no GestãoClick.")


def _itens_para_montagem(venda: dict) -> tuple[dict, ...]:
    """Mantém a ordem e os valores originais e exclui custo e dados pessoais."""
    produtos = venda.get("produtos")
    if not isinstance(produtos, list) or not 1 <= len(produtos) <= 100:
        raise ContratoInvalido("A quantidade de linhas retornada está fora do limite.")
    linhas = []
    for indice, linha in enumerate(produtos, start=1):
        produto = linha.get("produto") if isinstance(linha, dict) else None
        if not isinstance(produto, dict):
            raise ContratoInvalido("A venda retornou uma linha de produto inválida.")
        try:
            identificador = _id_produto(produto.get("produto_id"))
            variacao = _id_produto(produto.get("variacao_id"), variacao=True)
        except ContratoInvalido:
            raise ContratoInvalido(f"Identificador de produto ou variação inválido na linha {indice}.") from None
        nome = produto.get("nome_produto")
        unidade = produto.get("sigla_unidade")
        linhas.append({
            "Produto": nome if isinstance(nome, str) and len(nome) <= 200 and nome.isprintable() else None,
            "Unidade": unidade if isinstance(unidade, str) and len(unidade) <= 20 and unidade.isprintable() else None,
            "Produto no atacado (ID)": identificador,
            "Variação no atacado (ID)": variacao,
            "Quantidade na venda": _valor_comercial(produto, "quantidade", indice),
            "Valor de venda informado": _valor_comercial(produto, "valor_venda", indice),
            "Desconto em valor informado": _valor_comercial(produto, "desconto_valor", indice, opcional=True),
            "Desconto em percentual informado": _valor_comercial(
                produto, "desconto_porcentagem", indice, opcional=True,
            ),
            "Total da linha informado": _valor_comercial(produto, "valor_total", indice),
        })
    return tuple(linhas)


def consultar_itens_para_montagem(
    auth: RPCAuth, carregar_configuracao, conta: ContaGestaoClick,
    origem_id: str, destino_id: str, venda_id: str, numero: str, situacao_id: str,
    *, autorizado: bool, transporte: TransporteConsulta | None = None,
) -> tuple[dict, ...]:
    """Entrega somente as linhas comerciais da venda previamente localizada."""
    origem, _ = validar_lojas_fiscais(conta.lojas, origem_id, destino_id)
    if type(venda_id) is not str or not re.fullmatch(r"[1-9][0-9]{0,17}", venda_id):
        raise ContratoInvalido("O identificador interno da venda não pode ser consultado.")
    payload = _consulta_fiscal_get(
        auth, carregar_configuracao, conta, origem, f"/vendas/{venda_id}",
        autorizado=autorizado, conferencia_valores=True, transporte=transporte,
    )
    venda = payload.get("data")
    if (not isinstance(venda, dict) or venda.get("id") != venda_id or venda.get("codigo") != numero
            or venda.get("situacao_id") != situacao_id
            or _nome_normalizado(venda.get("nome_loja")) != "flt comercio"
            or ("loja_id" in venda and venda["loja_id"] != origem)):
        raise ContratoInvalido("O detalhe não confirma a venda e a origem observadas. Consulte novamente.")
    return _itens_para_montagem(venda)
