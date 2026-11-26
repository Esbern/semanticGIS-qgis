"""StatBank import dialog end to end (needs the network: api.statbank.dk).   python tests/statbank_dialog.py"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsProject
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
app = QgsApplication([], True); app.initQgis()
from qgis.PyQt.QtCore import Qt
from semanticgis.statbank_dialog import StatBankDialog
fails = 0
def expect(label, got, want=True):
    global fails
    ok = got == want; fails += not ok; print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": {got!r} != {want!r}"))
def pump(cond, s=60):
    t = time.time()
    while time.time() - t < s and not cond(): app.processEvents(); time.sleep(0.05)
    return cond()
class Bar:
    def __init__(self): self.msgs = []
    def pushMessage(self, *a, **k): self.msgs.append(a[1])
class Iface:
    def __init__(self): self.bar = Bar()
    def messageBar(self): return self.bar
    def setActiveLayer(self, l): self.active = l
joined = []
d = StatBankDialog(Iface(), None, open_join=lambda layer: joined.append(layer))
d.table.setText("https://www.statistikbanken.dk/FOLK1A"); d.fetch_info()
expect("the table is described", pump(lambda: d.info is not None))
expect("defaults: all areas, latest period, totals for the rest", (sorted(d.selection()), len(d.selection()["OMRÅDE"]) > 100, len(d.selection()["Tid"])), (["OMRÅDE", "Tid"], True, 1))
kon = next(d.tree.topLevelItem(i) for i in range(d.tree.topLevelItemCount()) if d.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == "KØN")
for j in range(kon.childCount()):
    if kon.child(j).data(0, Qt.ItemDataRole.UserRole) in ("1", "2"): kon.child(j).setCheckState(0, Qt.CheckState.Checked)
tid = next(d.tree.topLevelItem(i) for i in range(d.tree.topLevelItemCount()) if d.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == "Tid")
tid.child(tid.childCount() - 5).setCheckState(0, Qt.CheckState.Checked)       # a year earlier as well
expect("import is allowed", d.import_button.isEnabled())
d.run_import()
expect("the table is added", pump(lambda: d.layer is not None))
L = d.layer; names = [f.name() for f in L.fields()]; print("   ", L.name(), names, L.featureCount())
expect("code and name columns, then one column per sex and period", names[:2] == ["OMRAADE_kode", "OMRAADE"] and len(names) == 6 and names[2].startswith("Maend_"))
kbh = next(f for f in L.getFeatures() if f["OMRAADE_kode"] == "101")
expect("Copenhagen with numbers and its name in Danish", (kbh["OMRAADE"], all(isinstance(kbh[n], float) for n in names[2:])), ("København", True))
expect("the alias keeps the labels", L.attributeAlias(2).startswith("Mænd, "))
d._join(); expect("join is offered with the new table", joined == [L])
if os.environ.get("SEMANTICGIS_CATALOGUE"):
    from semanticgis.catalogue import load_catalogue
    from semanticgis.join import detect
    from semanticgis.join_dialog import load_indexes
    from semanticgis.joiner import table_columns
    cat, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
    idx, _ = load_indexes([r for r in cat.references if getattr(r, "join_key", None)], "DK")
    c = detect(table_columns(L), idx)[0]; print("   ", c.describe())
    expect("the imported table joins on its code column: the municipalities (country and regions are other levels)", (c.field, c.source.join_key, c.matched), ("OMRAADE_kode", "municipality-code", 99))
app.exitQgis(); print("failures:", fails); sys.exit(bool(fails))
