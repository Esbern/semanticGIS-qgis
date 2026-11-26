"""Statistics tables from Danmarks Statistik's StatBank API, as wide tables for QGIS.

The API (api.statbank.dk, no key) describes a table (its variables and their values, which variable is the map,
which can be left out to give the total, which is time) and delivers the selected data as JSON-stat, with codes,
labels and values together, in UTF-8. This module reads that, and turns the cube into a wide table: one row per
area, one column per combination of the other variables (e.g. Kvinder_2026K3, Maend_2026K3), ready for
calculations in the field calculator and for the join tool. JSON-stat is also what Eurostat and the PX-Web
offices deliver, so another source can be added with the same reader. No QGIS imports: testable without QGIS.
"""

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

DST_API = "https://api.statbank.dk/v1"
MAX_CELLS = 200_000          # a larger selection is refused before it is fetched (the API and QGIS both slow down)


@dataclass
class Variable:
    id: str
    text: str
    values: list                     # [(code, label)]
    elimination: bool = False        # may be left out: the data are then the total over it
    time: bool = False
    map: str | None = None           # the API's map name: this variable is the geography


@dataclass
class TableInfo:
    id: str
    title: str
    unit: str
    updated: str
    variables: list
    source: str = "Danmarks Statistik"
    footnote: str = ""

    def area(self):
        """The geographic variable (the one with a map), else None."""
        return next((v for v in self.variables if v.map), None)

    def default_selection(self):
        """Area: all values; time: the latest; a variable that can be left out: left out (total); others: the first."""
        sel = {}
        for v in self.variables:
            if v.map:
                sel[v.id] = [c for c, _ in v.values]
            elif v.time:
                sel[v.id] = [v.values[-1][0]]
            elif not v.elimination:
                sel[v.id] = [v.values[0][0]]
        return sel


def table_id(text):
    """'FOLK1A', 'folk1a' or a statbank.dk / statistikbanken.dk link -> 'FOLK1A'."""
    text = (text or "").strip()
    m = re.search(r"(?:statbank\.dk|statistikbanken\.dk)/(?:[^/?#]*/)*?([A-Za-z][A-Za-z0-9]{2,15})(?:[/?#]|$)", text)
    if m:
        return m.group(1).upper()
    m = re.fullmatch(r"[A-Za-z][A-Za-z0-9]{2,15}", text)
    return text.upper() if m else None


def tableinfo_url(table, lang="da"):
    return f"{DST_API}/tableinfo/{urllib.parse.quote(table)}?format=JSON&lang={lang}"


def data_url(table, selection, lang="da"):
    params = [("lang", lang)] + [(var, ",".join(codes)) for var, codes in selection.items() if codes]
    return f"{DST_API}/data/{urllib.parse.quote(table)}/JSONSTAT?" + urllib.parse.urlencode(params, safe=",*()")


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "SemanticGIS QGIS plugin"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8-sig"))


def parse_tableinfo(d):
    variables = [Variable(id=v["id"], text=v.get("text", v["id"]), values=[(x["id"], x.get("text", x["id"])) for x in v.get("values", [])],
                          elimination=bool(v.get("elimination")), time=bool(v.get("time")), map=v.get("map")) for v in d["variables"]]
    return TableInfo(id=d["id"], title=d.get("text", d["id"]), unit=d.get("unit", ""), updated=d.get("updated", ""), variables=variables,
                     footnote=(d.get("footnote") or {}).get("text", "") if isinstance(d.get("footnote"), dict) else "")


def cells(selection, info):
    n = 1
    for v in info.variables:
        if v.id in selection:
            n *= max(len(selection[v.id]), 1)
    return n


# -- JSON-stat --------------------------------------------------------------------------------------

@dataclass
class Dim:
    id: str
    label: str
    codes: list
    labels: dict = field(default_factory=dict)


@dataclass
class Cube:
    label: str
    dims: list
    values: list                      # dense, row-major over dims (last dimension fastest); None where missing
    note: str = ""


def read_jsonstat(obj):
    """A JSON-stat 1.0 ({"dataset": …}, Danmarks Statistik) or 2.0 (Eurostat, PX-Web) dataset -> Cube."""
    ds = obj.get("dataset", obj) if isinstance(obj, dict) else obj
    if "dataset" not in obj and obj.get("class") != "dataset" and "dimension" not in obj:
        ds = next(iter(obj.values()))                  # a JSON-stat 1.0 bundle with another name
    dim = ds["dimension"]
    ids = ds.get("id") or dim.get("id")
    size = ds.get("size") or dim.get("size")
    dims = []
    for i, n in zip(ids, size):
        cat = dim[i].get("category", {})
        index = cat.get("index")
        if isinstance(index, dict):
            codes = [c for c, _ in sorted(index.items(), key=lambda x: x[1])]
        elif isinstance(index, list):
            codes = index
        else:
            codes = list((cat.get("label") or {}).keys())
        labels = cat.get("label") or {c: c for c in codes}
        assert len(codes) == n, (i, len(codes), n)
        dims.append(Dim(id=i, label=dim[i].get("label", i), codes=codes, labels=labels))
    total = 1
    for n in size:
        total *= n
    raw = ds.get("value", [])
    if isinstance(raw, dict):
        values = [None] * total
        for k, v in raw.items():
            values[int(k)] = v
    else:
        values = list(raw) + [None] * (total - len(raw))
    values = [None if v in ("..", ".", "") else v for v in values]
    return Cube(label=ds.get("label", ""), dims=dims, values=values)


# -- the wide table ---------------------------------------------------------------------------------

FOLD = str.maketrans({"æ": "ae", "ø": "oe", "å": "aa", "Æ": "Ae", "Ø": "Oe", "Å": "Aa", "é": "e", "ü": "u", "ö": "o", "ä": "a"})


def fold(text):
    """Æ, Ø, Å to AE, OE, AA in a word in capitals (OMRÅDE -> OMRAADE), else Ae, Oe, Aa (Århus -> Aarhus)."""
    return re.sub(r"\w+", lambda m: m[0].translate(FOLD).upper() if m[0].isupper() and len(m[0]) > 1 else m[0].translate(FOLD), str(text))


def safe(text, width=24):
    """A field-name part: ASCII letters, digits and _ only."""
    t = re.sub(r"[^A-Za-z0-9]+", "_", fold(text)).strip("_")
    return t[:width] or "x"


@dataclass
class Column:
    name: str
    alias: str
    key: dict                      # {dim id: code} of the column's cell


@dataclass
class WideTable:
    row_fields: list               # [(field name, alias, dim id, "code" | "label")]
    columns: list                  # [Column]
    rows: list                     # [[row field values …, column values …]]
    title: str = ""
    fixed: list = field(default_factory=list)   # labels of the variables with one chosen value (e.g. the one period)


def pivot(cube, row_dims, time_as_rows=False):
    """Rows: the combinations of `row_dims` (plus time when `time_as_rows`); columns: the combinations of the other
    dimensions that have more than one value. A dimension with one value is not repeated in every column name
    (it is in the title). Column names come from the labels, folded to ASCII (Kvinder_2026K3); the alias keeps
    the labels as written."""
    by_id = {d.id: d for d in cube.dims}
    rows_ = [d for d in cube.dims if d.id in row_dims]
    if time_as_rows:
        rows_ += [d for d in cube.dims if d.id not in row_dims and re.fullmatch(r"(?i)tid|time|year|aar|år", d.id)]
    cols_ = [d for d in cube.dims if d not in rows_ and len(d.codes) > 1]
    single = [d for d in cube.dims if d not in rows_ and len(d.codes) == 1]
    strides, s = {}, 1
    for d in reversed(cube.dims):
        strides[d.id] = s
        s *= len(d.codes)
    base = sum(strides[d.id] * 0 for d in single)

    def combos(dims):
        out = [{}]
        for d in dims:
            out = [{**o, d.id: c} for o in out for c in d.codes]
        return out

    columns, taken = [], set()
    for key in combos(cols_):
        parts = [safe(by_id[i].labels.get(c, c)) for i, c in key.items()] or [safe(cube.label or "value")]
        name = "_".join(parts)
        if not re.match(r"[A-Za-z]", name):
            name = "v" + name
        while name in taken:
            name = name + "_"
        taken.add(name)
        alias = ", ".join(str(by_id[i].labels.get(c, c)) for i, c in key.items()) or (cube.label or "value")
        columns.append(Column(name=name[:63], alias=alias, key=key))
    row_fields = []
    for d in rows_:
        n = safe(d.id, 30)
        row_fields += [(n + "_kode", f"{d.label} (code)", d.id, "code"), (n, d.label, d.id, "label")]
    rows = []
    for rk in combos(rows_):
        offset = base + sum(strides[i] * by_id[i].codes.index(c) for i, c in rk.items())
        vals = [cube.values[offset + sum(strides[i] * by_id[i].codes.index(c) for i, c in col.key.items())] for col in columns]
        rows.append([rk[d] if what == "code" else by_id[d].labels.get(rk[d], rk[d]) for _, _, d, what in row_fields] + vals)
    fixed = [str(d.labels.get(d.codes[0], d.codes[0])) for d in single if str(d.labels.get(d.codes[0], d.codes[0])) not in (cube.label or "")]
    return WideTable(row_fields=row_fields, columns=columns, rows=rows, title=cube.label, fixed=fixed)
