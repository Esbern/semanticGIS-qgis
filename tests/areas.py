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
from semanticgis.catalogue_v2 import around, canvas_home, concerns
from semanticgis.dock import ROLE, SemanticGisDock
fails = 0
def expect(label, got, want=True):
    global fails
    ok = got == want; fails += not ok; print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": {got!r} != {want!r}"))
A = {"w": {"parent": None}, "e": {"parent": "w"}, "d": {"parent": "e"}, "z": {"parent": "d"}, "c": {"parent": "z"}, "g": {"parent": "w"}}
expect("around", around(A, "c"), ["z", "d", "e", "w"]); C = lambda s, a, u=None, n=None: concerns(A, s, a, useful=u, scale=n)
expect("itself, inside, around, beside: only those that cover the whole area", [C("d", "d"), C("c", "d"), C("e", "d"), C("w", "d"), C("g", "d")], [True, False, True, True, False])
expect("scale: useful from 1:2,000 or 1:50,000, mapping at 1:25,000", [C("w", "d", 2000, 25000), C("w", "d", 50000, 25000), C("d", "d", 50000, 25000), C("w", "d", None, 25000), C("w", "d", 50000, None)], [True, False, False, True, True])
B = {"w": {"parent": None, "boxes": [[-180, -90, 180, 90]]}, "d": {"parent": "w", "boxes": [[8, 54.5, 15.3, 57.8]]}, "c": {"parent": "d", "boxes": [[12.2, 55.5, 12.75, 55.85]]}, "b": {"parent": "d", "boxes": [[14.6, 54.9, 15.25, 55.4]]}}
cph, odense = [12.5, 55.6, 12.6, 55.7], [10.3, 55.35, 10.45, 55.45]
expect("canvas: the smallest area that holds it", [canvas_home(B, cph), canvas_home(B, odense), canvas_home(B, [-10, 40, 30, 60])], ["c", "d", "w"])
expect("unknown area, no choice", [C(None, "c"), C("g", ""), C("x", "d")], [True, True, True])
class I:
    w = QMainWindow()
    def mainWindow(self): return self.w
    def messageBar(self): return None
d = SemanticGisDock(I()); d.catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"]); d.verified_only.setChecked(False); d.rebuild()
def shown(area_branch=False):
    """The datasets in the tree; without the 'In …' branch, which reaches datasets the other ways in do not (no publisher, no leaf)."""
    out = set()
    def walk(i, top=False):
        for k in range(i.rowCount()):
            c = i.child(k)
            if top and c.text().startswith("In ") and not area_branch: continue
            x = c.data(ROLE)
            if x and x[0] == "dataset": out.add(x[1])
            walk(c)
    walk(d.model.invisibleRootItem(), True); return out
def choose(a, n=0):
    d.area.setCurrentIndex(d.area.findData(a)); d.scale.setCurrentIndex(d.scale.findData(n)); return shown()
titles = [d.area.itemText(i).strip() for i in range(d.area.count())]; print(titles)
kinds = {a.get("kind") for a in d.catalogue.areas.values()}
expect("the list is the catalogue's, by kind", len(titles), len(d.catalogue.areas) + 2 + len(kinds)); expect("kind headings cannot be chosen", [d.area.model().item(i).isEnabled() for i in range(d.area.count()) if d.area.itemData(i) is None], [False] * len(kinds)); expect("Denmark at any scale to begin with", (d.area.currentData(), d.scale.currentData()), ("denmark", 0))
dk, all_, cph, dk1k, dk25k = choose("denmark"), choose(""), choose("copenhagen"), choose("denmark", 1000), choose("denmark", 25000); scope = {k: v.scope for k, v in d.catalogue.datasets.items() if hasattr(v, "scope")}
gd = {k for k, v in d.catalogue.datasets.items() if getattr(v, "useful_from", None) == 2000}
print("all", len(all_), "Denmark", len(dk), "Copenhagen", len(cph), "Denmark 1:1,000", len(dk1k), "1:25,000", len(dk25k), "GeoDanmark", len(gd))
expect("Greenland is not in Denmark's list", [k for k in dk if scope.get(k) == "greenland"], []); expect("Copenhagen's are not in Denmark's (they do not cover Denmark)", any(scope.get(k) == "copenhagen" for k in dk), False); expect("Copenhagen's are in Copenhagen's", any(scope.get(k) == "copenhagen" for k in cph)); expect("Copenhagen: its own and the larger areas'", {scope.get(k) for k in cph} & {"bornholm", "greenland", "jutland", "funen"}, set()); expect("nothing lost in 'all'", dk <= all_ and cph <= all_)
expect("world data (OpenStreetMap) are in Denmark's and Copenhagen's", (any(scope.get(k) == "world" for k in dk), any(scope.get(k) == "world" for k in cph)), (True, True)); expect("area without a scope is always shown", {k for k in all_ if not scope.get(k)} <= cph)
expect("GeoDanmark (useful from 1:2,000) at 1:25,000 but not at 1:1,000", (bool(gd) and gd & dk == gd & dk25k == gd & dk, gd & dk1k), (True, set()))
d2 = SemanticGisDock(I()); choose("copenhagen", 25000); d2.catalogue = d.catalogue; d2.rebuild(); expect("the choice is remembered", (d2.area.currentData(), d2.scale.currentData()), ("copenhagen", 25000))
d.search.setText("marker"); d.rebuild(); n = len(shown()); choose("greenland"); d.search.setText("marker"); expect("search follows the area", len(shown()) < n or n == 0)
from qgis.gui import QgsMapCanvas
class J(I):
    c = QgsMapCanvas()
    def mapCanvas(self): return self.c
J.c.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326")); J.c.resize(400, 400); J.c.setExtent(QgsRectangle(12.5, 55.6, 12.6, 55.7))
d3 = SemanticGisDock(J()); d3.catalogue = d.catalogue; d3.verified_only.setChecked(False); d3.rebuild(); dd = d; d = d3
on = choose("@canvas"); print("canvas over Copenhagen:", len(on), d3.area.currentText())
expect("canvas over Copenhagen finds the Copenhagen area", "Copenhagen" in d3.area.currentText()); choose("@canvas", -1); expect("scale as the map", d3.scale.currentText().startswith("as the map (1:"))
expect("and shows its data and Denmark's, not Bornholm's or Greenland's", (any(scope.get(k) == "copenhagen" for k in on), any(scope.get(k) == "denmark" for k in on), [k for k in on if scope.get(k) in ("bornholm", "greenland")]), (True, True, []))
on = shown(); J.c.setExtent(QgsRectangle(14.7, 55.0, 14.9, 55.2)); expect("the map moves: the list stays until it is read again", shown(), on); expect("the read-the-map button is there", d3.read_map.isVisibleTo(d3))
d3.read_map.click(); on2 = shown()
expect("read again over Bornholm", ("Bornholm" in d3.area.currentText(), any(scope.get(k) == "copenhagen" for k in on2), on2 != on), (True, False, True))
J.c.setExtent(QgsRectangle(10.3, 55.35, 10.45, 55.45)); d3.read_map.click(); expect("over Odense: Funen's and the larger areas' only", ("Funen" in d3.area.currentText(), {scope.get(k) for k in shown()} <= {None, "funen", "denmark", "europe", "world"}), (True, True))
d = dd; d.search.setText(""); choose("copenhagen")
top = [d.model.invisibleRootItem().child(i) for i in range(d.model.rowCount())]
expect("no search text: 'In Copenhagen area' comes first", top[0].text(), "In Copenhagen area")
groups = [top[0].child(i).text() for i in range(top[0].rowCount())]; print(groups)
expect("by area, nearest first", [g.split(" (")[0] for g in groups][:3], ["About Copenhagen area", "About Zealand and the southern islands", "About Denmark"])
kk = top[0].child(0); kids = [kk.child(i).data(ROLE)[1] for i in range(kk.rowCount())]
KK = [f"koebenhavns-kommune-{i}" for i in ("bydele", "lokaludvalg", "kvarterer", "roder")]
expect("Copenhagen's four levels are datasets of their own", [k for k in KK if k in kids], KK)
expect("useful from 1:10,000", [d.catalogue.datasets[k].useful_from for k in KK], [10000] * 4)
from semanticgis.layers import layer_source
expect("each level is a file on the web", [layer_source(d.catalogue.datasets[k].services[0]) for k in KK], [("/vsicurl/https://semanticgis.org/Data/assets/reference/koebenhavn/" + n + ".gpkg", "ogr") for n in ("kk_bydel", "kk_lokaludvalg", "kk_kvarter", "kk_rode")])
lay = QgsVectorLayer(*layer_source(d.catalogue.datasets[KK[0]].services[0])[:1], "bydele", "ogr"); expect("bydele load over the web: 10 districts", (lay.isValid(), lay.featureCount()), (True, 10))
reg = d.catalogue.registers["koebenhavns-kommune-statistical-units"]; expect("a register with the four", (reg["page"], reg["datasets"]), ("/Initiatives/koebenhavns-kommune-statistical-units", KK))
choose("copenhagen", 1000); expect("at 1:1,000 they are hidden", [k for k in KK if k in shown(True)], [])
choose("copenhagen", 10000); expect("at 1:10,000 they are shown", [k for k in KK if k in shown(True)], KK)
choose(""); expect("All areas: no 'In …' branch", d.model.invisibleRootItem().child(0).text().startswith("In "), False)
d.catalogue.areas = {}; d.rebuild(); expect("no areas, no choice shown", d.area_row.isVisibleTo(d), False)
QgsSettings().remove("semanticgis"); print("failures:", fails); app.exitQgis(); sys.exit(bool(fails))
