# app.py — Schemaläggningshjälp (SQLite)
# ----------------------------------------------------
# Funktioner
# - Ladda upp CSV/Excel med svensk kolumnuppsättning → lagras i SQLite
# - Visa/sök per termin & program (t.ex. MTBG, VAKT, NGMV)
# - Robust veckodags-tolkning (svenska & engelska; mån/måndag/Mon/0–6/1–7)
# - Robust tidsparsning ('09:00', '09:00:00', '9.00', '9', Excel-fraktioner, Timestamp)
# - Krockkontroll mot databasen (pass som ligger direkt efter varandra räknas inte som krock)
# - Förslag på lediga tider (krockfri för valda program)
# - Databashantering i appen: rensa allt / per termin / per program / per kurs; normalisera tider; ta bort valda rader

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import time, timedelta, datetime, date
from typing import Iterable, List, Set, Tuple, Dict

import pandas as pd
import streamlit as st

DB_PATH = "timetable.db"

# ---------------------- Dagar/Tider ----------------------
DAY_TO_INT = {
    # Svenska varianter
    "mån": 0, "mandag": 0, "måndag": 0, "man": 0, "mon": 0,
    "tis": 1, "tisdag": 1,
    "ons": 2, "onsdag": 2,
    "tor": 3, "tors": 3, "torsdag": 3, "thu": 3, "thur": 3, "thurs": 3,
    "fre": 4, "fredag": 4,
    "lor": 5, "lör": 5, "lordag": 5, "lördag": 5, "sat": 5, "saturday": 5,
    "son": 6, "sön": 6, "sondag": 6, "söndag": 6, "sun": 6, "sunday": 6,
    # Engelska
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
}
INT_TO_DAY = {0: "Mån", 1: "Tis", 2: "Ons", 3: "Tors", 4: "Fre", 5: "Lör", 6: "Sön"}


def _normalize_ascii(s: str) -> str:
    return (
        s.replace("å", "a").replace("ä", "a").replace("ö", "o")
         .replace("Å", "A").replace("Ä", "A").replace("Ö", "O")
    )


def parse_day(value) -> int:
    """Accepterar 'mån', 'måndag', 'Mon', 0..6, 1..7 → returnerar 0..6 (mån=0)."""
    s = str(value).strip()
    if s.isdigit():
        n = int(s)
        if 0 <= n <= 6:
            return n
        if 1 <= n <= 7:
            return (n - 1) % 7
        raise ValueError(f"Veckodagsnummer utanför intervall: {s}")
    s_norm = _normalize_ascii(s).lower()
    if s_norm in DAY_TO_INT:
        return DAY_TO_INT[s_norm]
    if len(s_norm) >= 3 and s_norm[:3] in DAY_TO_INT:
        return DAY_TO_INT[s_norm[:3]]
    raise ValueError(f"Okänd veckodag: {value}")


def parse_time_str(x) -> time:
    """Accepterar '09:00', '09:00:00', '9.00', '9', 9 (timme), pandas.Timestamp,
    Excel-dagsfraktion (0..1), eller numerisk timme/minut (t.ex. 13.5)."""
    # 1) pandas Timestamp
    if isinstance(x, pd.Timestamp):
        return time(x.hour, x.minute)
    # 2) Numeriskt
    if isinstance(x, (int, float)):
        xf = float(x)
        # 0..2: sannolikt Excel-fraktion av dygn
        if 0 <= xf < 2:
            total_seconds = int(round(xf * 24 * 3600))
            hh = (total_seconds // 3600) % 24
            mm = (total_seconds % 3600) // 60
            return time(hh, mm)
        # 2..24: tolka som timme (ev. decimaler = minuter)
        if 0 <= xf < 24:
            hh = int(xf)
            mm = int(round((xf - hh) * 60))
            if mm == 60:
                hh = (hh + 1) % 24
                mm = 0
            return time(hh, mm)
    # 3) Strängar
    s = str(x).strip()
    # rena heltal: "9" → 09:00
    if s.isdigit():
        hh = int(s)
        if 0 <= hh < 24:
            return time(hh, 0)
    # HH:MM eller HH:MM:SS
    if ":" in s:
        parts = s.split(":")
        if len(parts) >= 2:
            hh = int(parts[0]); mm = int(parts[1])
            return time(hh, mm)
    # HH.MM
    if "." in s:
        parts = s.split(".")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            hh = int(parts[0]); mm = int(parts[1])
            return time(hh, mm)
    # sista utväg: låt pandas tolka
    try:
        ts = pd.to_datetime(s)
        return time(ts.hour, ts.minute)
    except Exception:
        pass
    raise ValueError(f"Okänt tidsformat: {x!r}")


def time_to_str(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def parse_program(s: str) -> Set[str]:
    """Dela på semikolon och normalisera till VERSALER för robust jämförelse."""
    return {str(g).strip().upper() for g in str(s).split(";") if str(g).strip()}


def programs_to_str(gs: Iterable[str]) -> str:
    return ";".join(sorted(set(gs)))


def parse_weeks(s: str) -> Set[int]:
    """Tål format som '36-38, 40', 'v36–38', 'vecka 36', 'W36'."""
    import re
    text = str(s).lower().replace("\u2013", "-")
    # ta bort ord som kan förekomma
    text = text.replace("vecka", "").replace("veckor", "").replace("v.", "v").replace("w", "").replace(" ", "")
    weeks: Set[int] = set()
    # hitta intervall först, t.ex. 36-38
    for a,b in re.findall(r"(\d{1,2})\s*-\s*(\d{1,2})", text):
        a,b = int(a), int(b)
        lo,hi = (a,b) if a<=b else (b,a)
        weeks.update(range(lo, hi+1))
    # ta bort intervalldelar så att ensamma tal inte dubblas
    text_no_ranges = re.sub(r"\d{1,2}\s*-\s*\d{1,2}", ",", text)
    for num in re.findall(r"\d{1,2}", text_no_ranges):
        weeks.add(int(num))
    return weeks


def weeks_to_str(weeks: Iterable[int]) -> str:
    return ",".join(str(w) for w in sorted(set(weeks)))


def overlaps(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    """Äkta överlapp: pass som bara möts vid gränsen (t.ex. 08:00–10:00 och 10:00–12:00)
    räknas INTE som krock. Används i både krockkontroll och förslagslogik."""
    return (a_start < b_end) and (b_start < a_end) and not (a_end == b_start or b_end == a_start)

# ---------------------- Databas ----------------------
def init_db():
    with closing(sqlite3.connect(DB_PATH)) as con, con:
        con.execute(
            """
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
st.markdown("_Verktyg för terminsplanering med krockkontroll och förslag på lediga tider_")

init_db()

with st.sidebar:
    st.header("Ladda upp schemafil")
    st.caption("Accepterar CSV eller Excel med rubrikerna: kurskod, program, veckodag, start, slut, veckonummer, termin")
    up = st.file_uploader("CSV eller Excel", type=["csv", "xlsx", "xls"])
    if st.button("Importera till databas", use_container_width=True, disabled=up is None):
        try:
            if up.name.lower().endswith(".csv"):
                df = pd.read_csv(up)
            else:
                df = pd.read_excel(up)
            bulk_insert_events(df)
            sems = ", ".join(sorted(normalize_columns(df)["semester"].astype(str).unique()))
            st.success(f"Importerade {len(df)} rader. Termer: {sems}")
        except Exception as e:
            st.exception(e)

    st.markdown("---")
    st.header("Hantera databas")
    if st.button("🗑️ Rensa ALLT", use_container_width=True):
        erase_all()
        st.success("Databasen är tömd.")
    sem_to_erase = st.text_input("Ta bort per termin (t.ex. 2025-HT)", "")
    if st.button("Ta bort termin", use_container_width=True, disabled=not sem_to_erase.strip()):
        erase_by_semester(sem_to_erase.strip())
        st.success(f"Tog bort termin: {sem_to_erase}")
    prog_list = list_program_tokens()
    if prog_list:
        prog_choice = st.selectbox("Ta bort per program (välj)", [""] + prog_list)
        if st.button("Ta bort valt program", use_container_width=True, disabled=not prog_choice):
            erase_by_program(prog_choice)
            st.success(f"Tog bort alla pass för program: {prog_choice}")
    course_to_erase = st.text_input("Ta bort per kurskod (skriv)", "")
    if st.button("Ta bort kurskod", use_container_width=True, disabled=not course_to_erase.strip()):
        erase_by_course(course_to_erase.strip())
        st.success(f"Tog bort alla pass för kurskod: {course_to_erase}")
    if st.button("Normalisera lagrade tider till HH:MM", use_container_width=True):
        normalize_db_times()
        st.success("Tider normaliserade.")

st.markdown("---")

# Utforskare
st.subheader("Utforska & redigera befintligt schema")
available_semesters = fetch_semesters()
col1, col2 = st.columns(2)
with col1:
    sem_sel = st.selectbox("Termin", options=available_semesters or ["(inga data)"])
with col2:
    prog_text = st.text_input("Filtrera på program (separera med semikolon)", "")
    prog_filter = {g.strip() for g in prog_text.split(";") if g.strip()} or None

if available_semesters:
    df_view = query_events(sem_sel, prog_filter)
    st.dataframe(df_view, use_container_width=True, hide_index=True)
    ids_to_delete = st.multiselect("Markera rader att ta bort (ID)", options=df_view["id"].tolist())
    if st.button("Ta bort markerade rader"):
        erase_by_ids(ids_to_delete)
        st.success(f"Tog bort {len(ids_to_delete)} rad(er). Ladda om tabellen ovan.")
else:
    st.info("Inga data ännu. Ladda upp en fil i sidofältet.")

st.markdown("---")

# 📊 Krockrapport i databasen (alla rader mot varandra)

def compute_db_collisions(semester: str, programs_filter: Set[str] | None = None, days_filter: Set[int] | None = None) -> pd.DataFrame:
    """Beräknar krockar mellan ALLA inlagda rader i DB för vald termin,
    valfritt filtrerat på program (semikolonavgränsad mängd). Returnerar DataFrame."""
    with closing(sqlite3.connect(DB_PATH)) as con:
        rows = con.execute(
            "SELECT course, groups, day, start, end, weeks, semester FROM events WHERE semester=?",
            (semester,)
        ).fetchall()

    # Expandera per program & vecka
    exp_rows = []
    for course, g_str, d, s, e, weeks, sem in rows:
        gset = parse_program(g_str)
        if programs_filter and not (gset & programs_filter):
            continue
        for w in sorted(parse_weeks(weeks)):
            for g in sorted(gset):
                d_int = int(d)
                if days_filter is not None and d_int not in days_filter:
                    continue
                exp_rows.append({
                    "termin": sem,
                    "veckonummer": w,
                    "dag_num": d_int,
                    "veckodag": INT_TO_DAY.get(d_int, str(d_int)),
                    "program": g,
                    "kurskod": str(course),
                    "start": time_to_str(parse_time_str(s)),
                    "slut": time_to_str(parse_time_str(e)),
                })
    exp_df = pd.DataFrame(exp_rows)
    if exp_df.empty:
        return pd.DataFrame()

    # Krockdetektion (strikt) inom varje (termin, program, vecka, dag)
    collisions = []
    for (term, prog, week, day), grp in exp_df.groupby(["termin","program","veckonummer","dag_num"]):
        rows = [(r.kurskod, parse_time_str(r.start), parse_time_str(r.slut)) for _, r in grp.iterrows()]
        rows.sort(key=lambda x: x[1])
        for i in range(len(rows)):
            c1,s1,e1 = rows[i]
            for j in range(i+1, len(rows)):
                c2,s2,e2 = rows[j]
                if s2 >= e1:
                    break
                if overlaps(s1,e1,s2,e2):
                    collisions.append({
                        "termin": term,
                        "program": prog,
                        "veckonummer": week,
                        "veckodag": INT_TO_DAY.get(day, str(day)),
                        "kurskod_1": c1, "start_1": time_to_str(s1), "slut_1": time_to_str(e1),
                        "kurskod_2": c2, "start_2": time_to_str(s2), "slut_2": time_to_str(e2),
                    })
    return pd.DataFrame(collisions).sort_values([
        "termin","program","veckonummer","veckodag","start_1","start_2"
    ]).reset_index(drop=True)

st.subheader("Krockrapport – alla schemarader i databasen")
colk1, colk2, colk3 = st.columns([2,2,1])
with colk1:
    rep_sem = st.selectbox("Termin (rapport)", options=available_semesters or ["(inga data)"]) 
with colk2:
    rep_prog_text = st.text_input("Filtrera på program (semikolon, tomt = alla)", "")
with colk3:
    rep_days = st.multiselect("Veckodagar", options=["Mån","Tis","Ons","Tors","Fre","Lör","Sön"], default=["Mån","Tis","Ons","Tors","Fre","Lör","Sön"])
run_report = st.button("Visa krockar")

if run_report and available_semesters:
    rep_filter = {g.strip().upper() for g in rep_prog_text.split(";") if g.strip()} or None
    day_map_ui = {"Mån":0,"Tis":1,"Ons":2,"Tors":3,"Fre":4,"Lör":5,"Sön":6}
    rep_days_set = {day_map_ui[d] for d in rep_days} if rep_days else None
    rep_df = compute_db_collisions(rep_sem, rep_filter, rep_days_set)
    if rep_df.empty:
        st.info("Inga krockar hittades för vald termin/filtrering.")
    else:
        st.dataframe(rep_df, use_container_width=True)
        # Summering per program + veckonummer
        with st.expander("Summering per program och veckonummer"):
            summary = (rep_df.groupby(["program","veckonummer"]).size()
                       .reset_index(name="antal_krockar")
                       .sort_values(["program","veckonummer"]))
            st.dataframe(summary, use_container_width=True)
        # Exportknapp
        csv_bytes = rep_df.to_csv(index=False).encode("utf-8")
        st.download_button("Ladda ner krockrapport (CSV)", data=csv_bytes, file_name="krockrapport.csv", mime="text/csv")

st.markdown("---")

# Krockkontroll & förslag
st.subheader("Kontrollera ett föreslaget pass & få förslag")
with st.form("proposal_form"):
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        prop_course = st.text_input("Kurskod", "NYTT-PASS")
        # Välj program från DB om möjligt, annars fritext
        _prog_opts = list_program_tokens()
        if _prog_opts:
            prop_groups = st.multiselect("Program (välj en eller flera)", options=_prog_opts, default=_prog_opts[:1])
        else:
            prop_groups = st.text_input("Program (t.ex. MTBG;NGMV)", "MTBG")
    with c2:
        # Använd termin från DB för att undvika stavfel/mismatch
        prop_sem = st.selectbox("Termin", options=available_semesters or ["2025-HT"], index=0 if available_semesters else 0)
        prop_days = st.multiselect(
            "Veckodagar (krockkontroll)",
            options=["Mån", "Tis", "Ons", "Tors", "Fre", "Lör", "Sön"],
            default=["Mån", "Tis", "Ons", "Tors", "Fre"]
        )
    with c3:
        prop_start = st.text_input("Start (HH:MM)", "09:00")
        prop_end = st.text_input("Slut (HH:MM)", "11:00")
    c4, c5, c6 = st.columns([1, 1, 2])
    with c4:
        prop_week = st.number_input("Vecka #", min_value=1, max_value=53, value=36, step=1)
    with c5:
        sug_duration = st.number_input("Föreslagen längd (min)", min_value=30, max_value=300, value=90, step=15)
    with c6:
        sug_weeks = st.text_input("Föreslå över veckor (t.ex. 36-40,42)", "36-40")
    d1, d2, d3 = st.columns(3)
    with d1:
        days_allowed = st.multiselect(
            "Tillåtna dagar (förslag)", options=["Mån", "Tis", "Ons", "Tors", "Fre", "Lör", "Sön"], default=["Mån", "Tis", "Ons", "Tors", "Fre"]
        )
    with d2:
        window_start = st.text_input("Dagsfönster start", "08:00")
    with d3:
        window_end = st.text_input("Dagsfönster slut", "18:00")
    submitted = st.form_submit_button("Kontrollera & föreslå")

if submitted:
    try:
        # Stötta både multiselect (lista) och fritext
        if isinstance(prop_groups, list):
            gset = {str(g).strip().upper() for g in prop_groups if str(g).strip()}
            groups_label = ";".join(sorted(gset))
        else:
            gset = parse_program(prop_groups)
            groups_label = ";".join(sorted(gset))
        start_t = parse_time_str(prop_start)
        end_t = parse_time_str(prop_end)
        # mappa UI-dag tillbaka till parsern
        day_map_ui = {"Mån":"mån","Tis":"tis","Ons":"ons","Tors":"tors","Fre":"fre","Lör":"lör","Sön":"sön"}
        all_conflicts = []
        for _d in (prop_days or []):
            d_idx = parse_day(day_map_ui[_d])
            all_conflicts.extend(check_conflict_in_db(gset, d_idx, start_t, end_t, int(prop_week), prop_sem))
        if all_conflicts:
            days_label = ", ".join(prop_days) if prop_days else "(inga dagar valda)"
            st.error(f"❌ Krock(ar) för {groups_label} vecka {prop_week} på: {days_label}")
            st.dataframe(pd.DataFrame(all_conflicts, columns=[
                "kurskod", "program", "veckonummer", "veckodag", "start", "slut"
            ]), use_container_width=True)
        else:
            st.success("✅ Ingen krock – passet är ledigt för valda veckodagar.")

        # Förslag
        st.markdown("**Förslag (krockfritt för valda program):**")
        weeks_iter = parse_weeks(sug_weeks)
        sched = load_schedule_from_db(prop_sem)
        free = sched.find_free_slots(
            groups=gset,
            duration_min=int(sug_duration),
            weeks=weeks_iter,
            days_allowed={parse_day(day_map_ui[d]) for d in days_allowed},
            day_window=(parse_time_str(window_start), parse_time_str(window_end)),
            granularity_min=30,
        )
        if not free:
            st.warning("Inga lediga tider hittades med valda filter.")
        else:
            out = pd.DataFrame(
                [
                    {"vecka": w, "dag": INT_TO_DAY[d], "start": time_to_str(s), "slut": time_to_str(e)}
                    for (w, d, s, e) in free
                ]
            )
            st.dataframe(out, use_container_width=True)
    except Exception as e:
        st.exception(e)

st.markdown("---")

# Hjälp
with st.expander("ℹ️ Tips & anmärkningar"):
    st.markdown(
        """
        - Ladda upp flera filer över tid för att bygga upp databas per **termin**.
        - Använd **program** som MTBG, VAKT, NGMV (semikolon för gemensamma pass).
        - Fält i Excel/CSV ska heta: **kurskod, program, veckodag, start, slut, veckonummer, termin**.
        - "Föreslå över veckor" accepterar intervall och listor, t.ex. `36-40,42`.
        - Lägg gärna till **lokaler/lärare** senare: utöka tabellen och indexera likt programmen.
        """
    )
