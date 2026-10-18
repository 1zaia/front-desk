"""Testes do agente sem chamar o Bedrock: um modelo falso pede as ferramentas e o servidor MCP real responde."""
import copy

import anyio
import pytest

from frontdesk import agente


@pytest.fixture(autouse=True)
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("FRONTDESK_DB", str(tmp_path / "agente.db"))
    monkeypatch.setenv("FRONTDESK_AGORA", "2026-10-19T08:00")
    monkeypatch.setenv("FRONTDESK_DEMO", "0")
    from frontdesk import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "agente.db")
    agente._sessoes.clear()


def _servidor():
    from frontdesk.server import mcp
    return mcp


class ModeloFalso:
    """Imita a Converse API: segue um roteiro de pedidos de ferramenta e depois responde com texto."""

    def __init__(self, roteiro):
        self.roteiro = list(roteiro)
        self.chamadas = []

    def __call__(self, **kwargs):
        self.chamadas.append(copy.deepcopy(kwargs))
        passo = self.roteiro.pop(0)
        if isinstance(passo, str):
            return {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [{"text": passo}]}}}
        conteudo = [{"toolUse": {"toolUseId": f"t{i}", "name": nome, "input": args}} for i, (nome, args) in enumerate(passo)]
        return {"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": conteudo}}}


def test_schema_sem_anyof_nulo():
    schema = {
        "type": "object", "title": "X",
        "properties": {
            "data": {"type": "string", "title": "Data"},
            "periodo": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "description": "Opcional"},
        },
        "required": ["data"],
    }
    s = agente._simplificar_schema(schema)
    assert s["properties"]["periodo"] == {"type": "string", "description": "Opcional"}
    assert "title" not in s and "title" not in s["properties"]["data"]
    assert s["required"] == ["data"]


def test_agendar_pela_conversa():
    modelo = ModeloFalso([
        [("consultar_horarios_livres", {"data": "sexta", "servico": "corte", "periodo": "manhã"})],
        "Para corte sexta de manhã tenho 9h, 9h30 ou 10h. Qual prefere?",
        [("agendar", {"cliente": "João", "servico": "corte", "data": "sexta", "horario": "9h"})],
        "Pronto, João! Marquei corte para sexta às 9h.",
    ])

    async def rodar():
        r1 = await agente.responder("tem horário pra corte sexta de manhã?", servidor=_servidor(), converse=modelo)
        r2 = await agente.responder("às 9h, meu nome é João", sessao_id=r1["sessao"], servidor=_servidor(), converse=modelo)
        return r1, r2

    r1, r2 = anyio.run(rodar)
    assert r1["chamadas"][0]["ferramenta"] == "consultar_horarios_livres"
    assert r1["chamadas"][0]["ok"] and "9h" in r1["chamadas"][0]["fala"]
    assert r2["sessao"] == r1["sessao"]
    assert r2["chamadas"][0]["ferramenta"] == "agendar" and r2["chamadas"][0]["ok"]
    assert r2["resposta"].startswith("Pronto, João")

    # As ferramentas foram entregues ao modelo no formato da Converse API
    specs = modelo.chamadas[0]["toolConfig"]["tools"]
    nomes = {s["toolSpec"]["name"] for s in specs}
    assert {"agendar", "consultar_horarios_livres", "resumo_do_dia"} <= nomes
    # O resultado da ferramenta voltou para o modelo como toolResult em JSON
    retorno = modelo.chamadas[1]["messages"][-1]["content"][0]["toolResult"]
    assert retorno["toolUseId"] == "t0" and retorno["content"][0]["json"]["ok"] is True
    # A segunda fala carregou o histórico da primeira
    assert len(modelo.chamadas[2]["messages"]) == 5


def test_ferramenta_com_erro_volta_para_o_modelo():
    modelo = ModeloFalso([
        [("agendar", {"cliente": "Ana", "servico": "barba", "data": "domingo", "horario": "10h"})],
        "Não abrimos no domingo. Quer outro dia?",
    ])
    r = anyio.run(lambda: agente.responder("barba domingo 10h, Ana", servidor=_servidor(), converse=modelo))
    assert not r["chamadas"][0]["ok"]
    assert "Não abrimos" in r["chamadas"][0]["fala"]
    assert r["resposta"].startswith("Não abrimos")


def test_fala_vazia():
    with pytest.raises(ValueError):
        anyio.run(lambda: agente.responder("   ", servidor=_servidor(), converse=ModeloFalso([])))


def test_erro_do_bedrock_vira_mensagem_clara():
    from botocore.exceptions import ClientError

    def recusa(**_):
        raise ClientError({"Error": {"Code": "UnrecognizedClientException", "Message": "The security token included in the request is invalid."}}, "Converse")

    with pytest.raises(agente.ErroAgente) as erro:
        anyio.run(lambda: agente.responder("oi", servidor=_servidor(), converse=recusa))
    assert "aws login" in str(erro.value) and "UnrecognizedClientException" in str(erro.value)
    assert agente._sessoes[next(iter(agente._sessoes))].mensagens == []  # a fala que falhou não fica no histórico


def test_sem_cota_passa_para_o_proximo_modelo(monkeypatch):
    from botocore.exceptions import ClientError
    monkeypatch.setenv("FRONTDESK_MODELO", "us.amazon.nova-lite-v1:0,us.amazon.nova-micro-v1:0")
    agente._pausados.clear()
    tentados = []

    def converse(**kw):
        tentados.append(kw["modelId"])
        if kw["modelId"] == "us.amazon.nova-lite-v1:0":
            raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "Too many tokens per day, please wait before trying again."}}, "Converse")
        return {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [{"text": "Olá!"}]}}}

    r = anyio.run(lambda: agente.responder("oi", servidor=_servidor(), converse=converse))
    assert r["resposta"] == "Olá!" and r["modelo"] == "us.amazon.nova-micro-v1:0"
    assert tentados == ["us.amazon.nova-lite-v1:0", "us.amazon.nova-micro-v1:0"]
    # o modelo sem cota fica pausado: a próxima fala já vai direto para o segundo
    tentados.clear()
    anyio.run(lambda: agente.responder("oi de novo", servidor=_servidor(), converse=converse))
    assert tentados == ["us.amazon.nova-micro-v1:0"]
    agente._pausados.clear()


def test_todos_sem_cota_explica(monkeypatch):
    from botocore.exceptions import ClientError
    monkeypatch.setenv("FRONTDESK_MODELO", "a,b")
    agente._pausados.clear()

    def converse(**kw):
        raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "Too many tokens per day, please wait before trying again."}}, "Converse")

    with pytest.raises(agente.ErroAgente) as erro:
        anyio.run(lambda: agente.responder("oi", servidor=_servidor(), converse=converse))
    assert "cota diária" in str(erro.value) and "FRONTDESK_AGENTE=ensaio" in str(erro.value)
    agente._pausados.clear()


def test_modo_auto_cai_para_o_ensaio(tmp_path, monkeypatch):
    """No deploy (FRONTDESK_AGENTE=auto), se o Bedrock falhar a conversa continua no modo ensaio, com aviso."""
    from starlette.testclient import TestClient
    from frontdesk import server

    async def falha(*a, **k):
        raise agente.ErroAgente("A conta da AWS está sem cota diária no Bedrock")

    monkeypatch.setenv("FRONTDESK_AGENTE", "auto")
    monkeypatch.setenv("FRONTDESK_MCP_URL", "local")
    monkeypatch.setattr(agente, "responder", falha)
    monkeypatch.setattr(server, "_falas_por_ip", {})
    monkeypatch.setattr(server, "_bedrock_fora", {"ate": 0.0, "aviso": ""})
    with TestClient(server.app()) as c:
        r = c.post("/api/conversa", json={"texto": "quanto custa o corte?"})
    dados = r.json()
    assert r.status_code == 200
    assert dados["modelo"].startswith("modo ensaio") and "sem cota" in dados["aviso"]
    assert dados["chamadas"][0]["ferramenta"] == "listar_servicos"
    # nos próximos minutos o servidor nem tenta o Bedrock: vai direto para o ensaio
    chamadas = []

    async def conta(*a, **k):
        chamadas.append(1)
        raise agente.ErroAgente("não deveria ser chamado")

    monkeypatch.setattr(agente, "responder", conta)
    with TestClient(server.app()) as c:
        r = c.post("/api/conversa", json={"texto": "oi"})
    assert r.status_code == 200 and chamadas == [] and r.json()["aviso"]
