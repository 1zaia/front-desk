"""Regras da agenda: horários livres, agendar, cancelar, reagendar e resumo do dia.

Todas as funções recebem a conexão SQLite e o "agora" explicitamente, para que os
testes e a demo possam fixar a data. As datas e horas são locais do negócio.
"""
from __future__ import annotations

import os
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")
PASSO_MIN = 30  # de quanto em quanto tempo oferecemos horários

DIAS_SEMANA = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]
NOMES_DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
PERIODOS = {
    "manha": (time(0, 0), time(12, 0)),
    "tarde": (time(12, 0), time(18, 0)),
    "noite": (time(18, 0), time(23, 59)),
}


class ErroAgenda(ValueError):
    """Erro com mensagem pronta para ser falada ao cliente."""


@dataclass
class Servico:
    id: int
    nome: str
    duracao_min: int
    preco_centavos: int


@dataclass
class Profissional:
    id: int
    nome: str


# ---------------------------------------------------------------- utilidades

def agora() -> datetime:
    """Hora local do negócio. FRONTDESK_AGORA (ex.: 2026-10-23T08:00) fixa o relógio na demo e nos testes."""
    fixo = os.environ.get("FRONTDESK_AGORA")
    if fixo:
        return datetime.fromisoformat(fixo).replace(tzinfo=None)
    return datetime.now(FUSO).replace(tzinfo=None)


def _sem_acento(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto.strip().lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def _normaliza_servico(texto: str) -> str:
    t = _sem_acento(texto).replace("+", " e ").replace("&", " e ")
    return " ".join(t.split())


def interpretar_data(texto: str, hoje: date) -> date:
    """Aceita 2026-10-23, 23/10, 23/10/2026, hoje, amanhã, depois de amanhã ou o nome do dia da semana."""
    t = _sem_acento(texto).replace("-feira", "").replace("feira", "").strip()
    if t in ("hoje",):
        return hoje
    if t in ("amanha",):
        return hoje + timedelta(days=1)
    if t in ("depois de amanha",):
        return hoje + timedelta(days=2)
    for prefixo in ("proxima ", "proximo ", "na ", "no ", "nesta ", "neste ", "essa ", "esse "):
        if t.startswith(prefixo):
            t = t[len(prefixo):].strip()
    if t in DIAS_SEMANA:
        alvo = DIAS_SEMANA.index(t)
        return hoje + timedelta(days=(alvo - hoje.weekday()) % 7)
    try:
        return date.fromisoformat(t)
    except ValueError:
        pass
    partes = t.split("/")
    if len(partes) in (2, 3) and all(p.isdigit() for p in partes):
        dia, mes = int(partes[0]), int(partes[1])
        ano = int(partes[2]) if len(partes) == 3 else hoje.year
        if ano < 100:
            ano += 2000
        try:
            d = date(ano, mes, dia)
        except ValueError as e:
            raise ErroAgenda(f"Não entendi a data {texto}.") from e
        if len(partes) == 2 and d < hoje:
            d = date(ano + 1, mes, dia)
        return d
    raise ErroAgenda(f"Não entendi a data {texto}. Pode dizer, por exemplo, sexta ou 23/10?")


def interpretar_horario(texto: str) -> time:
    """Aceita 9, 9h, 9:30, 09h30, 14h, 14:00."""
    t = _sem_acento(texto).replace(" ", "").replace("horas", "h").replace("hrs", "h")
    t = t.replace("h", ":") if "h" in t else t
    if t.endswith(":"):
        t = t[:-1]
    partes = t.split(":")
    try:
        hora = int(partes[0])
        minuto = int(partes[1]) if len(partes) > 1 and partes[1] else 0
        return time(hora, minuto)
    except (ValueError, IndexError) as e:
        raise ErroAgenda(f"Não entendi o horário {texto}. Pode dizer, por exemplo, 9h30?") from e


def falar_data(d: date, hoje: date) -> str:
    if d == hoje:
        return "hoje"
    if d == hoje + timedelta(days=1):
        return "amanhã"
    return f"{NOMES_DIAS[d.weekday()]}, {d.day:02d}/{d.month:02d}"


def falar_hora(h: time) -> str:
    return f"{h.hour}h" if h.minute == 0 else f"{h.hour}h{h.minute:02d}"


def reais(centavos: int) -> str:
    return f"R$ {centavos / 100:.2f}".replace(".", ",")


# ---------------------------------------------------------------- consultas

def buscar_servico(conn: sqlite3.Connection, nome: str) -> Servico:
    alvo = _normaliza_servico(nome)
    linhas = conn.execute("SELECT id, nome, duracao_min, preco_centavos FROM servicos").fetchall()
    servicos = [Servico(**dict(r)) for r in linhas]
    exatos = [s for s in servicos if _normaliza_servico(s.nome) == alvo]
    if exatos:
        return exatos[0]
    parecidos = [s for s in servicos if alvo in _normaliza_servico(s.nome)]
    if len(parecidos) == 1:
        return parecidos[0]
    if not parecidos:
        parecidos = [s for s in servicos if _normaliza_servico(s.nome) in alvo]
        parecidos.sort(key=lambda s: len(s.nome), reverse=True)
        if parecidos:
            return parecidos[0]
    nomes = ", ".join(s.nome for s in servicos)
    raise ErroAgenda(f"Não encontrei o serviço {nome}. Temos: {nomes}.")


def buscar_profissional(conn: sqlite3.Connection, nome: str | None) -> Profissional | None:
    if not nome:
        return None
    alvo = _sem_acento(nome)
    for r in conn.execute("SELECT id, nome FROM profissionais ORDER BY id").fetchall():
        if _sem_acento(r["nome"]) == alvo or alvo in _sem_acento(r["nome"]):
            return Profissional(r["id"], r["nome"])
    nomes = ", ".join(r["nome"] for r in conn.execute("SELECT nome FROM profissionais ORDER BY id"))
    raise ErroAgenda(f"Não temos {nome} na equipe. Quem atende aqui: {nomes}.")


def _profissionais(conn: sqlite3.Connection) -> list[Profissional]:
    return [Profissional(r["id"], r["nome"]) for r in conn.execute("SELECT id, nome FROM profissionais ORDER BY id")]


def _funcionamento(conn: sqlite3.Connection, d: date) -> tuple[time, time] | None:
    r = conn.execute("SELECT abre, fecha FROM funcionamento WHERE dia_semana = ?", (d.weekday(),)).fetchone()
    if not r:
        return None
    return time.fromisoformat(r["abre"]), time.fromisoformat(r["fecha"])


def _ocupados(conn: sqlite3.Connection, profissional_id: int, d: date, ignorar_id: int | None = None):
    linhas = conn.execute(
        "SELECT id, inicio, fim FROM agendamentos WHERE profissional_id = ? AND status = 'confirmado' "
        "AND substr(inicio, 1, 10) = ?",
        (profissional_id, d.isoformat()),
    ).fetchall()
    return [
        (datetime.fromisoformat(r["inicio"]), datetime.fromisoformat(r["fim"]))
        for r in linhas
        if r["id"] != ignorar_id
    ]


def _livre(conn, profissional_id: int, inicio: datetime, fim: datetime, ignorar_id: int | None = None) -> bool:
    return all(fim <= a or inicio >= b for a, b in _ocupados(conn, profissional_id, inicio.date(), ignorar_id))


def _validar_janela(conn, inicio: datetime, fim: datetime, momento: datetime) -> None:
    hoje = momento.date()
    janela = _funcionamento(conn, inicio.date())
    if janela is None:
        raise ErroAgenda(f"Não abrimos {falar_data(inicio.date(), hoje)}.")
    abre, fecha = janela
    if inicio.time() < abre or fim.time() > fecha or fim.date() != inicio.date():
        raise ErroAgenda(
            f"{falar_data(inicio.date(), hoje).capitalize()} atendemos das {falar_hora(abre)} às {falar_hora(fecha)}."
        )
    if inicio < momento:
        raise ErroAgenda("Esse horário já passou.")


def horarios_livres(
    conn: sqlite3.Connection,
    d: date,
    servico: Servico,
    momento: datetime,
    periodo: str | None = None,
    profissional: Profissional | None = None,
) -> list[dict]:
    """Lista os horários em que o serviço cabe, por profissional, a cada PASSO_MIN minutos."""
    janela = _funcionamento(conn, d)
    if janela is None:
        return []
    abre, fecha = janela
    ini_periodo, fim_periodo = PERIODOS.get(_sem_acento(periodo or ""), (time(0, 0), time(23, 59)))
    duracao = timedelta(minutes=servico.duracao_min)
    equipe = [profissional] if profissional else _profissionais(conn)
    resultado = []
    for p in equipe:
        cursor = datetime.combine(d, abre)
        limite = datetime.combine(d, fecha)
        while cursor + duracao <= limite:
            if (
                cursor >= momento
                and ini_periodo <= cursor.time() < fim_periodo
                and _livre(conn, p.id, cursor, cursor + duracao)
            ):
                resultado.append({"profissional": p.nome, "horario": cursor.strftime("%H:%M")})
            cursor += timedelta(minutes=PASSO_MIN)
    resultado.sort(key=lambda x: (x["horario"], x["profissional"]))
    return resultado


# ---------------------------------------------------------------- ações

def agendar(
    conn: sqlite3.Connection,
    cliente: str,
    servico: Servico,
    inicio: datetime,
    momento: datetime,
    profissional: Profissional | None = None,
) -> dict:
    cliente = " ".join(cliente.split()).title()
    if not cliente:
        raise ErroAgenda("Qual é o seu nome?")
    fim = inicio + timedelta(minutes=servico.duracao_min)
    _validar_janela(conn, inicio, fim, momento)
    candidatos = [profissional] if profissional else _profissionais(conn)
    escolhido = next((p for p in candidatos if _livre(conn, p.id, inicio, fim)), None)
    if escolhido is None:
        quem = f"com {profissional.nome} " if profissional else ""
        raise ErroAgenda(f"O horário das {falar_hora(inicio.time())} {quem}já está ocupado.")
    cur = conn.execute(
        "INSERT INTO agendamentos (cliente, servico_id, profissional_id, inicio, fim) VALUES (?, ?, ?, ?, ?)",
        (cliente, servico.id, escolhido.id, inicio.isoformat(timespec="minutes"), fim.isoformat(timespec="minutes")),
    )
    conn.commit()
    return _agendamento(conn, cur.lastrowid)


def _agendamento(conn: sqlite3.Connection, ag_id: int) -> dict:
    r = conn.execute(
        "SELECT a.id, a.cliente, a.inicio, a.fim, a.status, s.nome AS servico, s.preco_centavos, p.nome AS profissional "
        "FROM agendamentos a JOIN servicos s ON s.id = a.servico_id JOIN profissionais p ON p.id = a.profissional_id "
        "WHERE a.id = ?",
        (ag_id,),
    ).fetchone()
    return dict(r)


def buscar_agendamentos(conn: sqlite3.Connection, cliente: str, momento: datetime, d: date | None = None) -> list[dict]:
    """Agendamentos futuros e confirmados de um cliente (busca sem acento e por parte do nome)."""
    alvo = _sem_acento(cliente)
    linhas = conn.execute(
        "SELECT id, cliente, inicio FROM agendamentos WHERE status = 'confirmado' AND inicio >= ? ORDER BY inicio",
        (momento.isoformat(timespec="minutes"),),
    ).fetchall()
    achados = []
    for r in linhas:
        if alvo and alvo in _sem_acento(r["cliente"]):
            if d is None or r["inicio"].startswith(d.isoformat()):
                achados.append(_agendamento(conn, r["id"]))
    return achados


def cancelar(conn: sqlite3.Connection, ag_id: int) -> dict:
    conn.execute("UPDATE agendamentos SET status = 'cancelado' WHERE id = ?", (ag_id,))
    conn.commit()
    return _agendamento(conn, ag_id)


def reagendar(conn: sqlite3.Connection, ag_id: int, novo_inicio: datetime, momento: datetime) -> dict:
    atual = conn.execute(
        "SELECT a.servico_id, a.profissional_id, s.duracao_min FROM agendamentos a "
        "JOIN servicos s ON s.id = a.servico_id WHERE a.id = ?",
        (ag_id,),
    ).fetchone()
    fim = novo_inicio + timedelta(minutes=atual["duracao_min"])
    _validar_janela(conn, novo_inicio, fim, momento)
    preferido = atual["profissional_id"]
    ordem = [preferido] + [p.id for p in _profissionais(conn) if p.id != preferido]
    escolhido = next((pid for pid in ordem if _livre(conn, pid, novo_inicio, fim, ignorar_id=ag_id)), None)
    if escolhido is None:
        raise ErroAgenda(f"O horário das {falar_hora(novo_inicio.time())} já está ocupado.")
    conn.execute(
        "UPDATE agendamentos SET inicio = ?, fim = ?, profissional_id = ? WHERE id = ?",
        (novo_inicio.isoformat(timespec="minutes"), fim.isoformat(timespec="minutes"), escolhido, ag_id),
    )
    conn.commit()
    return _agendamento(conn, ag_id)


def resumo_do_dia(conn: sqlite3.Connection, d: date, momento: datetime) -> dict:
    linhas = conn.execute(
        "SELECT id FROM agendamentos WHERE status = 'confirmado' AND substr(inicio, 1, 10) = ? ORDER BY inicio",
        (d.isoformat(),),
    ).fetchall()
    ags = [_agendamento(conn, r["id"]) for r in linhas]
    proximos = [a for a in ags if datetime.fromisoformat(a["inicio"]) >= momento]
    receita = sum(a["preco_centavos"] for a in ags)
    janela = _funcionamento(conn, d)
    livres_por_prof = {}
    if janela:
        abre, fecha = janela
        for p in _profissionais(conn):
            ocupado = sum(
                (b - a).total_seconds() / 60 for a, b in _ocupados(conn, p.id, d)
            )
            total = (datetime.combine(d, fecha) - datetime.combine(d, abre)).total_seconds() / 60
            livres_por_prof[p.nome] = int(total - ocupado)
    return {
        "data": d.isoformat(),
        "aberto": janela is not None,
        "total_clientes": len(ags),
        "receita_prevista_centavos": receita,
        "proximo": proximos[0] if proximos else None,
        "agendamentos": ags,
        "minutos_livres_por_profissional": livres_por_prof,
    }


def agendamentos_do_dia(conn: sqlite3.Connection, d: date) -> list[dict]:
    linhas = conn.execute(
        "SELECT id FROM agendamentos WHERE substr(inicio, 1, 10) = ? ORDER BY inicio", (d.isoformat(),)
    ).fetchall()
    return [_agendamento(conn, r["id"]) for r in linhas]


# ---------------------------------------------------------------- dados da demo

def semear_demo(conn: sqlite3.Connection, hoje: date) -> None:
    """Coloca alguns horários ocupados nos próximos dias, para a agenda parecer real."""
    if conn.execute("SELECT COUNT(*) FROM agendamentos").fetchone()[0]:
        return
    servicos = {s["nome"]: s["id"] for s in conn.execute("SELECT id, nome FROM servicos")}
    profs = {p["nome"]: p["id"] for p in conn.execute("SELECT id, nome FROM profissionais")}
    duracoes = {s["id"]: s["duracao_min"] for s in conn.execute("SELECT id, duracao_min FROM servicos")}
    modelo = [
        (0, "10:00", "Carlos Mendes", "Corte", "Rafael"),
        (0, "15:30", "Pedro Lima", "Corte + barba", "Bruno"),
        (1, "09:00", "Lucas Rocha", "Barba", "Rafael"),
        (1, "11:00", "André Souza", "Corte", "Bruno"),
        (1, "17:00", "Felipe Costa", "Pigmentação", "Rafael"),
        (2, "09:30", "Marcos Dias", "Corte + barba", "Rafael"),
        (2, "14:00", "Thiago Alves", "Corte", "Bruno"),
        (3, "10:30", "Rodrigo Nunes", "Corte", "Rafael"),
        (4, "09:00", "Gustavo Reis", "Corte", "Bruno"),
    ]
    for dias, hora, cliente, servico, prof in modelo:
        d = hoje + timedelta(days=dias)
        janela = _funcionamento(conn, d)
        if janela is None:
            continue
        inicio = datetime.combine(d, time.fromisoformat(hora))
        fim = inicio + timedelta(minutes=duracoes[servicos[servico]])
        if inicio.time() < janela[0] or fim.time() > janela[1]:
            continue
        conn.execute(
            "INSERT INTO agendamentos (cliente, servico_id, profissional_id, inicio, fim) VALUES (?, ?, ?, ?, ?)",
            (cliente, servicos[servico], profs[prof], inicio.isoformat(timespec="minutes"), fim.isoformat(timespec="minutes")),
        )
    conn.commit()
