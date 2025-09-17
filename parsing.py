# parsing.py
# ----------------------------------------------------
# Robust parsning och hjälp-funktioner för schemaplaneraren

from datetime import time
from typing import Iterable, Set

import pandas as pd


# ---------------------- Dagar/Tider ----------------------

DAY_TO_INT = {
    # Svenska varianter
    "mån": 0, "mandag": 0, "måndag": 0, "man": 0,
    "tis": 1, "tisdag": 1,
    "ons": 2, "onsdag": 2,
    "tor": 3, "tors": 3, "torsdag": 3,
    "fre": 4, "fredag": 4,
    "lor": 5, "lör": 5, "lordag": 5, "lördag": 5,
    "son": 6, "sön": 6, "sondag": 6, "söndag": 6,
    # Engelska
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}

INT_TO_DAY = {0: "Mån", 1: "Tis", 2: "Ons", 3: "Tors", 4: "Fre", 5: "Lör", 6: "Sön"}


def _normalize_ascii(s: str) -> str:
    """Ersätt svenska tecken för robust jämförelse."""
    return (
        s.replace("å", "a").replace("ä", "a").replace("ö", "o")
         .replace("Å", "A").replace("Ä", "A").replace("Ö", "O")
    )


def parse_day(value) -> int:
    """
    Accepterar 'mån', 'måndag', 'Mon', 0..6, 1..7 → returnerar 0..6 (mån=0).
    """
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
    """
    Accepterar:
      - '09:00', '09:00:00', '9.00', '9'
      - int/float (9 → 09:00, 13.5 → 13:30)
      - Excel-dagsfraktion 0..1 (t.ex. 0.5 → 12:00)
      - pandas.Timestamp
    """
    # pandas Timestamp
    if isinstance(x, pd.Timestamp):
        return time(x.hour, x.minute)

    # Numeriskt
    if isinstance(x, (int, float)):
        xf = float(x)
        # Excel-fraktion av dygn (0..~2 för säkerhets skull)
        if 0 <= xf < 2:
            total_seconds = int(round(xf * 24 * 3600))
            hh = (total_seconds // 3600) % 24
            mm = (total_seconds % 3600) // 60
            return time(hh, mm)
        # Annars tolka som timmar (ev. decimaler = minuter)
        if 0 <= xf < 24:
            hh = int(xf)
            mm = int(round((xf - hh) * 60))
            if mm == 60:
                hh = (hh + 1) % 24
                mm = 0
            return time(hh, mm)

    # Strängar
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
            hh = int(parts[0])
            mm = int(parts[1])
            return time(hh, mm)
    # HH.MM
    if "." in s:
        parts = s.split(".")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            hh = int(parts[0])
            mm = int(parts[1])
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
    """
    Dela på semikolon och normalisera till VERSALER för robust jämförelse.
    Används för både program och lärare (flera namn tillåtna).
    """
    return {str(g).strip().upper() for g in str(s).split(";") if str(g).strip()}


def programs_to_str(gs: Iterable[str]) -> str:
    """Sorterad & semikolonavgränsad sträng av unika värden."""
    return ";".join(sorted(set(gs)))


def parse_weeks(s: str) -> Set[int]:
    """
    Tål format som '36-38, 40', 'v36–38', 'veckor 36-38', 'W36'.
    Returnerar mängd heltal (veckonummer).
    """
    import re

    text = str(s).lower().replace("\u2013", "-")
    # ta bort ord som kan förekomma
    text = (
        text.replace("vecka", "")
            .replace("veckor", "")
            .replace("v.", "v")
            .replace("v", "")
            .replace("w", "")
            .replace(" ", "")
    )

    weeks: Set[int] = set()

    # intervall: 36-38
    for a, b in re.findall(r"(\d{1,2})\s*-\s*(\d{1,2})", text):
        a, b = int(a), int(b)
        lo, hi = (a, b) if a <= b else (b, a)
        weeks.update(range(lo, hi + 1))

    # ta bort intervallen så ensamma tal inte dubblas
    text_no_ranges = re.sub(r"\d{1,2}\s*-\s*\d{1,2}", ",", text)

    # ensamma tal
    for num in re.findall(r"\d{1,2}", text_no_ranges):
        weeks.add(int(num))

    return weeks


def overlaps(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    """
    Äkta överlapp:
      - True om intervallen faktiskt överlappar.
      - Pass som bara möts i gränsen (t.ex. 08:00–10:00 och 10:00–12:00) räknas INTE som krock.
    """
    return (a_start < b_end) and (b_start < a_end) and not (a_end == b_start or b_end == a_start)
