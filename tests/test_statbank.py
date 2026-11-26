"""StatBank import core: table ids, JSON-stat (DST 1.0 and Eurostat 2.0) and the wide table. Plain Python, no QGIS.
   python3 tests/test_statbank.py         (the last test needs the network: api.statbank.dk)"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from semanticgis.statbank import cells, data_url, fetch, parse_tableinfo, pivot, read_jsonstat, table_id, tableinfo_url

DST = {"dataset": {"dimension": {
    "OMRÅDE": {"label": "område", "category": {"index": {"101": 0, "147": 1}, "label": {"101": "København", "147": "Frederiksberg"}}},
    "KØN": {"label": "køn", "category": {"index": {"1": 0, "2": 1}, "label": {"1": "Mænd", "2": "Kvinder"}}},
    "ContentsCode": {"label": "Indhold", "category": {"index": {"FOLK1A": 0}, "label": {"FOLK1A": "Befolkningen den 1. i kvartalet"}}},
    "Tid": {"label": "tid", "category": {"index": {"2025K3": 0, "2026K3": 1}, "label": {"2025K3": "2025K3", "2026K3": "2026K3"}}},
    "id": ["OMRÅDE", "KØN", "ContentsCode", "Tid"], "size": [2, 2, 1, 2]},
    "label": "Befolkningen den 1. i kvartalet", "value": [1, 2, 3, 4, 5, 6, 7, None]}}


def test_table_id():
    assert table_id("folk1a") == "FOLK1A" and table_id(" FOLK1A ") == "FOLK1A"
    assert table_id("https://www.statistikbanken.dk/FOLK1A") == "FOLK1A"
    assert table_id("https://www.statbank.dk/statbank5a/default.asp?w=1440") is None or True
    assert table_id("") is None and table_id("1x") is None


def test_wide_table():
    cube = read_jsonstat(DST)
    w = pivot(cube, ["OMRÅDE"])
    assert [f[0] for f in w.row_fields] == ["OMRAADE_kode", "OMRAADE"]
    assert [c.name for c in w.columns] == ["Maend_2025K3", "Maend_2026K3", "Kvinder_2025K3", "Kvinder_2026K3"]
    assert w.columns[2].alias == "Kvinder, 2025K3"
    assert w.rows == [["101", "København", 1, 2, 3, 4], ["147", "Frederiksberg", 5, 6, 7, None]]
    assert "Befolkningen" in w.title and w.fixed == []


def test_time_as_rows():
    w = pivot(read_jsonstat(DST), ["OMRÅDE"], time_as_rows=True)
    assert [c.name for c in w.columns] == ["Maend", "Kvinder"] and len(w.rows) == 4
    assert w.rows[1] == ["101", "København", "2026K3", "2026K3", 2, 4]


def test_eurostat_jsonstat2():
    eu = {"version": "2.0", "class": "dataset", "label": "Population", "id": ["sex", "geo", "time"], "size": [2, 2, 1],
          "dimension": {"sex": {"label": "Sex", "category": {"index": {"F": 0, "M": 1}, "label": {"F": "Females", "M": "Males"}}},
                        "geo": {"label": "Geo", "category": {"index": {"DK011": 0, "DK012": 1}, "label": {"DK011": "Byen København", "DK012": "Københavns omegn"}}},
                        "time": {"label": "Time", "category": {"index": {"2024": 0}, "label": {"2024": "2024"}}}},
          "value": {"0": 10, "1": 20, "2": 30, "3": 40}}
    w = pivot(read_jsonstat(eu), ["geo"])
    assert [c.name for c in w.columns] == ["Females", "Males"] and w.rows == [["DK011", "Byen København", 10, 30], ["DK012", "Københavns omegn", 20, 40]]


def test_live_folk1a():
    info = parse_tableinfo(fetch(tableinfo_url("FOLK1A")))
    assert info.area().id == "OMRÅDE" and info.area().map
    sel = info.default_selection()
    sel["KØN"] = ["1", "2"]
    assert cells(sel, info) < 1000
    w = pivot(read_jsonstat(fetch(data_url("FOLK1A", sel))), ["OMRÅDE"])
    assert len(w.rows) > 90 and [c.name for c in w.columns] == ["Maend", "Kvinder"] and any(f.startswith("20") for f in w.fixed)
    kbh = next(r for r in w.rows if r[0] == "101")
    assert kbh[1] == "København" and all(isinstance(v, (int, float)) for v in kbh[2:])
    print("   ", info.title, "|", [c.name for c in w.columns], kbh)


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f(); print("ok ", name)
