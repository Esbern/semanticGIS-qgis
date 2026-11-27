"""Time-sliced join: table rows join to DAGI's units as they were on a date, or each row at its own period (needs network
and a Datafordeleren API key in DATAFORDELER_API_KEY).   SEMANTICGIS_CATALOGUE=<folder> python tests/join_dated.py"""
import datetime as dt
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsFeature, QgsField, QgsVectorLayer
from qgis.PyQt.QtCore import QMetaType
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
app = QgsApplication([], False); app.initQgis()
from semanticgis import references
from semanticgis.access import KEYS
from semanticgis.catalogue import load_catalogue
from semanticgis.join import detect, explain, KeyIndex
from semanticgis.joiner import build_dated_layer, matched_units, table_columns
from semanticgis.timeslice import period_start, time_field
fails = 0
def expect(label, got, want=True):
    global fails
    ok = got == want; fails += not ok; print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": {got!r} != {want!r}"))

KEYS.use({"datafordeler": os.environ["DATAFORDELER_API_KEY"]})
catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
parish = next(s for s in catalogue.references if getattr(s, "join_key", None) == "parish-code")
expect("parishes have a source of units by date", bool(parish.wfs.get("graphql")))
expect("ended parishes are among the units (7080 Islands Brygges)", any(u.id == "7080" for u in parish.units))

# a StatBank-like table with time as rows: Islands Brygges, coded 7080 until 2024-12-01 and 9345 from then
rows = [("7080", "2020K1", 100), ("7080", "2026K1", 5), ("9345", "2020K1", 200), ("9345", "2026K1", 210)]
t = QgsVectorLayer("None", "KM1-test", "memory"); p = t.dataProvider()
p.addAttributes([QgsField("SOGN_kode", QMetaType.Type.QString), QgsField("Tid_kode", QMetaType.Type.QString), QgsField("INDHOLD", QMetaType.Type.Int)]); t.updateFields()
fs = []
for code, tid, n in rows:
    f = QgsFeature(t.fields()); f.setAttributes([code, tid, n]); fs.append(f)
p.addFeatures(fs)
columns = table_columns(t)
expect("the period column is found", time_field({k: v for k, v in columns.items() if k != "SOGN_kode"}), "Tid_kode")
cands = detect({"SOGN_kode": columns["SOGN_kode"]}, [KeyIndex(parish, parish.units, "DK")])
c = cands[0]
expect("both parishes match (one has ended)", (c.matched, c.total), (2, 2))
notes = explain(c, columns["SOGN_kode"])
print("   ", notes)
expect("the ended parish is explained", any("7080" in n and "ended 2024-12-01" in n for n in notes))

cache = tempfile.mkdtemp()
units = matched_units(t, c)
row_day = {f.id(): period_start(f["Tid_kode"]) for f in t.getFeatures()}
days = sorted(set(row_day.values()))
path, chosen, begins = references.units_on(parish, list(units), days, cache)
expect("history begins in January 2018", begins, dt.date(2018, 1, 22))
expect("7080 existed in 2020, not in 2026; 9345 the other way round", ("7080" in chosen[dt.date(2020, 1, 1)], "7080" in chosen[dt.date(2026, 1, 1)], "9345" in chosen[dt.date(2020, 1, 1)], "9345" in chosen[dt.date(2026, 1, 1)]), (True, False, False, True))
L, matched, unmatched = build_dated_layer(t, c, path, lambda row, unit: chosen.get(row_day.get(row.id()), {}).get(unit.id), "as at each Tid_kode", "Tid_kode")
names = [f.name() for f in L.fields()]
expect("two rows joined: each code only while it was valid", (matched, sorted(unmatched)), (2, ["7080", "9345"]))
expect("unit and period dates are fields", {"ref_from", "ref_to", "period_from", "period_to"} <= set(names))
expect("the layer is temporal", L.temporalProperties().isActive())
areas = {(f["SOGN_kode"], f["Tid_kode"]): f.geometry().area() for f in L.getFeatures()}
print("   ", {k: round(v / 1e6, 2) for k, v in areas.items()})
expect("geometries are polygons with area", all(a > 1e5 for a in areas.values()))
path2, chosen2, _ = references.units_on(parish, list(units), [dt.date(2015, 6, 1)], cache)
expect("a date before the history gets its first units", "7080" in chosen2[dt.date(2015, 6, 1)])
import time; t0 = time.time(); references.units_on(parish, list(units), days, cache); print(f"    second time {time.time() - t0:.1f}s (cached geometry)")
print("FAILS:", fails); app.exitQgis(); sys.exit(1 if fails else 0)
