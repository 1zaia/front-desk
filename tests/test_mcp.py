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


def test_anotacoes_e_saida_estruturada(servidor):
    """Ferramentas de leitura e de escrita bem marcadas, e resposta em structuredContent."""
    async def rodar():
        async with Client(servidor + "/mcp", mode="legacy") as c:
            ferramentas = {t.name: t for t in (await c.list_tools()).tools}
            r = await c.call_tool("listar_servicos", {})
            return ferramentas, r

    ferramentas, r = anyio.run(rodar)
    for nome in ("listar_servicos", "consultar_horarios_livres", "resumo_do_dia"):
        assert ferramentas[nome].annotations.read_only_hint is True
    assert ferramentas["cancelar"].annotations.destructive_hint is True
    assert ferramentas["agendar"].annotations.read_only_hint is False
    assert all(t.title for t in ferramentas.values())
    assert all(t.output_schema for t in ferramentas.values())
    assert r.structured_content["ok"] is True and "Corte" in r.structured_content["fala"]


def test_latencia_das_ferramentas(servidor):
    """O Alexa+ pede ida e volta abaixo de 500 ms; aqui cada chamada tem de ficar bem abaixo disso."""
    async def rodar():
        async with Client(servidor + "/mcp", mode="legacy") as c:
            tempos = []
            for _ in range(5):
                t0 = time.perf_counter()
                await c.call_tool("consultar_horarios_livres", {"data": "sexta", "servico": "corte"})
                tempos.append(time.perf_counter() - t0)
            return max(tempos)

    assert anyio.run(rodar) < 0.5


def test_host_publico_liberado(tmp_path):
    """Atrás de um proxy, o domínio público em FRONTDESK_HOSTS precisa passar pela proteção de Host."""
    porta = _porta_livre()
    env = {**os.environ, "PORT": str(porta), "FRONTDESK_DB": str(tmp_path / "h.db"), "FRONTDESK_DEMO": "0",
           "FRONTDESK_HOSTS": "front-desk.exemplo.com"}
    proc = subprocess.Popen([sys.executable, "-m", "frontdesk.server"], env=env)
    base = f"http://127.0.0.1:{porta}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/health")
                break
            except OSError:
                time.sleep(0.1)
        corpo = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}).encode()

        def chamar(host):
            req = urllib.request.Request(base + "/mcp", data=corpo, method="POST", headers={
                "Host": host, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code

        assert chamar("front-desk.exemplo.com") == 200
        assert chamar("outro.exemplo.com") == 421
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_limite_de_falas_por_ip(monkeypatch):
    from types import SimpleNamespace
    from frontdesk import server
    monkeypatch.setattr(server, "LIMITE_POR_IP", 3)
    server._falas_por_ip.clear()
    pedido = SimpleNamespace(headers={"x-forwarded-for": "203.0.113.9"}, client=None)
    assert [server._dentro_do_limite(pedido) for _ in range(4)] == [True, True, True, False]
    outro = SimpleNamespace(headers={"x-forwarded-for": "198.51.100.7"}, client=None)
    assert server._dentro_do_limite(outro)
    server._falas_por_ip.clear()
