# conflicts.py
# ----------------------------------------------------
# Krocklogik: krockkontroll mot DB, helrapporter (program & lärare)

from __future__ import annotations

from datetime import time
from typing import Dict, Iterable, List, Set, Tuple, Optional

import pandas as pd

from parsing import (
    parse_day,
    parse_time_str,
    time_to_str,
    parse_program,
    parse_weeks,
    overlaps,
    INT_TO_DAY,
)
from db import fetch_events_for_semester


# ---------------------- Förslaget pass → DB-krock ----------------------

def check_conflict_in_db(
    semester: str,
    groups: Set[str],
    day: int,
    start: time,
    end: time,
    week: int,
    teachers: Optional[Set[str]] = None,   # ✅ korrekt typhint
) -> List[Dict[str, str]]:
    """
    Kollar om ett föreslaget pass krockar med DB för vald termin och veckodag.
    Krock om (minst ett program överlappar) ELLER (minst en lärare överlappar).
    """
    rows = fetch_events_for_semester(semester)
    conflicts: List[Dict[str, str]] = []

    for (course, g_str, d, s, e, weeks, _sem, teacher_str) in rows:
        if int(d) != int(day):
            continue

        g_set = parse_program(g_str)
        t_set = parse_program(teacher_str)
        same_group = bool(groups & g_set) if groups else False
        same_teacher = bool((teachers or set()) & t_set) if teachers else False

        if not (same_group or same_teacher):
            continue

        if week not in parse_weeks(weeks):
            continue

        if overlaps(parse_time_str(s), parse_time_str(e), start, end):
            conflicts.append({
                "kurskod": str(course),
                "program": str(g_str),
                "lärare": ";".join(sorted(t_set)) if t_set else "",
                "veckonummer": int(week),
                "veckodag": INT_TO_DAY.get(int(d), str(d)),
                "start": time_to_str(parse_time_str(s)),
                "slut": time_to_str(parse_time_str(e)),
            })

    return conflicts


# ---------------------- DB-vid rapport: alla rader (program) ----------------------

def compute_db_collisions(
    semester: str,
    programs_filter: Optional[Set[str]] = None,  # ✅
    days_filter: Optional[Set[int]] = None,      # ✅
    teacher_filter: Optional[Set[str]] = None,   # ✅
) -> pd.DataFrame:
    """
    Bygger full krockrapport genom att:
      1) Expandera rader till (program × veckonummer)
      2) Gruppera per (termin, program, vecka, dag)
      3) Jämföra alla intervall inom gruppen
    Filter:
      - programs_filter: endast de program som korsar filtermängden
      - days_filter: endast valda veckodagar (0–6)
      - teacher_filter: endast rader där lärarmängden korsar filtermängden
    """
    rows = fetch_events_for_semester(semester)

    # Expandera per program & vecka
    exp_rows: List[Dict[str, object]] = []
    for (course, g_str, d, s, e, weeks, sem, teacher) in rows:
        gset = parse_program(g_str)
        tset = parse_program(teacher)

        if programs_filter and not (gset & programs_filter):
            continue
        if teacher_filter and not (tset & teacher_filter):
            continue

        d_int = int(d)
        if (days_filter is not None) and (d_int not in days_filter):
            continue

        for w in sorted(parse_weeks(weeks)):
            for g in sorted(gset):
                exp_rows.append({
                    "termin": sem,
                    "veckonummer": int(w),
                    "dag_num": d_int,
                    "veckodag": INT_TO_DAY.get(d_int, str(d_int)),
                    "program": g,
                    "lärare": ";".join(sorted(tset)),
                    "kurskod": str(course),
                    "start": time_to_str(parse_time_str(s)),
                    "slut": time_to_str(parse_time_str(e)),
                })

    exp_df = pd.DataFrame(exp_rows)
    if exp_df.empty:
        return pd.DataFrame()

    # Krockdetektion inom (termin, program, vecka, dag)
    collisions: List[Dict[str, object]] = []
    for (term, prog, week, day), grp in exp_df.groupby(["termin", "program", "veckonummer", "dag_num"]):
        rows2 = [
            (r.kurskod, parse_time_str(r.start), parse_time_str(r.slut), r.lärare)
            for _, r in grp.iterrows()
        ]
        rows2.sort(key=lambda x: x[1])  # sortera på start

        for i in range(len(rows2)):
            c1, s1, e1, t1 = rows2[i]
            for j in range(i + 1, len(rows2)):
                c2, s2, e2, t2 = rows2[j]
                if s2 >= e1:
                    break  # tidig exit
                if overlaps(s1, e1, s2, e2):
                    collisions.append({
                        "termin": term,
                        "program": prog,
                        "veckonummer": int(week),
                        "veckodag": INT_TO_DAY.get(int(day), str(day)),
                        "kurskod_1": c1, "start_1": time_to_str(s1), "slut_1": time_to_str(e1), "lärare_1": t1,
                        "kurskod_2": c2, "start_2": time_to_str(s2), "slut_2": time_to_str(e2), "lärare_2": t2,
                    })

    return pd.DataFrame(collisions).sort_values(
        ["termin", "program", "veckonummer", "veckodag", "start_1", "start_2"]
    ).reset_index(drop=True)


# ---------------------- DB-vid rapport: lärardubbelbokning ----------------------

def compute_teacher_collisions(
    semester: str,
    days_filter: Optional[Set[int]] = None,      # ✅
    teachers_filter: Optional[Set[str]] = None,  # ✅
) -> pd.DataFrame:
    """
    Bygger lärarrapport genom att:
      1) Expandera rader till (lärare × veckonummer)
      2) Gruppera per (termin, lärare, vecka, dag)
      3) Jämföra alla intervall inom gruppen
    """
    rows = fetch_events_for_semester(semester)

    exp: List[Dict[str, object]] = []
    for (course, g_str, d, s, e, weeks, sem, teacher_str) in rows:
        tset = parse_program(teacher_str)
        d_int = int(d)

        if teachers_filter and not (tset & teachers_filter):
            continue
        if (days_filter is not None) and (d_int not in days_filter):
            continue

        for w in sorted(parse_weeks(weeks)):
            for teacher in (tset or {""}):
                exp.append({
                    "termin": sem,
                    "veckonummer": int(w),
                    "dag_num": d_int,
                    "veckodag": INT_TO_DAY.get(d_int, str(d_int)),
                    "lärare": teacher,
                    "kurskod": str(course),
                    "program": ";".join(sorted(parse_program(g_str))),
                    "start": time_to_str(parse_time_str(s)),
                    "slut": time_to_str(parse_time_str(e)),
                })

    exp_df = pd.DataFrame(exp)
    if exp_df.empty:
        return pd.DataFrame()

    collisions: List[Dict[str, object]] = []
    for (term, teacher, week, day), grp in exp_df.groupby(["termin", "lärare", "veckonummer", "dag_num"]):
        if not teacher:
            continue  # hoppa över rader utan lärare
        rows2 = [
            (r.kurskod, parse_time_str(r.start), parse_time_str(r.slut), r.program)
            for _, r in grp.iterrows()
        ]
        rows2.sort(key=lambda x: x[1])

        for i in range(len(rows2)):
            c1, s1, e1, p1 = rows2[i]
            for j in range(i + 1, len(rows2)):
                c2, s2, e2, p2 = rows2[j]
                if s2 >= e1:
                    break
                if overlaps(s1, e1, s2, e2):
                    collisions.append({
                        "termin": term,
                        "lärare": teacher,
                        "veckonummer": int(week),
                        "veckodag": INT_TO_DAY.get(int(day), str(day)),
                        "kurskod_1": c1, "program_1": p1, "start_1": time_to_str(s1), "slut_1": time_to_str(e1),
                        "kurskod_2": c2, "program_2": p2, "start_2": time_to_str(s2), "slut_2": time_to_str(e2),
                    })

    return pd.DataFrame(collisions).sort_values(
        ["termin", "lärare", "veckonummer", "veckodag", "start_1", "start_2"]
    ).reset_index(drop=True)
