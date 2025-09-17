# app.py — Streamlit UI för Schemaplanerare (modulär version)
# -----------------------------------------------------------
# Kräver lokala moduler:
#   parsing.py, db.py, models.py, conflicts.py
#
# Kör:
#   pip install streamlit pandas openpyxl
#   streamlit run app.py

from __future__ import annotations

from datetime import time
from typing import Set

import pandas as pd
import streamlit as st

from parsing import (
    parse_time_str,
    time_to_str,
    parse_weeks,
    parse_day,
    INT_TO_DAY,
)
from db import (
    init_db,
    ensure_teacher_column,
    bulk_insert_events,
    normalize_columns,
    fetch_semesters,
    list_program_tokens,
    query_events,
    erase_all,
    erase_by_semester,
    erase_by_program,
    erase_by_course,
    erase_by_ids,
    normalize_db_times,
    fetch_events_for_semester,
)
from models import events_from_db_rows, Schedule
from conflicts import (
    compute_db_collisions,
    compute_teacher_collisions,
    check_conflict_in_db,
)


# ---------------------- Streamlit config ----------------------
st.set_page_config(page_title="Schemaläggningshjälp", page_icon="📅", layout="wide")
st.title("📅 Schemaplanerare")
st.subheader("Ett verktyg för terminsplanering med kontroll av schemakrockar och förslag på lediga tider")
st.markdown("av _Salar Haghighatafshar_, universitetslektor vid Högskolan Kristianstad")

# Init DB + säkerställ lärarkolumn (bakåtkompatibilitet)
init_db()
ensure_teacher_column()


# ---------------------- Sidofält: import & DB-hantering ----------------------
with st.sidebar:
    st.header("Ladda upp schemafil")
    st.caption("Accepterar CSV eller Excel med rubrikerna: **kurskod, program, veckodag, start, slut, veckonummer, termin**, (valfritt) **lärare**")
    up = st.file_uploader("CSV eller Excel", type=["csv", "xlsx", "xls"])
    if st.button("Importera till databas", use_container_width=True, disabled=up is None):
        try:
            df = pd.read_csv(up) if up.name.lower().endswith(".csv") else pd.read_excel(up)
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


# ---------------------- Utforskare ----------------------
st.subheader("Utforska & redigera befintligt schema")
available_semesters = fetch_semesters()
col1, col2 = st.columns(2)
with col1:
    sem_sel = st.selectbox("Termin", options=available_semesters or ["(inga data)"])
with col2:
    prog_text = st.text_input("Filtrera på program (separera med semikolon)", "")
    prog_filter: Set[str] | None = {g.strip() for g in prog_text.split(";") if g.strip()} or None

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


# ---------------------- Krockrapport – program ----------------------
st.subheader("Krockrapport – alla schemarader i databasen (program)")

colk1, colk2, colk3, colk4 = st.columns([2, 2, 1, 1])
with colk1:
    rep_sem = st.selectbox("Termin (rapport)", options=available_semesters or ["(inga data)"])
with colk2:
    rep_prog_text = st.text_input("Filtrera på program (semikolon, tomt = alla)", "")
with colk3:
    rep_days = st.multiselect("Veckodagar", options=["Mån", "Tis", "Ons", "Tors", "Fre", "Lör", "Sön"],
                              default=["Mån", "Tis", "Ons", "Tors", "Fre", "Lör", "Sön"])
with colk4:
    rep_teacher_text = st.text_input("Lärare (semikolon, tomt = alla)", "")

run_report = st.button("Visa krockar (program)")
if run_report and available_semesters:
    rep_filter = {g.strip().upper() for g in rep_prog_text.split(";") if g.strip()} or None
    day_map_ui = {"Mån": 0, "Tis": 1, "Ons": 2, "Tors": 3, "Fre": 4, "Lör": 5, "Sön": 6}
    rep_days_set = {day_map_ui[d] for d in rep_days} if rep_days else None
    rep_teacher_set = {t.strip().upper() for t in rep_teacher_text.split(";") if t.strip()} or None

    rep_df = compute_db_collisions(rep_sem, rep_filter, rep_days_set, rep_teacher_set)
    if rep_df.empty:
        st.info("Inga krockar hittades för vald termin/filtrering.")
    else:
        st.dataframe(rep_df, use_container_width=True)
        with st.expander("Summering per program och veckonummer"):
            summary = (
                rep_df.groupby(["program", "veckonummer"])
                .size()
                .reset_index(name="antal_krockar")
                .sort_values(["program", "veckonummer"])
            )
            st.dataframe(summary, use_container_width=True)
        csv_bytes = rep_df.to_csv(index=False).encode("utf-8")
        st.download_button("Ladda ner krockrapport (CSV)", data=csv_bytes,
                           file_name="krockrapport_program.csv", mime="text/csv")


st.markdown("---")


# ---------------------- Krockrapport – lärare ----------------------
st.subheader("Krockrapport – lärare (dubbelbokning)")

colt1, colt2, colt3 = st.columns([2, 2, 1])
with colt1:
    t_sem = st.selectbox("Termin (lärarrapport)", options=available_semesters or ["(inga data)"])
with colt2:
    t_days = st.multiselect("Veckodagar", options=["Mån", "Tis", "Ons", "Tors", "Fre", "Lör", "Sön"],
                            default=["Mån", "Tis", "Ons", "Tors", "Fre", "Lör", "Sön"])
with colt3:
    t_teachers = st.text_input("Lärare (semikolon, tomt = alla)", "")

run_teacher_report = st.button("Visa lärarkrockar")
if run_teacher_report and available_semesters:
    day_map_ui = {"Mån": 0, "Tis": 1, "Ons": 2, "Tors": 3, "Fre": 4, "Lör": 5, "Sön": 6}
    t_days_set = {day_map_ui[d] for d in t_days} if t_days else None
    t_teachers_set = {x.strip().upper() for x in t_teachers.split(";") if x.strip()} or None

    t_df = compute_teacher_collisions(t_sem, t_days_set, t_teachers_set)
    if t_df.empty:
        st.info("Inga lärardubbelbokningar hittades för vald termin/filtrering.")
    else:
        st.dataframe(t_df, use_container_width=True)
        csv_bytes = t_df.to_csv(index=False).encode("utf-8")
        st.download_button("Ladda ner lärarkrockar (CSV)", data=csv_bytes,
                           file_name="krockrapport_larare.csv", mime="text/csv")


st.markdown("---")


# ---------------------- Föreslaget pass + Förslag ----------------------
st.subheader("Kontrollera ett föreslaget pass & få förslag")

with st.form("proposal_form"):
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        prop_course = st.text_input("Kurskod", "NYTT-PASS")
        # Programval från DB om möjligt, annars fritext
        _prog_opts = list_program_tokens()
        if _prog_opts:
            prop_groups = st.multiselect(
                "Program (välj en eller flera)", options=_prog_opts,
                default=_prog_opts[:1]
            )
        else:
            prop_groups = st.text_input("Program (t.ex. MTBG;NGMV)", "MTBG")
    with c2:
        prop_sem = st.selectbox("Termin", options=available_semesters or ["2025-HT"],
                                index=0 if available_semesters else 0)
        # Flera veckodagar för krockkontroll
        prop_days = st.multiselect(
            "Veckodagar (krockkontroll)",
            options=["Mån", "Tis", "Ons", "Tors", "Fre", "Lör", "Sön"],
            default=["Mån", "Tis", "Ons", "Tors", "Fre"],
        )
    with c3:
        prop_start = st.text_input("Start (HH:MM)", "08:00")
        prop_end = st.text_input("Slut (HH:MM)", "17:00")
        prop_teachers = st.text_input("Lärare (valfritt, semikolon)", "")

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
            "Tillåtna dagar (förslag)",
            options=["Mån", "Tis", "Ons", "Tors", "Fre", "Lör", "Sön"],
            default=["Mån", "Tis", "Ons", "Tors", "Fre"],
        )
    with d2:
        window_start = st.text_input("Dagsfönster start", "08:00")
    with d3:
        window_end = st.text_input("Dagsfönster slut", "18:00")

    submitted = st.form_submit_button("Kontrollera & föreslå")

if submitted:
    try:
        # Normalisera programval från UI
        if isinstance(prop_groups, list):
            gset = {str(g).strip().upper() for g in prop_groups if str(g).strip()}
            groups_label = ";".join(sorted(gset))
        else:
            from parsing import parse_program
            gset = parse_program(prop_groups)
            groups_label = ";".join(sorted(gset))

        start_t = parse_time_str(prop_start)
        end_t = parse_time_str(prop_end)
        day_map_ui = {"Mån": "mån", "Tis": "tis", "Ons": "ons", "Tors": "tors", "Fre": "fre", "Lör": "lör", "Sön": "sön"}
        tset = {s.strip().upper() for s in prop_teachers.split(";") if s.strip()} if prop_teachers else set()

        # Krockkontroll för alla valda veckodagar
        all_conflicts = []
        for _d in (prop_days or []):
            d_idx = parse_day(day_map_ui[_d])
            all_conflicts.extend(
                check_conflict_in_db(
                    semester=prop_sem,
                    groups=gset,
                    day=d_idx,
                    start=start_t,
                    end=end_t,
                    week=int(prop_week),
                    teachers=tset if tset else None,
                )
            )

        if all_conflicts:
            days_label = ", ".join(prop_days) if prop_days else "(inga dagar valda)"
            st.error(f"❌ Krock(ar) för {groups_label} vecka {prop_week} på: {days_label}")
            st.dataframe(
                pd.DataFrame(
                    all_conflicts,
                    columns=["kurskod", "program", "lärare", "veckonummer", "veckodag", "start", "slut"],
                ),
                use_container_width=True,
            )
        else:
            st.success("✅ Ingen krock – passet är ledigt för valda veckodagar.")

        # Förslag på lediga tider (program-baserat)
        st.markdown("**Förslag (krockfritt för valda program):**")
        # Ladda schema från DB → Schedule
        rows = fetch_events_for_semester(prop_sem)
        sched = Schedule(events_from_db_rows(rows))
        free = sched.find_free_slots(
            groups=gset,
            duration_min=int(sug_duration),
            weeks=parse_weeks(sug_weeks),
            days_allowed={parse_day(day_map_ui[d]) for d in days_allowed},
            day_window=(parse_time_str(window_start), parse_time_str(window_end)),
            granularity_min=30,
        )
        if not free:
            st.warning("Inga lediga tider hittades med valda filter.")
        else:
            out = pd.DataFrame(
                [{"vecka": w, "dag": INT_TO_DAY[d], "start": time_to_str(s), "slut": time_to_str(e)} for (w, d, s, e) in free]
            )
            st.dataframe(out, use_container_width=True)

    except Exception as e:
        st.exception(e)


st.markdown("---")

with st.expander("ℹ️ Tips & anmärkningar"):
    st.markdown(
        """
        - Ladda upp flera filer över tid för att bygga upp databas per **termin**.
        - Använd **program** som MTBG, VAKT, NGMV (semikolon för gemensamma pass).
        - Valfri kolumn **lärare** kan anges, även semikolonavgränsad vid team-teaching.
        - Fält i Excel/CSV ska heta: **kurskod, program, veckodag, start, slut, veckonummer, termin**, (valfritt) **lärare**.
        - "Föreslå över veckor" accepterar intervall och listor, t.ex. `36-40,42`.
        - Pass som möts exakt i gränsen (t.ex. 10:00–12:00 och 12:00–14:00) räknas **inte** som krock.
        - Lägg gärna till **lokaler** senare: utöka tabellen och indexera likt program/lärare.
        """
    )
