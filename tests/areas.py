"""Areas: the list comes from the catalogue, and the choice filters the panel.  SEMANTICGIS_CATALOGUE=<folder> python tests/areas.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import *
from qgis.PyQt.QtCore import QCoreApplication, QSettings
QCoreApplication.setOrganizationName("SemanticGISTestAreas")
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"): QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], True); app.initQgis()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp()); QgsSettings().remove("semanticgis")
from qgis.PyQt.QtWidgets import QMainWindow
from semanticgis.catalogue import load_catalogue
from semanticgis.catalogue_v2 import around, concerns
from semanticgis.dock import ROLE, SemanticGisDock
fails = 0
def expect(label, got, want=True):
    global fails
    ok = got == want; fails += not ok; print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": {got!r} != {want!r}"))
A = {"w": {"parent": None}, "e": {"parent": "w"}, "d": {"parent": "e"}, "z": {"parent": "d"}, "c": {"parent": "z"}, "g": {"parent": "w"}}
expect("around", around(A, "c"), ["z", "d", "e", "w"]); C = lambda s, a, u=None: concerns(A, s, a, u)
expect("itself, inside, around, beside", [C("d", "d"), C("c", "d"), C("e", "d"), C("e", "d", "e"), C("w", "d", "e"), C("w", "d", "w"), C("g", "d", "w")], [True, True, False, True, False, True, False])
expect("Copenhagen and up to Denmark", [C(s, "c", "d") for s in ("c", "z", "d", "e", "g")], [True, True, True, False, False]); expect("unknown area, no choice", [C(None, "c"), C("g", ""), C("x", "d")], [True, True, True])
class I:
    w = QMainWindow()
    def mainWindow(self): return self.w
    def messageBar(self): return None
d = SemanticGisDock(I()); d.catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"]); d.verified_only.setChecked(False); d.rebuild()
def shown():
    out = set()
    def walk(i):
        for k in range(i.rowCount()):
            x = i.child(k).data(ROLE)
            if x and x[0] == "dataset": out.add(x[1])
            walk(i.child(k))
    walk(d.model.invisibleRootItem()); return out
def choose(a, u):
    d.area.setCurrentIndex(d.area.findData(a)); d.area_around.setCurrentIndex(d.area_around.findData(u)); return shown()
titles = [d.area.itemText(i).strip() for i in range(d.area.count())]; print(titles)
expect("the list is the catalogue's", len(titles), len(d.catalogue.areas) + 1); expect("Denmark to begin with", (d.area.currentData(), d.area_around.currentData()), ("denmark", "world"))
dk, all_, cph, dkonly = choose("denmark", "world"), choose("", ""), choose("copenhagen", ""), choose("denmark", ""); scope = {k: v.scope for k, v in d.catalogue.datasets.items() if hasattr(v, "scope")}
print("all", len(all_), "Denmark+", len(dk), "Denmark only", len(dkonly), "Copenhagen only", len(cph))
expect("Greenland is not in Denmark's list", [k for k in dk if scope.get(k) == "greenland"], []); expect("Copenhagen is in Denmark's", any(scope.get(k) == "copenhagen" for k in dk)); expect("only its own", {scope.get(k) for k in cph} <= {"copenhagen", None}); expect("nothing lost in 'all'", dk <= all_ and cph <= all_)
expect("Denmark only has no world data", any(scope.get(k) in ("world", "europe") for k in dkonly), False); expect("area without a scope is always shown", {k for k in all_ if not scope.get(k)} <= cph)
d2 = SemanticGisDock(I()); choose("copenhagen", "denmark"); d2.catalogue = d.catalogue; d2.rebuild(); expect("the choice is remembered", (d2.area.currentData(), d2.area_around.currentData()), ("copenhagen", "denmark"))
d.search.setText("marker"); d.rebuild(); n = len(shown()); choose("greenland", ""); d.search.setText("marker"); expect("search follows the area", len(shown()) < n or n == 0)
d.catalogue.areas = {}; d.rebuild(); expect("no areas, no choice shown", d.area_row.isVisibleTo(d), False)
QgsSettings().remove("semanticgis"); print("failures:", fails); app.exitQgis(); sys.exit(bool(fails))
