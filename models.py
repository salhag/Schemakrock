# models.py
# ----------------------------------------------------
# Datamodeller och schemaintervall-beräkningar

from __future__ import annotations

from dataclasses import dataclass
from datetime import time, timedelta, datetime, date
from typing import Iterable, List, Set, Tuple, Dict, FrozenSet

from parsing import (
    parse_program,
    parse_weeks,
    parse_time_str,
    time_to_str,
    overlaps,
)


# ---------------------- Datamodeller ----------------------

@dataclass(frozen=True)
class Event:
    """
    Normaliserad representation av en schemarad.
    - times (start/end) är datetime.time
    - groups/teachers är normaliserade (VERSALER) och semantik: mängder
    - weeks är en mängd veckonummer (int)
    """
    course: str
    groups: FrozenSet[str]
    day: int                # 0 = Mån ... 6 = Sön
    start: time
    end: time
    weeks: FrozenSet[int]
    teachers: FrozenSet[str]  # kan vara tom mängd om okänt


def events_from_db_rows(rows: Iterable[Tuple[str, str, int, str, str, str, str, str]]) -> List[Event]:
    """
    Bygger Event-objekt från DB-rader:
      (course, groups, day, start, end, weeks, semester, teacher)
    Obs: semester används inte i Event (hanteras i appens filter).
    """
    evs: List[Event] = []
    for course, groups, day, start, end, weeks, _semester, teacher in rows:
        evs.append(
            Event(
                course=str(course),
                groups=frozenset(parse_program(groups)),
                day=int(day),
                start=parse_time_str(start),
                end=parse_time_str(end),
                weeks=frozenset(parse_weeks(weeks)),
                teachers=frozenset(parse_program(teacher)),
            )
        )
    return evs


# ---------------------- Schedule & lediga tider ----------------------

class Schedule:
    """
    Indexerar events per (program -> vecka -> dag) för att snabbt hitta lediga luckor.
    Används av "Förslag (krockfritt)" i appen.
    """
    def __init__(self, events: Iterable[Event]):
        self.events = list(events)

        # index: program -> vecka -> dag -> list[(start, end)]
        self.index: Dict[str, Dict[int, Dict[int, List[Tuple[time, time]]]]] = {}
        for ev in self.events:
            for g in ev.groups:
                self.index.setdefault(g, {})
                for w in ev.weeks:
                    self.index[g].setdefault(w, {}).setdefault(ev.day, []).append((ev.start, ev.end))

        # sortera intervall för deterministisk scanning
        for g in self.index:
            for w in self.index[g]:
                for d in self.index[g][w]:
                    self.index[g][w][d].sort()

    def find_free_slots(
        self,
        groups: Set[str],
        duration_min: int,
        weeks: Iterable[int],
        days_allowed: Set[int] = {0, 1, 2, 3, 4},
        day_window: Tuple[time, time] = (time(8, 0), time(18, 0)),
        granularity_min: int = 30,
    ) -> List[Tuple[int, int, time, time]]:
        """
        Returnerar kandidater (vecka, dag, start, slut) som inte överlappar
        något event för något av valda programmen.
        - Pass som bara möts på gränsen (t.ex. 10:00–12:00 och 12:00–14:00) räknas INTE som krock.
        """
        dur = timedelta(minutes=duration_min)
        step = timedelta(minutes=granularity_min)
        start_bound, end_bound = day_window
        cands: List[Tuple[int, int, time, time]] = []
        today = date.today()  # referensdag för att bygga datetime

        for w in weeks:
            for d in days_allowed:
                # samla upptagna intervall för alla program
                busy: List[Tuple[time, time]] = []
                for g in groups:
                    busy.extend(self.index.get(g, {}).get(w, {}).get(d, []))
                busy.sort()

                # (valfritt) merge om överlapp inom samma program ger längre block
                merged: List[Tuple[time, time]] = []
                for s, e in busy:
                    if not merged or s >= merged[-1][1]:
                        merged.append((s, e))
                    else:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], e))

                cur = datetime.combine(today, start_bound)
                end_dt = datetime.combine(today, end_bound)

                def is_free(s_t: time, e_t: time) -> bool:
                    for bs, be in merged:
                        if overlaps(s_t, e_t, bs, be):
                            return False
                    return True

                while cur + dur <= end_dt:
                    s = cur.time()
                    e = (cur + dur).time()
                    if is_free(s, e):
                        cands.append((w, d, s, e))
                    cur += step

        return cands
