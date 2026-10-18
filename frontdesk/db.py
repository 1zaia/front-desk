"""Agenda do Front Desk em SQLite: serviços, profissionais, funcionamento e agendamentos."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("FRONTDESK_DB", Path(__file__).resolve().parent.parent / "frontdesk.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS negocio (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    nome TEXT NOT NULL,
    fuso TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS servicos (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE,
    duracao_min INTEGER NOT NULL,
    preco_centavos INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS profissionais (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE
);
-- dia_semana: 0 = segunda ... 6 = domingo (igual a datetime.weekday())
CREATE TABLE IF NOT EXISTS funcionamento (
    dia_semana INTEGER PRIMARY KEY,
    abre TEXT NOT NULL,
    fecha TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agendamentos (
    id INTEGER PRIMARY KEY,
    cliente TEXT NOT NULL,
    servico_id INTEGER NOT NULL REFERENCES servicos(id),
    profissional_id INTEGER NOT NULL REFERENCES profissionais(id),
    inicio TEXT NOT NULL,  -- ISO local, ex.: 2026-10-23T09:00
    fim TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmado',
    criado_em TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agendamentos_inicio ON agendamentos(inicio);
"""

# Barbearia fictícia usada na demo
SEED_NEGOCIO = ("Barbearia Navalha", "America/Sao_Paulo")
SEED_SERVICOS = [
    ("Corte", 30, 4500),
    ("Barba", 20, 3000),
    ("Corte + barba", 50, 7000),
    ("Pigmentação", 40, 6000),
]
SEED_PROFISSIONAIS = ["Rafael", "Bruno"]
SEED_FUNCIONAMENTO = [
    (0, "09:00", "19:00"),
    (1, "09:00", "19:00"),
    (2, "09:00", "19:00"),
    (3, "09:00", "19:00"),
    (4, "09:00", "20:00"),
    (5, "08:00", "14:00"),
]


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM negocio").fetchone()[0] == 0:
        conn.execute("INSERT INTO negocio (id, nome, fuso) VALUES (1, ?, ?)", SEED_NEGOCIO)
        conn.executemany(
            "INSERT INTO servicos (nome, duracao_min, preco_centavos) VALUES (?, ?, ?)", SEED_SERVICOS
        )
        conn.executemany("INSERT INTO profissionais (nome) VALUES (?)", [(n,) for n in SEED_PROFISSIONAIS])
        conn.executemany(
            "INSERT INTO funcionamento (dia_semana, abre, fecha) VALUES (?, ?, ?)", SEED_FUNCIONAMENTO
        )
    conn.commit()


def listar_servicos(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT nome, duracao_min, preco_centavos FROM servicos ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def nome_do_negocio(conn: sqlite3.Connection) -> str:
    return conn.execute("SELECT nome FROM negocio WHERE id = 1").fetchone()["nome"]
