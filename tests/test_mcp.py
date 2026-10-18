"""Teste de ida e volta: sobe o servidor de verdade e chama as ferramentas por um cliente MCP."""
import json
import os
import socket
import subprocess
import sys
import time
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
    env = {**os.environ, "PORT": str(porta), "FRONTDESK_DB": str(tmp_path / "teste.db")}
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


def test_listar_servicos(servidor):
    async def rodar():
        async with Client(servidor + "/mcp", mode="legacy") as cliente:
            ferramentas = await cliente.list_tools()
            resultado = await cliente.call_tool("listar_servicos", {})
            return ferramentas, resultado

    ferramentas, resultado = anyio.run(rodar)
    assert "listar_servicos" in [t.name for t in ferramentas.tools]
    assert not resultado.is_error
    texto = " ".join(c.text for c in resultado.content if getattr(c, "type", "") == "text")
    assert "Corte" in texto
