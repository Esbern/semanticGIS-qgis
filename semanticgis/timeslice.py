"""Time in tables and registers: periods of statistics, versions of units, objects as they were on a date.

A statistics table counts people in the units valid at its period: a table of parishes for 2019 needs the parishes
of 2019, which DAGI keeps (bitemporal, from January 2018). This module reads periods (2025K4, 2025M03, 2025, …),
finds the column that holds them, picks the version of each unit valid on a date, and selects a register's objects
as they existed on a date (valid time, registration time, status roles). No QGIS imports, so it is tested without QGIS.
"""

import datetime as dt
import re
from dataclasses import dataclass

TZ_NAME = "Europe/Copenhagen"


def _date(y, m=1, d=1):
    try:
        return dt.date(int(y), int(m), int(d))
    except ValueError:
        return None


def _add_months(day, months):
    m = day.month - 1 + months
    return dt.date(day.year + m // 12, m % 12 + 1, 1)


PERIOD = re.compile(
    r"(?P<y>(?:18|19|20)\d\d)(?:"
    r"(?:K|Q|-Q)(?P<q>[1-4])|"                 # quarter: 2025K4 (Danmarks Statistik), 2025Q4, 2025-Q4
    r"(?:M|-)(?P<m>0?[1-9]|1[0-2])(?:-(?P<d>\d\d))?|"   # month: 2025M03, 2025-03; a day: 2025-03-01
    r"H(?P<h>[12])|"                           # half year: 2025H1
    r"U(?P<w>\d\d)|"                           # week: 2025U07
    r"(?P<span>[/-](?:18|19|20)\d\d)|"          # school or financial year 2024/2025, 2024-2025: from the first
    r")")


def period_range(value):
    """(first day, first day after) of a period written as 2025, 2025K4, 2025Q4, 2025M03, 2025-03, 2025-03-01, 2025H1,
    2025U07 or 2024/2025; None when the value is not a period."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        value = value.date()
    if isinstance(value, dt.date):
        return value, value + dt.timedelta(days=1)
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    m = PERIOD.fullmatch(text)
    if not m:
        return None
    y = int(m["y"])
    if m["q"]:
        start = _date(y, 3 * int(m["q"]) - 2)
        return start, _add_months(start, 3)
    if m["m"]:
        if m["d"]:
            start = _date(y, m["m"], m["d"])
            return (start, start + dt.timedelta(days=1)) if start else None
        start = _date(y, m["m"])
        return start, _add_months(start, 1)
    if m["h"]:
        start = _date(y, 6 * int(m["h"]) - 5)
        return start, _add_months(start, 6)
    if m["w"]:
        start = dt.date.fromisocalendar(y, max(1, min(int(m["w"]), 52)), 1)
        return start, start + dt.timedelta(days=7)
    if m["span"]:
        return _date(y), _date(int(m["span"][1:]) + 1)
    return _date(y), _date(y + 1)


def period_start(value):
    r = period_range(value)
    return r[0] if r else None


TIME_NAMES = re.compile(r"(?i)^_*(tid|time|year|aar|år|periode|period|dato|date|kvartal|quarter|maaned|måned|month)(_kode|_code)?$")


def time_field(columns, min_rate=0.9):
    """The column of a table that holds periods (time kept as rows), or None. `columns`: {field: distinct values}.
    A column named like time (Tid_kode, TID, aar, year …) wins over one that only looks like it (a column of years
    can be a count)."""
    best = None
    for name, values in columns.items():
        values = [v for v in values if v not in (None, "")]
        if len(values) < 1:
            continue
        rate = sum(1 for v in values if period_range(v)) / len(values)
        named = bool(TIME_NAMES.match(name))
        if rate >= min_rate and (named or len(values) > 1):
            score = (named, name.lower().endswith(("_kode", "_code")), rate)
            if best is None or score > best[0]:
                best = (score, name)
    return best[1] if best and best[0][0] else None


def periods_in_names(names):
    """Periods written in column names (Kvinder_2026K3, FOLK_2025): a wide table with one column per period."""
    found = {}
    for n in names:
        for token in re.findall(r"(?:18|19|20)\d\d(?:[KQHMU]\d\d?)?", str(n)):
            r = period_range(token)
            if r:
                found[token] = r
    return found


def latest_period_start(names):
    found = periods_in_names(names)
    return max(r[0] for r in found.values()) if found else None


# -- versions of units (DAGI) --------------------------------------------------------------------------

def local_date(stamp):
    """DAGI's time stamps are Danish midnight written in UTC (2018-01-21T23:00:00Z is 22 January): the Danish date."""
    if not stamp:
        return None
    if isinstance(stamp, dt.date) and not isinstance(stamp, dt.datetime):
        return stamp
    t = stamp if isinstance(stamp, dt.datetime) else dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    if t.tzinfo is None:
        return t.date()
    try:
        from zoneinfo import ZoneInfo
        return t.astimezone(ZoneInfo(TZ_NAME)).date()
    except Exception:                       # no time zone database: Danish time is UTC+1 or +2, midnight lands on the same date
        return (t + dt.timedelta(hours=2)).date()


@dataclass
class UnitVersion:
    code: str
    name: str | None
    stamp: str                # the register's virkningFra as delivered: the version's key and the instant to ask for it at
    valid_from: dt.date
    valid_to: dt.date | None  # first day it is no longer valid

    def covers(self, day):
        return self.valid_from <= day and (self.valid_to is None or day < self.valid_to)


def versions_from(nodes, code_field, name_field="navn"):
    return [UnitVersion(str(n[code_field]), n.get(name_field), n["virkningFra"], local_date(n["virkningFra"]), local_date(n.get("virkningTil")))
            for n in nodes]


def pick(versions, day, begins=None):
    """{code: the version valid on `day`}. Before the history begins (`begins`), the first version of each unit,
    if it was valid when the history began: the units of that date are the nearest known."""
    by = {}
    for v in versions:
        by.setdefault(v.code, []).append(v)
    out = {}
    for code, vs in by.items():
        vs.sort(key=lambda v: v.valid_from)
        hit = next((v for v in vs if v.covers(day)), None)
        if hit is None and begins and day < begins and vs[0].valid_from <= begins:
            hit = vs[0]
        if hit:
            out[code] = hit
    return out


# -- objects as they were on a date (registers) ----------------------------------------------------------

@dataclass
class TimeFields:
    """The columns of a layer that say when an object was valid and how the register knew it."""
    valid_from: str | None = None
    valid_to: str | None = None
    registered_from: str | None = None
    registered_to: str | None = None
    status: str | None = None
    identity: str | None = None
    built: str | None = None         # construction year: a 'new' object may only be new in the register
    form: str = "unknown"            # history | current | current, status Gældende only | unknown
    status_list: str | None = None


VARIABLE_OF = {"valid_from": "valid-time-from", "valid_to": "valid-time-to", "registered_from": "registration-time-from",
               "registered_to": "registration-time-to", "status": "lifecycle-status", "identity": "register-local-id",
               "built": "construction-year"}
GUESS = {"valid_from": ("virkningfra", "virkning_fra", "valid_from", "validfrom"),
         "valid_to": ("virkningtil", "virkning_til", "valid_to", "validto"),
         "registered_from": ("registreringfra", "registrering_fra", "registration_from"),
         "registered_to": ("registreringtil", "registrering_til", "registration_to"),
         "status": ("status", "livscyklus", "lifecycle_status"),
         "identity": ("id_lokalid", "lokalid", "id_lokal_id", "local_id"),
         "built": ("byg026opførelsesår", "byg026opfoerelsesaar", "opførelsesår", "construction_year")}


def layer_key(source):
    """The WFS layer name (typename) of a QGIS layer source, lower case: bbr_v001:bygning_current."""
    m = re.search(r"(?i)\btypename=['\"]?([^'\"\s&]+)", source or "")
    return m.group(1).lower() if m else None


def time_fields(field_names, time_layers=None, layer=None):
    """TimeFields of a layer: from the catalogue's column map for the delivered layer (time_layers: {dataset: [{layer,
    form, columns, status_list}]}) when its WFS layer is known, else from the usual column names."""
    names = {n.lower(): n for n in field_names}
    entry = None
    if layer and time_layers:
        local = layer.split(":")[-1]
        for entries in time_layers.values():
            for e in entries:
                if str(e.get("layer", "")).lower() in (layer, local) or str(e.get("layer", "")).lower().split(":")[-1] == local:
                    entry = e
    tf = TimeFields()
    if entry:
        tf.form, tf.status_list = entry.get("form", "unknown"), entry.get("status_list")
        for attr, var in VARIABLE_OF.items():
            col = (entry.get("columns") or {}).get(var)
            if col and col.lower() in names:
                setattr(tf, attr, names[col.lower()])
    for attr, guesses in GUESS.items():
        if getattr(tf, attr) is None:
            setattr(tf, attr, next((names[g] for g in guesses if g in names), None))
    if tf.form == "unknown" and layer:
        tf.form = "history" if layer.endswith("_hist") or "historik" in layer else "current" if layer.endswith("_current") else "unknown"
    return tf


def existing_codes(status_roles, status_list, include=("exists",)):
    """The status codes whose role counts as existing (exists; also under-construction if asked)."""
    lst = ((status_roles or {}).get("lists") or {}).get(status_list) or {}
    return {str(c) for c, role in (lst.get("codes") or {}).items() if role in include}


def to_instant(value):
    """A time stamp of a register (text, date, datetime) as an aware datetime (UTC when none is given); None when empty."""
    if value is None or value == "" or str(value) == "NULL":
        return None
    if isinstance(value, dt.datetime):
        t = value
    elif isinstance(value, dt.date):
        t = dt.datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip().replace("Z", "+00:00").replace(" ", "T", 1)
        text = re.sub(r"(\.\d{6})\d+", r"\1", text)            # BBR writes 7 decimals
        try:
            t = dt.datetime.fromisoformat(text)
        except ValueError:
            return None
    if t.tzinfo is None:
        try:
            from zoneinfo import ZoneInfo
            t = t.replace(tzinfo=ZoneInfo(TZ_NAME))
        except Exception:
            t = t.replace(tzinfo=dt.timezone.utc)
    return t


def day_instant(day):
    """Noon on a Danish date: well inside the day whatever the time zone of the stamps."""
    t = dt.datetime(day.year, day.month, day.day, 12)
    try:
        from zoneinfo import ZoneInfo
        return t.replace(tzinfo=ZoneInfo(TZ_NAME))
    except Exception:
        return t.replace(tzinfo=dt.timezone(dt.timedelta(hours=1)))


def in_span(start, end, t):
    s, e = to_instant(start), to_instant(end)
    return (s is None or s <= t) and (e is None or t < e)


def valid_on(row, tf, t, known=None, existing=None):
    """True when the row (a dict) is the version valid at `t`, as the register knew it at `known` (default: now),
    and its status (if any and if `existing` is given) counts as existing."""
    if tf.valid_from and not in_span(row.get(tf.valid_from), row.get(tf.valid_to) if tf.valid_to else None, t):
        return False
    if tf.registered_from or tf.registered_to:
        if known is None:
            if tf.registered_to and to_instant(row.get(tf.registered_to)) is not None:
                return False
        elif not in_span(row.get(tf.registered_from) if tf.registered_from else None, row.get(tf.registered_to) if tf.registered_to else None, known):
            return False
    if existing is not None and tf.status:
        return str(row.get(tf.status)) in existing
    return True
