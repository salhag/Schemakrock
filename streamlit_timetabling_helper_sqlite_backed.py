# app.py — Streamlit Timetabling Helper (SQLite-backed)
# ----------------------------------------------------
# Features
# - Upload CSV/Excel timetables → stored in SQLite
# - View/search by semester & group (MTBG, VAKT, NGMV or any label)
# - Check a proposed event for conflicts vs DB
# - Suggest free slots (no collisions across selected groups)
#
# Run:
# pip install streamlit pandas openpyxl
#   streamlit run app.py
#
# CSV/Excel columns (header row required):
#   course, groups, day, start, end, weeks, semester
# Where:
#   groups  = e.g. "MTBG" or "MTBG;NGMV" (semicolon-separated for joint sessions)
#   day     = Mon,Tue,Wed,Thu,Fri,Sat,Sun
#   start   = HH:MM (24h)
#   end     = HH:MM (24h)
#   weeks   = comma-separated list/ranges (e.g., "36-38,40,42")
#   semester= e.g., "2025-Fall" (used to isolate schedules)

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import time, timedelta, datetime, date
from typing import Iterable, List, Set, Tuple, Dict

import pandas as pd
import streamlit as st

DB_PATH = "timetable.db"
DAY_MAP = {"Mon":0, "Tue":1, "Wed":2, "Thu":3, "Fri":4, "Sat":5, "Sun":6}
INV_DAY_MAP = {v:k for k,v in DAY_MAP.items()}

# ---------------------- Utilities ----------------------
def parse_time_str(x) -> time:
    """Accepts '09:00', '09:00:00', '9.00', pandas.Timestamp, or Excel time fraction."""
    import pandas as pd
    from datetime import time, timedelta

    # pandas Timestamp or datetime
    if isinstance(x, pd.Timestamp):
        return time(x.hour, x.minute)
    # Excel / pandas numeric fraction of a day (e.g., 0.375 -> 09:00)
    if isinstance(x, (int, float)) and 0 <= x < 2:  # allow 0..1 (some files have >1 by mistake)
        total_seconds = int(round(float(x) * 24 * 3600))
        hh = (total_seconds // 3600) % 24
        mm = (total_seconds % 3600) // 60
        return time(hh, mm)

    s = str(x).strip()
    # Try common string formats
    # 1) HH:MM or HH:MM:SS
    if ":" in s:
        parts = s.split(":")
        if len(parts) >= 2:
            hh = int(parts[0])
            mm = int(parts[1])
            return time(hh, mm)
    # 2) HH.MM
    if "." in s:
        parts = s.split(".")
        if len(parts) >= 2 and all(p.isdigit() for p in parts[:2]):
            hh = int(parts[0]); mm = int(parts[1])
            return time(hh, mm)

    # 3) Fallback: try pandas to_datetime (last resort)
    try:
        ts = pd.to_datetime(s)
        return time(ts.hour, ts.minute)
    except Exception:
        pass

    raise ValueError(f"Unrecognized time format: {x!r}")

def time_to_str(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def parse_groups(s: str) -> Set[str]:
    return {g.strip() for g in str(s).split(";") if str(g).strip()}


def groups_to_str(gs: Iterable[str]) -> str:
    return ";".join(sorted(set(gs)))


def parse_weeks(s: str) -> Set[int]:
    weeks: Set[int] = set()
    for part in str(s).replace("\u2013","-").split(","):
        seg = part.strip()
        if not seg:
            continue
        if "-" in seg:
            a,b = seg.split("-")
            a,b = int(a), int(b)
            lo, hi = (a,b) if a<=b else (b,a)
            weeks.update(range(lo, hi+1))
        else:
            weeks.add(int(seg))
    return weeks


def weeks_to_str(weeks: Iterable[int]) -> str:
    return ",".join(str(w) for w in sorted(set(weeks)))


def overlaps(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    return (a_start < b_end) and (b_start < a_end)

# ---------------------- Database ----------------------
def init_db():
    with closing(sqlite3.connect(DB_PATH)) as con, con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course   TEXT NOT NULL,
                groups   TEXT NOT NULL,  -- semicolon-separated
                day      INTEGER NOT NULL, -- 0=Mon .. 6=Sun
                start    TEXT NOT NULL, -- HH:MM
                end      TEXT NOT NULL, -- HH:MM
                weeks    TEXT NOT NULL, -- e.g. 36-38,40
                semester TEXT NOT NULL
            )
            """
        )


def bulk_insert_events(df: pd.DataFrame):
    # Expect normalized DataFrame columns: course, groups, day, start, end, weeks, semester
    with closing(sqlite3.connect(DB_PATH)) as con, con:
        con.executemany(
            """
            INSERT INTO events (course, groups, day, start, end, weeks, semester)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    str(r["course"]).strip(),
                    groups_to_str(parse_groups(r["groups"])),
                    parse_day(r["day"]),
                    time_to_str(parse_time_str(r["start"])),  # normalize to 'HH:MM'
                    time_to_str(parse_time_str(r["end"])),    # normalize to 'HH:MM'
                    str(r["weeks"]).strip(),
                    str(r["semester"]).strip(),
                )
                for _, r in df.iterrows()
            ],
        )


def fetch_semesters() -> List[str]:
    with closing(sqlite3.connect(DB_PATH)) as con:
        rows = con.execute("SELECT DISTINCT semester FROM events ORDER BY semester").fetchall()
    return [r[0] for r in rows]


def query_events(semester: str, groups_filter: Set[str] | None = None) -> pd.DataFrame:
    with closing(sqlite3.connect(DB_PATH)) as con:
        if groups_filter:
            like_clauses = [f"groups LIKE ?" for _ in groups_filter]
            sql = f"SELECT id,course,groups,day,start,end,weeks,semester FROM events WHERE semester=? AND (" + " OR ".join(like_clauses) + ") ORDER BY day, start"
            params = [semester] + [f"%{g}%" for g in groups_filter]
        else:
            sql = "SELECT id,course,groups,day,start,end,weeks,semester FROM events WHERE semester=? ORDER BY day, start"
            params = [semester]
        df = pd.read_sql_query(sql, con, params=params)
    if not df.empty:
        df["day_name"] = df["day"].map(INV_DAY_MAP)
        df = df[["id","course","groups","day_name","start","end","weeks","semester"]]
    return df

# ---------------------- Conflict & Suggestion Engine ----------------------
@dataclass(frozen=True)
class Event:
    course: str
    groups: frozenset[str]
    day: int
    start: time
    end: time
    weeks: frozenset[int]


class Schedule:
    def __init__(self, events: Iterable[Event]):
        self.events = list(events)
        self.index: Dict[str, Dict[int, Dict[int, List[Tuple[time,time]]]]] = {}
        for ev in self.events:
            for g in ev.groups:
                self.index.setdefault(g, {})
                for w in ev.weeks:
                    self.index[g].setdefault(w, {}).setdefault(ev.day, []).append((ev.start, ev.end))
        # sort intervals
        for g in self.index:
            for w in self.index[g]:
                for d in self.index[g][w]:
                    self.index[g][w][d].sort()

    def group_collisions(self, group: str) -> List[Tuple[int,int,time,time,Event,Event]]:
        cols=[]
        for w, day_map in self.index.get(group, {}).items():
            for d, intervals in day_map.items():
                for i in range(len(intervals)):
                    for j in range(i+1,len(intervals)):
                        s1,e1 = intervals[i]; s2,e2 = intervals[j]
                        if overlaps(s1,e1,s2,e2):
                            ev1 = next(ev for ev in self.events if group in ev.groups and w in ev.weeks and ev.day==d and ev.start==s1 and ev.end==e1)
                            ev2 = next(ev for ev in self.events if group in ev.groups and w in ev.weeks and ev.day==d and ev.start==s2 and ev.end==e2)
                            cols.append((w,d,max(s1,s2),min(e1,e2),ev1,ev2))
        return cols

    def find_free_slots(
        self,
        groups: Set[str],
        duration_min: int,
        weeks: Iterable[int],
        days_allowed: Set[int] = {0,1,2,3,4},
        day_window: Tuple[time,time] = (time(8,0), time(18,0)),
        granularity_min: int = 30,
    ) -> List[Tuple[int,int,time,time]]:
        dur = timedelta(minutes=duration_min)
        step = timedelta(minutes=granularity_min)
        start_bound, end_bound = day_window
        cands: List[Tuple[int,int,time,time]] = []
        today = date.today()
        for w in weeks:
            for d in days_allowed:
                busy: List[Tuple[time,time]] = []
                for g in groups:
                    busy.extend(self.index.get(g, {}).get(w, {}).get(d, []))
                busy.sort()
                merged: List[Tuple[time,time]] = []
                for s,e in busy:
                    if not merged or s >= merged[-1][1]:
                        merged.append((s,e))
                    else:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], e))
                cur = datetime.combine(today, start_bound)
                end_dt = datetime.combine(today, end_bound)
                def is_free(s_t: time, e_t: time) -> bool:
                    for bs,be in merged:
                        if overlaps(s_t, e_t, bs, be):
                            return False
                    return True
                while cur + dur <= end_dt:
                    s = cur.time(); e = (cur + dur).time()
                    if is_free(s, e):
                        cands.append((w,d,s,e))
                    cur += step
        return cands


def load_schedule_from_db(semester: str) -> Schedule:
    with closing(sqlite3.connect(DB_PATH)) as con:
        rows = con.execute(
            "SELECT course, groups, day, start, end, weeks FROM events WHERE semester=?",
            (semester,)
        ).fetchall()
    evs: List[Event] = []
    for course, g_str, day, s, e, weeks in rows:
        evs.append(
            Event(
                course=str(course),
                groups=frozenset(parse_groups(g_str)),
                day=int(day),
                start=parse_time_str(s),
                end=parse_time_str(e),
                weeks=frozenset(parse_weeks(weeks)),
            )
        )
    return Schedule(evs)


def check_conflict_in_db(groups: Set[str], day: int, start: time, end: time, week: int, semester: str) -> List[Tuple[str,str,str,str]]:
    """Return list of conflicting rows: (course, groups, start, end)."""
    with closing(sqlite3.connect(DB_PATH)) as con:
        rows = con.execute(
            "SELECT course, groups, start, end, weeks FROM events WHERE semester=? AND day=?",
            (semester, day),
        ).fetchall()
    conflicts = []
    for course, g_str, s, e, weeks in rows:
        g_set = parse_groups(g_str)
        if groups & g_set and week in parse_weeks(weeks):
            if overlaps(parse_time_str(s), parse_time_str(e), start, end):
                conflicts.append((course, g_str, s, e))
    return conflicts

# ---------------------- Streamlit UI ----------------------
st.set_page_config(page_title="Timetabling Helper", page_icon="📅", layout="wide")
st.title("📅 Timetabling Helper (SQLite)")

init_db()

with st.sidebar:
    st.header("Upload timetable")
    sem_default = "2025-Fall"
    semester = st.text_input("Semester label", sem_default)
    up = st.file_uploader("CSV or Excel file", type=["csv","xlsx","xls"])
    if st.button("Import to database", use_container_width=True, disabled=up is None or not semester.strip()):
        try:
            if up.name.lower().endswith(".csv"):
                df = pd.read_csv(up)
            else:
                df = pd.read_excel(up)
            req = ["course","groups","day","start","end","weeks","semester"]
            missing = [c for c in req if c not in df.columns]
            if missing:
                st.error(f"Missing required columns: {', '.join(missing)}")
            else:
                # Basic normalization: trim spaces
                for c in req:
                    df[c] = df[c].astype(str).str.strip()
                bulk_insert_events(df[req])
                st.success(f"Imported {len(df)} rows into '{semester}'.")
        except Exception as e:
            st.exception(e)

st.markdown("---")

# Explorer
st.subheader("Explore existing timetable")
available_semesters = fetch_semesters()
col1, col2 = st.columns(2)
with col1:
    sem_sel = st.selectbox("Semester", options=available_semesters or ["(no data)"])
with col2:
    grp_text = st.text_input("Filter by groups (semicolon-separated, optional)", "")
    grp_filter = {g.strip() for g in grp_text.split(";") if g.strip()} or None

if available_semesters:
    df_view = query_events(sem_sel, grp_filter)
    st.dataframe(df_view, use_container_width=True, hide_index=True)
else:
    st.info("No data yet. Upload a CSV/Excel in the sidebar.")

st.markdown("---")

# Conflict check form
st.subheader("Check a proposed event & get suggestions")
with st.form("proposal_form"):
    c1, c2, c3 = st.columns([2,1,1])
    with c1:
        prop_course = st.text_input("Course name", "New Session")
        prop_groups = st.text_input("Groups (e.g., MTBG;NGMV)", "MTBG")
    with c2:
        prop_sem = st.text_input("Semester", sem_sel if available_semesters else "2025-Fall")
        prop_day = st.selectbox("Day", options=list(DAY_MAP.keys()), index=0)
    with c3:
        prop_start = st.text_input("Start (HH:MM)", "09:00")
        prop_end   = st.text_input("End (HH:MM)",   "11:00")
    c4, c5, c6 = st.columns([1,1,2])
    with c4:
        prop_week = st.number_input("Week #", min_value=1, max_value=53, value=36, step=1)
    with c5:
        sug_duration = st.number_input("Suggest duration (min)", min_value=30, max_value=300, value=90, step=15)
    with c6:
        sug_weeks = st.text_input("Suggest over weeks (e.g., 36-40,42)", "36-40")
    d1, d2, d3 = st.columns(3)
    with d1:
        days_allowed = st.multiselect("Allowed days (suggest)", options=list(DAY_MAP.keys()), default=["Mon","Tue","Wed","Thu","Fri"])
    with d2:
        window_start = st.text_input("Day window start", "08:00")
    with d3:
        window_end = st.text_input("Day window end", "18:00")
    submitted = st.form_submit_button("Check & Suggest")

if submitted:
    try:
        gset = parse_groups(prop_groups)
        start_t = parse_time_str(prop_start)
        end_t   = parse_time_str(prop_end)
        conflicts = check_conflict_in_db(gset, parse_day(prop_day), start_t, end_t, int(prop_week), prop_sem)
        if conflicts:
            st.error(f"❌ Conflict(s) found for {prop_groups} in week {prop_week} on {prop_day}:")
            st.table(pd.DataFrame(conflicts, columns=["course","groups","start","end"]))
        else:
            st.success("✅ No conflicts — this slot is available.")

        # Suggestions
        st.markdown("**Suggestions (no collisions across selected groups):**")
        weeks_iter = parse_weeks(sug_weeks)
        sched = load_schedule_from_db(prop_sem)
        free = sched.find_free_slots(
            groups=gset,
            duration_min=int(sug_duration),
            weeks=weeks_iter,
            days_allowed={DAY_MAP[d] for d in days_allowed},
            day_window=(parse_time_str(window_start), parse_time_str(window_end)),
            granularity_min=30,
        )
        if not free:
            st.warning("No free slots found with the current filters.")
        else:
            out = pd.DataFrame([
                {
                    "week": w,
                    "day": INV_DAY_MAP[d],
                    "start": time_to_str(s),
                    "end": time_to_str(e),
                }
                for (w,d,s,e) in free
            ])
            st.dataframe(out, use_container_width=True)
    except Exception as e:
        st.exception(e)

st.markdown("---")

# Footer help
with st.expander("ℹ️ Tips & Notes"):
    st.markdown(
        """
        - Upload multiple files over time to build up your semester database.
        - Use **groups like MTBG, VAKT, NGMV** (semicolon for joint sessions).
        - "Suggest over weeks" accepts ranges and lists, e.g. `36-40,42`.
        - To extend the model with **rooms/teachers**, add columns to the DB and mirror the group-indexing logic per resource.
        """
    )
