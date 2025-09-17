# db.py
# ----------------------------------------------------
# SQLite-hantering: skapa tabell, import från CSV/Excel, queries, städning.
# Kräver parsing.py för: parse_day, parse_time_str, time_to_str, parse_program,
# programs_to_str, parse_weeks

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Iterable, List, Set, Tuple, Dict, Any, Optional

import pandas as pd

from .parsing import (
    parse_day,
    parse_time_str,
    time_to_str,
    parse_program,
    programs_to_str,
)

DB_PATH = "timetable.db"

# ---------------------- Schema ----------------------

def init_db(db_path: str = DB_PATH) -> None:
    """Skapar tabellen om den inte finns."""
    with closing(sqlite3.connect(db_path)) as con, con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course   TEXT NOT NULL,   -- kurskod
                groups   TEXT NOT NULL,   -- program (semikolonavgr.)
                day      INTEGER NOT NULL, -- 0=mån .. 6=sön
                start    TEXT NOT NULL,   -- HH:MM
                end      TEXT NOT NULL,   -- HH:MM
                weeks    TEXT NOT NULL,   -- t.ex. '36-38,40'
                semester TEXT NOT NULL,   -- t.ex. 'HT-25'
                teacher  TEXT DEFAULT ''  -- lärare (semikolonavgr.)
            )
            """
        )

def ensure_teacher_column(db_path: str = DB_PATH) -> None:
    """Lägger till teacher-kolumn om äldre DB saknar den."""
    with closing(sqlite3.connect(db_path)) as con, con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(events)").fetchall()]
        if "teacher" not in cols:
            con.execute("ALTER TABLE events ADD COLUMN teacher TEXT DEFAULT ''")

# ---------------------- Kolumnmappning ----------------------

SWEDISH_MAP = {
    "kurskod": "course",
    "program": "groups",
    "veckodag": "day",
    "start": "start",
    "slut": "end",
    "veckonummer": "weeks",
    "termin": "semester",
    "lärare": "teacher",
    "larare": "teacher",
}
ENGLISH_MAP = {
    "course": "course",
    "groups": "groups",
    "day": "day",
    "start": "start",
    "end": "end",
    "weeks": "weeks",
    "semester": "semester",
    "teacher": "teacher",
}

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Mappar svenska/engelska rubriker till DB-fält och säkerställer teacher-kolumnen."""
    colmap: Dict[str, str] = {}
    lower_cols = {c.lower(): c for c in df.columns}
    for src, dst in {**SWEDISH_MAP, **ENGLISH_MAP}.items():
        if src in lower_cols:
            colmap[lower_cols[src]] = dst
    df2 = df.rename(columns=colmap)

    required = ["course", "groups", "day", "start", "end", "weeks", "semester"]
    missing = [c for c in required if c not in df2.columns]
    if missing:
        raise ValueError("Saknade kolumner: " + ", ".join(missing))

    if "teacher" not in df2.columns:
        df2["teacher"] = ""

    return df2[["course", "groups", "day", "start", "end", "weeks", "semester", "teacher"]]

# ---------------------- Import/Insert ----------------------

def bulk_insert_events(df: pd.DataFrame, db_path: str = DB_PATH) -> None:
    """Normaliserar indata och laddar in i SQLite."""
    df_norm = normalize_columns(df)
    rows: List[Tuple[Any, ...]] = []

    for _, r in df_norm.iterrows():
        rows.append(
            (
                str(r["course"]).strip(),
                programs_to_str(parse_program(r["groups"])),
                parse_day(r["day"]),
                time_to_str(parse_time_str(r["start"])),
                time_to_str(parse_time_str(r["end"])),
                str(r["weeks"]).strip(),
                str(r["semester"]).strip(),
                programs_to_str(parse_program(r.get("teacher", ""))),
            )
        )

    with closing(sqlite3.connect(db_path)) as con, con:
        con.executemany(
            """
            INSERT INTO events (course, groups, day, start, end, weeks, semester, teacher)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

# ---------------------- Queries (läs) ----------------------

def fetch_semesters(db_path: str = DB_PATH) -> List[str]:
    with closing(sqlite3.connect(db_path)) as con:
        rows = con.execute(
            "SELECT DISTINCT semester FROM events ORDER BY semester"
        ).fetchall()
    return [r[0] for r in rows]

def list_program_tokens(db_path: str = DB_PATH) -> List[str]:
    tokens = set()
    with closing(sqlite3.connect(db_path)) as con:
        for (gstr,) in con.execute("SELECT DISTINCT groups FROM events"):
            tokens.update({t.strip() for t in str(gstr).split(";") if t.strip()})
    return sorted(tokens)

def query_events(
    semester: str,
    groups_filter: Optional[Set[str]] = None,
    db_path: str = DB_PATH,
) -> pd.DataFrame:
    """
    Hämtar rader för en termin, ev. filtrerat på program (LIKE %X%).
    Returnerar DataFrame för visning i UI.
    """
    with closing(sqlite3.connect(db_path)) as con:
        if groups_filter:
            like_clauses = ["groups LIKE ?" for _ in groups_filter]
            sql = (
                "SELECT id, course, groups, day, start, end, weeks, semester, teacher FROM events "
                "WHERE semester=? AND (" + " OR ".join(like_clauses) + ") "
                "ORDER BY day, start"
            )
            params = [semester] + [f"%{g}%" for g in groups_filter]
        else:
            sql = (
                "SELECT id, course, groups, day, start, end, weeks, semester, teacher FROM events "
                "WHERE semester=? ORDER BY day, start"
            )
            params = [semester]
        df = pd.read_sql_query(sql, con, params=params)

    # UI-vänlig vy
    if not df.empty:
        from .parsing import INT_TO_DAY  # lokal import för att undvika cykel
        df["veckodag"] = df["day"].map(INT_TO_DAY)
        df = df[
            ["id", "course", "groups", "teacher", "veckodag", "start", "end", "weeks", "semester"]
        ].rename(
            columns={
                "course": "kurskod",
                "groups": "program",
                "teacher": "lärare",
                "end": "slut",
                "weeks": "veckonummer",
                "semester": "termin",
            }
        )
    return df

def fetch_events_for_semester(
    semester: str,
    db_path: str = DB_PATH,
) -> List[Tuple[str, str, int, str, str, str, str, str]]:
    """
    Returnerar råa events för termin.
    Kolumner: course, groups, day, start, end, weeks, semester, teacher
    (strängar för tider; parsning görs i models/conflicts)
    """
    with closing(sqlite3.connect(db_path)) as con:
        rows = con.execute(
            """
            SELECT course, groups, day, start, end, weeks, semester, teacher
            FROM events
            WHERE semester=?
            """,
            (semester,),
        ).fetchall()
    return rows

# ---------------------- Underhåll (radera/normalisera) ----------------------

def erase_all(db_path: str = DB_PATH) -> None:
    with closing(sqlite3.connect(db_path)) as con, con:
        con.execute("DELETE FROM events")

def erase_by_semester(semester: str, db_path: str = DB_PATH) -> None:
    with closing(sqlite3.connect(db_path)) as con, con:
        con.execute("DELETE FROM events WHERE semester=?", (semester,))

def erase_by_program(substr: str, db_path: str = DB_PATH) -> None:
    with closing(sqlite3.connect(db_path)) as con, con:
        con.execute(
            "DELETE FROM events WHERE groups LIKE ? COLLATE NOCASE", (f"%{substr}%",)
        )

def erase_by_course(course_name: str, db_path: str = DB_PATH) -> None:
    with closing(sqlite3.connect(db_path)) as con, con:
        con.execute(
            "DELETE FROM events WHERE course=? COLLATE NOCASE",
            (course_name.strip(),),
        )

def erase_by_ids(ids: List[int], db_path: str = DB_PATH) -> None:
    if not ids:
        return
    with closing(sqlite3.connect(db_path)) as con, con:
        con.executemany("DELETE FROM events WHERE id=?", [(int(i),) for i in ids])

def normalize_db_times(db_path: str = DB_PATH) -> None:
    """
    Normaliserar lagrade start/slut till HH:MM (t.ex. efter import med sekunddelar).
    """
    with closing(sqlite3.connect(db_path)) as con, con:
        rows = con.execute("SELECT id, start, end FROM events").fetchall()
        for _id, s, e in rows:
            try:
                ns = time_to_str(parse_time_str(s))
                ne = time_to_str(parse_time_str(e))
                con.execute(
                    "UPDATE events SET start=?, end=? WHERE id=?", (ns, ne, _id)
                )
            except Exception:
                # hoppa över rader som inte går att tolka
                pass
