"""Modo ensaio: o agente por regras usa as mesmas ferramentas MCP, sem Bedrock."""
import anyio
import pytest

from frontdesk import ensaio


@pytest.fixture(autouse=True)
def ambiente(tmp_path, monkeypatch):
    from frontdesk import db
    monkeypatch.setenv("FRONTDESK_AGORA", "2026-10-18T18:00")
    monkeypatch.setenv("FRONTDESK_DEMO", "0")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ensaio.db")
    ensaio._estados.clear()


def _conversa(*falas):
    from frontdesk.server import mcp

    async def rodar():
        sid, saidas = None, []
        for f in falas:
            r = await ensaio.responder(f, sid, servidor=mcp)
            sid = r["sessao"]
            saidas.append(r)
        return saidas
    return anyio.run(rodar)


def test_marcar_em_tres_falas():
    r = _conversa("tem horário pra corte sexta de manhã?", "às 9h", "João")
    assert r[0]["chamadas"][0]["ferramenta"] == "consultar_horarios_livres"
    assert r[0]["chamadas"][0]["argumentos"] == {"data": "sexta", "servico": "corte", "periodo": "manhã"}
    assert r[1]["chamadas"] == [] and "nome" in r[1]["resposta"]
    assert r[2]["chamadas"][0]["ferramenta"] == "agendar" and r[2]["chamadas"][0]["ok"]
    assert r[2]["resposta"].startswith("Pronto, João!")
    assert r[2]["modelo"] == ensaio.NOME_MODO


def test_tudo_numa_frase_e_meio_dia():
    r = _conversa("quero cortar o cabelo segunda-feira ao meio-dia, meu nome é Ana Paula")
    c = r[0]["chamadas"][0]
    assert c["ferramenta"] == "agendar" and c["argumentos"]["horario"] == "12h" and c["argumentos"]["data"] == "segunda"
    assert c["argumentos"]["cliente"] == "Ana Paula" and c["ok"]


def test_cancelar_e_servicos():
    r = _conversa("corte e barba amanhã às 10h, meu nome é Pedro", "quanto custa a barba?", "quero cancelar")
    assert r[0]["chamadas"][0]["argumentos"]["servico"] == "corte e barba" and r[0]["chamadas"][0]["ok"]
    assert r[1]["chamadas"][0]["ferramenta"] == "listar_servicos"
    assert r[2]["chamadas"][0]["ferramenta"] == "cancelar" and r[2]["chamadas"][0]["ok"]


def test_pergunta_o_que_falta():
    r = _conversa("quero marcar um horário", "barba", "amanhã à tarde")
    assert "serviço" in r[0]["resposta"]
    assert "dia" in r[1]["resposta"]
    assert r[2]["chamadas"][0]["argumentos"] == {"data": "amanhã", "servico": "barba", "periodo": "tarde"}


def test_saudacao_funcionamento_e_fala_do_reconhecimento():
    r = _conversa("Oi, bom dia")
    assert r[0]["resposta"].startswith("Olá!") and r[0]["chamadas"] == []
    r = _conversa("Vocês abrem domingo?")
    assert r[0]["resposta"].startswith("Não abrimos")  # o relógio do teste está num domingo
    r = _conversa("Vocês abrem sábado?")
    assert r[0]["resposta"].startswith("Abrimos sim")
    # frase real que o reconhecimento de voz do Edge devolveu no teste do Juan
    r = _conversa("Segunda-feira é meio-dia ou é mais rápido?", "corte", "Ana")
    assert r[0]["resposta"].startswith("Claro! Para segunda às 12h")
    assert r[2]["chamadas"][0]["argumentos"] == {"cliente": "Ana", "servico": "corte", "data": "segunda", "horario": "12h"}
    assert r[2]["chamadas"][0]["ok"]
