# app.py — Streamlit UI för Schemaplanerare (modulär version, fixad)
# -----------------------------------------------------------

from __future__ import annotations

import pandas as pd
import streamlit as st
from typing import Set

from parsing import parse_time_str, time_to_str, parse_weeks, parse_day, INT_TO_DAY
from db import (
    init_db, ensure_teacher_column, bulk_insert_events, normalize_columns,
    fetch_semesters, list_program_tokens, query_events, erase_all,
    erase_by_semester, erase_by_program, erase_by_course, erase_by_ids,
    normalize_db_times, fetch_events_for_semester
)
from models import events_from_db_rows, Schedule
from conflicts import compute_db_collisions, compute_teacher_collisions, check_conflict_in_db


# ---------------------- Streamlit config ----------------------
st.set_page_config(page_title="Schemaläggningshjälp", page_icon="📅", layout="wide")
st.title("📅 Krockfritt")
st.subheader("Ett verktyg för terminsplanering med kontroll av schemakrockar och förslag på lediga tider")
st.markdown("av _Salar Haghighatafshar_, universitetslektor vid Högskolan Kristianstad")

init_db()
ensure_teacher_column()


# ---------------------- Sidofält ----------------------
with st.sidebar:
    st.header("Ladda upp schemafil")
    st.caption("Rubriker: kurskod, program, veckodag, start, slut, veckonummer, termin, (valfritt) lärare")
    up = st.file_uploader("CSV eller Excel", type=["csv", "xlsx", "xls"], key="file_upload")
    if st.button("Importera till databas", use_container_width=True, disabled=up is None, key="btn_import"):
        try:
            df = pd.read_csv(up) if up.name.lower().endswith(".csv") else pd.read_excel(up)
            bulk_insert_events(df)
            sems = ", ".join(sorted(normalize_columns(df)["semester"].astype(str).unique()))
            st.success(f"Importerade {len(df)} rader. Termer: {sems}")
        except Exception as e:
            st.exception(e)

    st.markdown("---")
    st.header("Hantera databas")
    if st.button("🗑️ Rensa ALLT", use_container_width=True, key="btn_erase_all"):
        erase_all()
        st.success("Databasen är tömd.")

    sem_to_erase = st.text_input("Ta bort per termin", "", key="erase_sem")
    if st.button("Ta bort termin", use_container_width=True, disabled=not sem_to_erase.strip(), key="btn_erase_sem"):
        erase_by_semester(sem_to_erase.strip())
        st.success(f"Tog bort termin: {sem_to_erase}")

    prog_list = list_program_tokens()
    if prog_list:
        prog_choice = st.selectbox("Ta bort per program (välj)", [""] + prog_list, key="erase_prog_sel")
        if st.button("Ta bort valt program", use_container_width=True, disabled=not prog_choice, key="btn_erase_prog"):
            erase_by_program(prog_choice)
            st.success(f"Tog bort alla pass för program: {prog_choice}")

    course_to_erase = st.text_input("Ta bort per kurskod", "", key="erase_course")
    if st.button("Ta bort kurskod", use_container_width=True, disabled=not course_to_erase.strip(), key="btn_erase_course"):
        erase_by_course(course_to_erase.strip())
        st.success(f"Tog bort alla pass för kurskod: {course_to_erase}")

    if st.button("Normalisera lagrade tider till HH:MM", use_container_width=True, key="btn_norm"):
        normalize_db_times()
        st.success("Tider normaliserade.")


# ---------------------- Utforskare ----------------------
st.markdown("---")
st.subheader("Utforska & redigera importerade scheman")
available_semesters = fetch_semesters()

col1, col2 = st.columns(2)
with col1:
    sem_sel = st.selectbox("Termin", options=available_semesters or ["(inga data)"], key="explore_sem")
with col2:
    prog_text = st.text_input("Filtrera på program (separera med semikolon)", "", key="explore_prog")
    prog_filter: Set[str] | None = {g.strip() for g in prog_text.split(";") if g.strip()} or None

if available_semesters:
    df_view = query_events(sem_sel, prog_filter)
    st.dataframe(df_view, use_container_width=True, hide_index=True)
    ids_to_delete = st.multiselect("Markera rader att ta bort (ID)", options=df_view["id"].tolist(), key="explore_delete")
    if st.button("Ta bort markerade rader", key="btn_explore_delete"):
        erase_by_ids(ids_to_delete)
        st.success(f"Tog bort {len(ids_to_delete)} rad(er). Ladda om tabellen ovan.")
else:
    st.info("Inga data ännu. Ladda upp dina schemafiler i sidofältet.")


# ---------------------- Krockrapport – program ----------------------
st.markdown("---")
st.subheader("Krockrapport – program")

colk1, colk2, colk3, colk4 = st.columns([2, 2, 1, 1])
with colk1:
    rep_sem = st.selectbox("Termin (rapport)", options=available_semesters or ["(inga data)"], key="rep_sem")
with colk2:
    rep_prog_text = st.text_input("Filtrera på program (semikolon, tomt = alla)", "", key="rep_prog_text")
with colk3:
    rep_days = st.multiselect("Veckodagar (program)", options=["Mån","Tis","Ons","Tors","Fre","Lör","Sön"],
                              default=["Mån","Tis","Ons","Tors","Fre"], key="rep_days")
with colk4:
    rep_teacher_text = st.text_input("Lärare (semikolon, tomt = alla)", "", key="rep_teacher_text")

if st.button("Visa programkrockar", key="btn_report_program") and available_semesters:
    rep_filter = {g.strip().upper() for g in rep_prog_text.split(";") if g.strip()} or None
    day_map_ui = {"Mån":0,"Tis":1,"Ons":2,"Tors":3,"Fre":4,"Lör":5,"Sön":6}
    rep_days_set = {day_map_ui[d] for d in rep_days} if rep_days else None
    rep_teacher_set = {t.strip().upper() for t in rep_teacher_text.split(";") if t.strip()} or None

    rep_df = compute_db_collisions(rep_sem, rep_filter, rep_days_set, rep_teacher_set)
    if rep_df.empty:
        st.info("Inga krockar hittades.")
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


# ---------------------- Krockrapport – lärare ----------------------
st.markdown("---")
st.subheader("Krockrapport – lärare")

colt1, colt2, colt3 = st.columns([2,2,1])
with colt1:
    t_sem = st.selectbox("Termin (lärarrapport)", options=available_semesters or ["(inga data)"], key="teacher_sem")
with colt2:
    t_days = st.multiselect("Veckodagar (lärare)", options=["Mån","Tis","Ons","Tors","Fre","Lör","Sön"],
                            default=["Mån","Tis","Ons","Tors","Fre"], key="teacher_days")
with colt3:
    t_teachers = st.text_input("Lärare (semikolon, tomt = alla)", "", key="teacher_text")

if st.button("Visa lärarkrockar", key="btn_report_teacher") and available_semesters:
    day_map_ui = {"Mån":0,"Tis":1,"Ons":2,"Tors":3,"Fre":4,"Lör":5,"Sön":6}
    t_days_set = {day_map_ui[d] for d in t_days} if t_days else None
    t_teachers_set = {x.strip().upper() for x in t_teachers.split(";") if x.strip()} or None

    t_df = compute_teacher_collisions(t_sem, t_days_set, t_teachers_set)
    if t_df.empty:
        st.info("Inga lärardubbelbokningar hittades.")
    else:
        st.dataframe(t_df, use_container_width=True)
        csv_bytes = t_df.to_csv(index=False).encode("utf-8")
        st.download_button("Ladda ner lärarkrockar (CSV)", data=csv_bytes,
                           file_name="krockrapport_larare.csv", mime="text/csv")


# ---------------------- Krockkontroll & Förslag (uppdelad i två delar) ----------------------
st.markdown("---")
# st.subheader("Kontroll av krockar och förslag på lediga tider")

col_left, col_right = st.columns(2, gap="large")

# ---------- Vänster: Kontroll av krockar för ett givet pass ----------
with col_left:
    st.markdown("### 🔎 Kontrollera krockar för ett föreslaget pass")

    with st.form("form_conflicts"):
        c1, c2 = st.columns([2, 1])
        with c1:
            prop_course_c = st.text_input("Kurskod", "NYTT-PASS", key="conf_course")
            _prog_opts = list_program_tokens()
            if _prog_opts:
                prop_groups_c = st.multiselect("Program", options=_prog_opts, default=_prog_opts[:1], key="conf_groups")
            else:
                prop_groups_c = st.text_input("Program", "MTBG", key="conf_groups_text")
        with c2:
            prop_sem_c = st.selectbox("Termin", options=available_semesters or ["2025-HT"], key="conf_sem")
            prop_week_c = st.number_input("Vecka #", min_value=1, max_value=53, value=36, step=1, key="conf_week")

        c3, c4 = st.columns(2)
        with c3:
            prop_days_c = st.multiselect(
                "Veckodagar (kontroll)",
                options=["Mån", "Tis", "Ons", "Tors", "Fre", "Lör", "Sön"],
                default=["Mån", "Tis", "Ons", "Tors", "Fre"],
                key="conf_days",
            )
            prop_teachers_c = st.text_input("Lärare (valfritt, semikolon)", "", key="conf_teachers")
        with c4:
            prop_start_c = st.text_input("Start (HH:MM)", "10:00", key="conf_start")
            prop_end_c   = st.text_input("Slut (HH:MM)",  "12:00", key="conf_end")

        submitted_conf = st.form_submit_button("Kontrollera krockar", use_container_width=True)

    if submitted_conf:
        try:
            from parsing import parse_program
            # Normalisera program
            if isinstance(prop_groups_c, list):
                gset_c = {str(g).strip().upper() for g in prop_groups_c if str(g).strip()}
            else:
                gset_c = parse_program(prop_groups_c)

            start_t_c = parse_time_str(prop_start_c)
            end_t_c   = parse_time_str(prop_end_c)
            day_map_ui = {"Mån":"mån","Tis":"tis","Ons":"ons","Tors":"tors","Fre":"fre","Lör":"lör","Sön":"sön"}
            tset_c = {s.strip().upper() for s in prop_teachers_c.split(";") if s.strip()}

            # Kolla alla valda dagar
            all_conflicts = []
            for _d in prop_days_c:
                d_idx = parse_day(day_map_ui[_d])
                all_conflicts.extend(
                    check_conflict_in_db(
                        semester=prop_sem_c,
                        groups=gset_c,
                        day=d_idx,
                        start=start_t_c,
                        end=end_t_c,
                        week=int(prop_week_c),
                        teachers=tset_c if tset_c else None,
                    )
                )

            if all_conflicts:
                st.error("❌ Krockar hittades")
                st.dataframe(pd.DataFrame(all_conflicts, columns=[
                    "kurskod", "program", "lärare", "veckonummer", "veckodag", "start", "slut"
                ]), use_container_width=True)
            else:
                st.success("✅ Ingen krock för det föreslagna passet.")
        except Exception as e:
            st.exception(e)


# ---------- Höger: Förslag på lediga tider ----------
with col_right:
    st.markdown("### 💡 Förslag på lediga tider (krockfritt för valda program)")

    with st.form("form_suggestions"):
        s1, s2 = st.columns([2, 1])
        with s1:
            # Återanvänd programlista
            _prog_opts_s = list_program_tokens()
            if _prog_opts_s:
                prop_groups_s = st.multiselect("Program (förslag)", options=_prog_opts_s,
                                               default=_prog_opts_s[:1], key="sug_groups")
            else:
                prop_groups_s = st.text_input("Program (förslag)", "MTBG", key="sug_groups_text")
        with s2:
            prop_sem_s = st.selectbox("Termin (förslag)", options=available_semesters or ["2025-HT"], key="sug_sem")

        s3, s4 = st.columns(2)
        with s3:
            sug_duration_s = st.number_input("Längd (min)", min_value=30, max_value=300, value=90, step=15, key="sug_duration2")
            sug_weeks_s = st.text_input("Veckor (t.ex. 36-40,42)", "36-40", key="sug_weeks2")
        with s4:
            days_allowed_s = st.multiselect("Tillåtna dagar",
                                            options=["Mån","Tis","Ons","Tors","Fre","Lör","Sön"],
                                            default=["Mån","Tis","Ons","Tors","Fre"], key="sug_days2")
            window_start_s = st.text_input("Dagsfönster start", "08:00", key="sug_win_start")
            window_end_s   = st.text_input("Dagsfönster slut",  "18:00", key="sug_win_end")

        submitted_sug = st.form_submit_button("Visa förslag", use_container_width=True)

    if submitted_sug:
        try:
            from parsing import parse_program
            # Normalisera program
            if isinstance(prop_groups_s, list):
                gset_s = {str(g).strip().upper() for g in prop_groups_s if str(g).strip()}
            else:
                gset_s = parse_program(prop_groups_s)

            # Ladda schema → bygg Schedule
            rows_s = fetch_events_for_semester(prop_sem_s)
            sched = Schedule(events_from_db_rows(rows_s))

            day_map_ui = {"Mån":0,"Tis":1,"Ons":2,"Tors":3,"Fre":4,"Lör":5,"Sön":6}
            free = sched.find_free_slots(
                groups=gset_s,
                duration_min=int(sug_duration_s),
                weeks=parse_weeks(sug_weeks_s),
                days_allowed={day_map_ui[d] for d in days_allowed_s},
                day_window=(parse_time_str(window_start_s), parse_time_str(window_end_s)),
                granularity_min=30,
            )
            if free:
                out = pd.DataFrame(
                    [{"vecka": w, "dag": INT_TO_DAY[d], "start": time_to_str(s), "slut": time_to_str(e)} for (w, d, s, e) in free]
                )
                st.dataframe(out, use_container_width=True)
            else:
                st.info("Inga lediga luckor hittades för valda inställningar.")
        except Exception as e:
            st.exception(e)



# ---------------------- Hjälp ----------------------
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
        - Vi kan lägga till **lokaler** senare: utöka tabellen och indexera likt program/lärare.
        """
    )
