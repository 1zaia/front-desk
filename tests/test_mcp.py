"""Teste de ida e volta: sobe o servidor de verdade e chama as ferramentas por um cliente MCP."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import anyio
import pytest

from mcp import Client


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def servidor(tmp_path):
    porta = _porta_livre()
    env = {**os.environ, "PORT": str(porta), "FRONTDESK_DB": str(tmp_path / "teste.db"),
           "FRONTDESK_AGORA": "2026-10-19T08:00", "FRONTDESK_DEMO": "0"}
    proc = subprocess.Popen([sys.executable, "-m", "frontdesk.server"], env=env)
    base = f"http://127.0.0.1:{porta}"
    for _ in range(100):
        try:
            with urllib.request.urlopen(base + "/health") as r:
                if r.status == 200:
                    break
        except OSError:
            time.sleep(0.1)
    yield base
    proc.terminate()
    proc.wait(timeout=5)


def test_initialize_2025_11_25(servidor):
    """O servidor aceita o handshake na versão exigida pelo hackathon."""
    corpo = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                   "clientInfo": {"name": "teste", "version": "0"}},
    }
    req = urllib.request.Request(
        servidor + "/mcp", data=json.dumps(corpo).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    with urllib.request.urlopen(req) as r:
        resposta = json.loads(r.read())
    assert resposta["result"]["protocolVersion"] == "2025-11-25"
    assert resposta["result"]["serverInfo"]["name"] == "front-desk"


def _dados(resultado) -> dict:
    texto = "".join(c.text for c in resultado.content if getattr(c, "type", "") == "text")
    return json.loads(texto)


def test_ferramentas_listadas(servidor):
    async def rodar():
        async with Client(servidor + "/mcp", mode="legacy") as cliente:
            return await cliente.list_tools()

    nomes = {t.name for t in anyio.run(rodar).tools}
    assert nomes == {"listar_servicos", "consultar_horarios_livres", "agendar", "cancelar", "reagendar", "resumo_do_dia"}


def test_conversa_completa(servidor):
    """O roteiro da demo: consultar, agendar, ver o resumo, reagendar e cancelar."""
    async def rodar():
        r = {}
        async with Client(servidor + "/mcp", mode="legacy") as c:
            r["servicos"] = _dados(await c.call_tool("listar_servicos", {}))
            r["livres"] = _dados(await c.call_tool("consultar_horarios_livres", {"data": "sexta", "servico": "corte", "periodo": "manhã"}))
            r["agendar"] = _dados(await c.call_tool("agendar", {"cliente": "joão", "servico": "corte", "data": "sexta", "horario": "9h"}))
            r["ocupado"] = _dados(await c.call_tool("agendar", {"cliente": "Ana", "servico": "corte", "data": "sexta", "horario": "9h", "profissional": r["agendar"]["agendamento"]["profissional"]}))
            r["resumo"] = _dados(await c.call_tool("resumo_do_dia", {"data": "sexta"}))
            r["reagendar"] = _dados(await c.call_tool("reagendar", {"cliente": "joão", "nova_data": "sexta", "novo_horario": "16h"}))
            r["cancelar"] = _dados(await c.call_tool("cancelar", {"cliente": "joao"}))
            r["domingo"] = _dados(await c.call_tool("agendar", {"cliente": "Ana", "servico": "barba", "data": "domingo", "horario": "10h"}))
        return r

    r = anyio.run(rodar)
    assert "Corte" in r["servicos"]["fala"]
    assert r["livres"]["ok"] and "sexta, 23/10" in r["livres"]["fala"] and "9h" in r["livres"]["fala"]
    assert r["agendar"]["ok"] and r["agendar"]["fala"].startswith("Pronto, João! Marquei corte para sexta")
    assert not r["ocupado"]["ok"] and "ocupado" in r["ocupado"]["fala"]
    assert r["resumo"]["total_clientes"] == 1
    assert r["reagendar"]["ok"] and r["reagendar"]["agendamento"]["inicio"] == "2026-10-23T16:00"
    assert r["cancelar"]["ok"] and r["cancelar"]["agendamento"]["status"] == "cancelado"
    assert not r["domingo"]["ok"] and "Não abrimos" in r["domingo"]["fala"]
    for etapa, dados in r.items():
        print(f"{etapa:>9}: {dados['fala']}")


def test_api_agenda(servidor):
    with urllib.request.urlopen(servidor + "/api/agenda?data=2026-10-23") as resp:
        dados = json.loads(resp.read())
    assert dados["negocio"] == "Barbearia Navalha"
    assert dados["data"] == "2026-10-23"


def test_api_semana(servidor):
    with urllib.request.urlopen(servidor + "/api/semana?dias=7") as resp:
        dados = json.loads(resp.read())
    assert dados["profissionais"] == ["Rafael", "Bruno"]
    assert len(dados["dias"]) == 7
    segunda, domingo = dados["dias"][0], dados["dias"][6]
    assert segunda["data"] == "2026-10-19" and segunda["aberto"] and segunda["abre"] == "09:00"
    assert domingo["dia"] == "domingo" and not domingo["aberto"]


def test_paginas(servidor):
    for caminho in ("/simulador", "/painel", "/demo"):
        with urllib.request.urlopen(servidor + caminho) as resp:
            assert resp.status == 200 and "text/html" in resp.headers["Content-Type"]


def test_conversa_sem_texto(servidor):
    req = urllib.request.Request(servidor + "/api/conversa", data=b'{"texto": ""}', method="POST",
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as erro:
        urllib.request.urlopen(req)
    assert erro.value.code == 400
