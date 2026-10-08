"""Matching table values to reference units: the core of the auto-join.

A statistical table carries only an ID (or a name) for each unit, often formatted
differently from the reference: "101", "0101", "101 København" and "DK_101" all mean the
same Danish municipality. This module normalises values, indexes the reference units by
full ID, national code and name, and scores every table column against every reference to
find the join. It has no QGIS imports so it can be tested without QGIS.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field

MIN_RATE = 0.5          # a candidate must match at least half of a column's distinct values
NAME_SUFFIXES = (" kommune", " municipality", " kommun", " kommune.")
NAME_PREFIXES = ("region ",)   # Danmarks Statistik "Region Hovedstaden" = NUTS "Hovedstaden"
MODE_ORDER = {"id": 0, "national": 1, "name": 2}  # prefer IDs over names on equal rates


def norm_id(value):
    """Normalise an ID: codes compare without leading zeros, "101 København" -> "101"."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+\.0+", text):          # numbers read from CSV as floats
        text = text.split(".")[0]
    code = re.fullmatch(r"(\d+)(?:\s+\S.*)?", text)
    if code:
        return str(int(code.group(1)))
    return re.sub(r"\s+", "", text).upper()


def norm_name(value):
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip().casefold()
    for suffix in NAME_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    for prefix in NAME_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text or None


@dataclass
class RefUnit:
    """One reference unit as read from a geometry file (attributes only)."""

    id: str
    name: str | None = None
    country: str | None = None
    level: int | None = None
    bbox: tuple | None = None   # (minx, miny, maxx, maxy) in the source CRS


class KeyIndex:
    """Lookup of one reference source's units by full ID, national code and name."""

    def __init__(self, source, units, country=None):
        self.source = source
        self.units = units
        self.country = (country or "").upper() or None
        self.by_id = {}
        self.by_national = {}
        self.by_name = defaultdict(list)
        for unit in units:
            key = norm_id(unit.id)
            if key:
                self.by_id[key] = unit
            # "DK_671" -> national code "671", only within the chosen country (codes repeat across countries).
            if "_" in unit.id and (self.country is None or (unit.country or "").upper() == self.country):
                national = norm_id(unit.id.split("_", 1)[1])
                if national:
                    self.by_national.setdefault(national, unit)
            name = norm_name(unit.name)
            if name:
                self.by_name[name].append(unit)

    def lookup(self, value, mode, level=None):
        if mode == "id":
            return self.by_id.get(norm_id(value))
        if mode == "national":
            return self.by_national.get(norm_id(value))
        if mode == "name":
            hits = [u for u in self.by_name.get(norm_name(value), []) if level is None or u.level == level]
            return hits[0] if len(hits) == 1 else None
        raise ValueError(mode)

    def name_levels(self):
        return sorted({u.level for u in self.units if u.level is not None}) or [None]


@dataclass
class Candidate:
    field: str
    index: KeyIndex
    mode: str                  # "id" | "national" | "name"
    level: int | None          # restricts name lookups; IDs are unique across levels
    matched: int
    total: int
    unmatched: list = field(default_factory=list)
    levels: list = field(default_factory=list)   # levels of the matched units, for display

    @property
    def rate(self):
        return self.matched / self.total if self.total else 0.0

    @property
    def source(self):
        return self.index.source

    def describe(self):
        how = {"id": "by ID", "national": f"by national code ({self.index.country})", "name": "by name"}[self.mode]
        levels = self.levels or ([self.level] if self.level is not None else [])
        level = f", level {'/'.join(map(str, levels))}" if levels else ""
        return f"{self.field} → {self.source.label} {how}{level}: {self.matched}/{self.total} ({self.rate:.0%})"

    def lookup(self, value):
        return self.index.lookup(value, self.mode, self.level)


def detect(columns, indexes, min_rate=MIN_RATE):
    """Score every (column, reference, mode) and return candidates, best first.

    `columns` maps a field name to its distinct values (a sample is enough).
    """
    candidates = []
    for field_name, values in columns.items():
        values = [v for v in dict.fromkeys(values) if v is not None and str(v).strip() != ""]
        if not values:
            continue
        for index in indexes:
            plans = [("id", None), ("national", None)] + [("name", lvl) for lvl in index.name_levels()]
            for mode, level in plans:
                if mode == "national" and not index.by_national:
                    continue
                hits = [(v, index.lookup(v, mode, level)) for v in values]
                matched = [unit for _, unit in hits if unit is not None]
                if not matched:
                    continue
                candidates.append(
                    Candidate(
                        field=field_name,
                        index=index,
                        mode=mode,
                        level=level,
                        matched=len(matched),
                        total=len(values),
                        unmatched=[str(v) for v, unit in hits if unit is None][:8],
                        levels=sorted({u.level for u in matched if u.level is not None}),
                    )
                )
    good = [c for c in candidates if c.rate >= min_rate]
    return sorted(good, key=lambda c: (-c.rate, MODE_ORDER[c.mode], -c.matched))
