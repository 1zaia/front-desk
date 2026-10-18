"""Testes das regras da agenda, com relógio fixo numa segunda-feira (19/10/2026) às 8h."""
from datetime import date, datetime, time

import pytest

from frontdesk import agenda, db
from frontdesk.agenda import ErroAgenda

AGORA = datetime(2026, 10, 19, 8, 0)  # segunda-feira
HOJE = AGORA.date()


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def corte(conn):
    return agenda.buscar_servico(conn, "corte")


# ------------------------------------------------------------ interpretação

@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("hoje", date(2026, 10, 19)),
        ("amanhã", date(2026, 10, 20)),
        ("depois de amanhã", date(2026, 10, 21)),
        ("sexta", date(2026, 10, 23)),
        ("sexta-feira", date(2026, 10, 23)),
        ("na quarta", date(2026, 10, 21)),
        ("segunda", date(2026, 10, 19)),
        ("sábado", date(2026, 10, 24)),
        ("2026-10-23", date(2026, 10, 23)),
        ("23/10", date(2026, 10, 23)),
        ("05/01", date(2027, 1, 5)),
        ("23/10/2026", date(2026, 10, 23)),
    ],
)
def test_interpretar_data(texto, esperado):
    assert agenda.interpretar_data(texto, HOJE) == esperado


def test_interpretar_data_invalida():
    with pytest.raises(ErroAgenda):
        agenda.interpretar_data("semana que vem talvez", HOJE)


@pytest.mark.parametrize(
    "texto, esperado",
    [("9", time(9)), ("9h", time(9)), ("9h30", time(9, 30)), ("14:00", time(14)), ("14 horas", time(14))],
)
def test_interpretar_horario(texto, esperado):
    assert agenda.interpretar_horario(texto) == esperado


@pytest.mark.parametrize(
    "texto, esperado",
    [("corte", "Corte"), ("Barba", "Barba"), ("corte e barba", "Corte + barba"), ("corte + barba", "Corte + barba"),
     ("pigmentacao", "Pigmentação"), ("um corte de cabelo", "Corte")],
)
def test_buscar_servico(conn, texto, esperado):
    assert agenda.buscar_servico(conn, texto).nome == esperado


def test_servico_inexistente(conn):
    with pytest.raises(ErroAgenda, match="Temos"):
        agenda.buscar_servico(conn, "massagem")


# ------------------------------------------------------------ horários livres

def test_horarios_livres_manha(conn):
    livres = agenda.horarios_livres(conn, date(2026, 10, 23), corte(conn), AGORA, "manhã")
    horarios = {x["horario"] for x in livres}
    assert "09:00" in horarios and "11:30" in horarios
    assert all(x["horario"] < "12:00" for x in livres)
    assert {x["profissional"] for x in livres} == {"Rafael", "Bruno"}


def test_horarios_respeitam_fechamento(conn):
    # Sábado fecha às 14h: um corte de 30 min começa no máximo às 13h30
    livres = agenda.horarios_livres(conn, date(2026, 10, 24), corte(conn), AGORA)
    assert max(x["horario"] for x in livres) == "13:30"
    assert agenda.horarios_livres(conn, date(2026, 10, 25), corte(conn), AGORA) == []  # domingo


def test_horarios_ignoram_o_passado(conn):
    momento = datetime(2026, 10, 19, 15, 10)
    livres = agenda.horarios_livres(conn, HOJE, corte(conn), momento)
    assert min(x["horario"] for x in livres) == "15:30"


def test_horario_ocupado_some_da_lista(conn):
    rafael = agenda.buscar_profissional(conn, "rafael")
    agenda.agendar(conn, "Ana", corte(conn), datetime(2026, 10, 23, 9, 0), AGORA, rafael)
    livres = agenda.horarios_livres(conn, date(2026, 10, 23), corte(conn), AGORA, "manha", rafael)
    assert "09:00" not in {x["horario"] for x in livres}
    assert "09:30" in {x["horario"] for x in livres}


# ------------------------------------------------------------ agendar

def test_agendar_escolhe_quem_esta_livre(conn):
    s = corte(conn)
    a1 = agenda.agendar(conn, "joão silva", s, datetime(2026, 10, 23, 9, 0), AGORA)
    a2 = agenda.agendar(conn, "Maria", s, datetime(2026, 10, 23, 9, 0), AGORA)
    assert a1["cliente"] == "João Silva"
    assert {a1["profissional"], a2["profissional"]} == {"Rafael", "Bruno"}
    with pytest.raises(ErroAgenda, match="ocupado"):
        agenda.agendar(conn, "Pedro", s, datetime(2026, 10, 23, 9, 0), AGORA)


def test_agendar_bloqueia_sobreposicao(conn):
    combo = agenda.buscar_servico(conn, "corte e barba")  # 50 min
    rafael = agenda.buscar_profissional(conn, "Rafael")
    agenda.agendar(conn, "Ana", combo, datetime(2026, 10, 23, 10, 0), AGORA, rafael)
    with pytest.raises(ErroAgenda):
        agenda.agendar(conn, "Bia", corte(conn), datetime(2026, 10, 23, 10, 30), AGORA, rafael)
    agenda.agendar(conn, "Bia", corte(conn), datetime(2026, 10, 23, 11, 0), AGORA, rafael)  # cabe depois


@pytest.mark.parametrize(
    "inicio, trecho",
    [
        (datetime(2026, 10, 25, 10, 0), "Não abrimos"),
        (datetime(2026, 10, 23, 7, 0), "atendemos"),
        (datetime(2026, 10, 24, 13, 45), "atendemos"),
        (datetime(2026, 10, 19, 7, 0), "atendemos"),
    ],
)
def test_agendar_fora_do_funcionamento(conn, inicio, trecho):
    with pytest.raises(ErroAgenda, match=trecho):
        agenda.agendar(conn, "Ana", corte(conn), inicio, AGORA)


def test_agendar_no_passado(conn):
    with pytest.raises(ErroAgenda, match="passou"):
        agenda.agendar(conn, "Ana", corte(conn), datetime(2026, 10, 19, 9, 0), datetime(2026, 10, 19, 10, 0))


# ------------------------------------------------------------ cancelar e reagendar

def test_cancelar_libera_o_horario(conn):
    rafael = agenda.buscar_profissional(conn, "Rafael")
    ag = agenda.agendar(conn, "Ana Paula", corte(conn), datetime(2026, 10, 23, 9, 0), AGORA, rafael)
    achados = agenda.buscar_agendamentos(conn, "ana", AGORA)
    assert [a["id"] for a in achados] == [ag["id"]]
    assert agenda.cancelar(conn, ag["id"])["status"] == "cancelado"
    assert agenda.buscar_agendamentos(conn, "ana", AGORA) == []
    agenda.agendar(conn, "Bia", corte(conn), datetime(2026, 10, 23, 9, 0), AGORA, rafael)


def test_reagendar_mantem_profissional(conn):
    bruno = agenda.buscar_profissional(conn, "Bruno")
    ag = agenda.agendar(conn, "Caio", corte(conn), datetime(2026, 10, 23, 9, 0), AGORA, bruno)
    novo = agenda.reagendar(conn, ag["id"], datetime(2026, 10, 23, 16, 0), AGORA)
    assert novo["inicio"] == "2026-10-23T16:00"
    assert novo["profissional"] == "Bruno"


def test_reagendar_para_o_mesmo_horario_nao_conflita_consigo(conn):
    ag = agenda.agendar(conn, "Caio", corte(conn), datetime(2026, 10, 23, 9, 0), AGORA)
    novo = agenda.reagendar(conn, ag["id"], datetime(2026, 10, 23, 9, 0), AGORA)
    assert novo["profissional"] == ag["profissional"]


# ------------------------------------------------------------ resumo e demo

def test_resumo_do_dia(conn):
    agenda.agendar(conn, "Ana", corte(conn), datetime(2026, 10, 19, 10, 0), AGORA)
    agenda.agendar(conn, "Bia", agenda.buscar_servico(conn, "barba"), datetime(2026, 10, 19, 11, 0), AGORA)
    r = agenda.resumo_do_dia(conn, HOJE, AGORA)
    assert r["total_clientes"] == 2
    assert r["receita_prevista_centavos"] == 4500 + 3000
    assert r["proximo"]["cliente"] == "Ana"


def test_semear_demo_so_uma_vez(conn):
    agenda.semear_demo(conn, HOJE)
    n = conn.execute("SELECT COUNT(*) FROM agendamentos").fetchone()[0]
    assert n > 0
    agenda.semear_demo(conn, HOJE)
    assert conn.execute("SELECT COUNT(*) FROM agendamentos").fetchone()[0] == n
