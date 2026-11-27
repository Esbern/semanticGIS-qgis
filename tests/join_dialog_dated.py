"""The join dialog's 'Units as they were' choice (needs network and DATAFORDELER_API_KEY).
  SEMANTICGIS_CATALOGUE=<folder> python tests/join_dialog_dated.py"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsFeature, QgsField, QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QMetaType
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
app = QgsApplication([], True); app.initQgis()
from semanticgis.access import KEYS
from semanticgis.catalogue import load_catalogue
from semanticgis.join import KeyIndex, detect
from semanticgis.join_dialog import JoinDialog
from semanticgis.joiner import table_columns
fails = 0
def expect(label, got, want=True):
    global fails
    ok = got == want; fails += not ok; print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": {got!r} != {want!r}"))
class Bar:
    def pushMessage(self, *a): print("    message:", a[1])
class Iface:
    def messageBar(self): return Bar()

KEYS.use({"datafordeler": os.environ["DATAFORDELER_API_KEY"]})
catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
def table(name, fields, rows):
    t = QgsVectorLayer("None", name, "memory"); p = t.dataProvider()
    p.addAttributes([QgsField(f, QMetaType.Type.QString) for f in fields]); t.updateFields()
    fs = []
    for r in rows:
        f = QgsFeature(t.fields()); f.setAttributes(list(r)); fs.append(f)
    p.addFeatures(fs); QgsProject.instance().addMapLayer(t); return t

def dialog_for(t, field):
    d = JoinDialog(Iface(), catalogue)
    d.table.setLayer(t)
    d._columns = table_columns(t)
    parish = next(s for s in catalogue.references if getattr(s, "join_key", None) == "parish-code")
    d.candidates = detect({field: d._columns[field]}, [KeyIndex(parish, parish.units, "DK")])
    for c in d.candidates:
        d.results.addItem(c.describe())
    d.results.setCurrentRow(0)
    return d

rows_t = table("KM1 rows", ["SOGN_kode", "Tid_kode", "INDHOLD"], [("7080", "2020K1", "1"), ("9345", "2026K1", "2")])
d = dialog_for(rows_t, "SOGN_kode")
expect("the choice is shown with three options, rows by default", ([d.when.itemData(i) for i in range(d.when.count())], d.when.currentData()), (["today", "date", "rows"], "rows"))
wide = table("KM1 wide", ["SOGN_kode", "Kvinder_2019K1", "Kvinder_2026K3"], [("7080", "1", "2")])
d2 = dialog_for(wide, "SOGN_kode")
expect("a wide table: on the start of its latest period", (d2.when.currentData(), d2.day.date().toString("yyyy-MM-dd"), d2.scale.isEnabled()), ("date", "2026-07-01", False))
d2.when.setCurrentIndex(d2.when.findData("today"))
expect("today uses the scale choice again", d2.scale.isEnabled())

d.run_join()
t0 = time.time()
while QgsApplication.taskManager().countActiveTasks() and time.time() - t0 < 300:
    app.processEvents(); time.sleep(0.05)
for _ in range(20):
    app.processEvents(); time.sleep(0.05)
print("   ", d.details.text())
joined = [l for l in QgsProject.instance().mapLayers().values() if "as at each Tid_kode" in l.name()]
expect("the joined layer was added, both rows at their own period", (len(joined), joined and joined[0].featureCount()), (1, 2))
expect("and it is temporal", bool(joined) and joined[0].temporalProperties().isActive())
QgsProject.instance().clear()
print("FAILS:", fails); app.exitQgis(); sys.exit(1 if fails else 0)
