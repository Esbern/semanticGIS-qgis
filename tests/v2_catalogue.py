"""The v2 catalogue in the panel, files on the data share, and column names from variables (network; keys from the environment).

  QT_QPA_PLATFORM=offscreen SEMANTICGIS_CATALOGUE=<folder or URL> RUC_SHAR=… RUC_SHAR_TOKEN=… DATAFORDELER_API_KEY=… python tests/v2_catalogue.py
"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsProject, QgsSettings
from qgis.PyQt.QtCore import QCoreApplication, QSettings
scratch = tempfile.mkdtemp(prefix="sgis-v2-")
QCoreApplication.setOrganizationName("SemanticGISTestV2")
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"): QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], True); app.initQgis()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, scratch)
from qgis.PyQt.QtWidgets import QMainWindow
from semanticgis import network, share
from semanticgis.access import KEYS
from semanticgis.catalogue import load_catalogue
from semanticgis.dock import ROLE, SemanticGisDock
from semanticgis.layers import VARIABLES, build_layer, place_layer
failures = 0
def expect(label, got, want=True):
    global failures
    good = got == want; failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}" + ("" if good else f": {got!r} (expected {want!r})"), flush=True)
class Iface:
    w = QMainWindow(); said = []
    def mainWindow(self): return self.w
    def messageBar(self):
        said = self.said
        return type("B", (), {"pushMessage": lambda s, *a, **k: said.append(a)})()

# --- share links
expect("link parsed", share.parse_link("https://nc.example.dk/s/AbC123?dir=/"), ("https://nc.example.dk", "AbC123"))
expect("index.php link parsed", share.parse_link("https://nc.example.dk/index.php/s/AbC123"), ("https://nc.example.dk", "AbC123"))
expect("no link", [share.parse_link(x) for x in ("", None, "nonsense", "https://nc.example.dk/AbC", "http://nc.example.dk/s/x", "https://nc.example.dk/s")], [None] * 6)
expect("not usable without password", share.configure("https://nc.example.dk/s/x", ""), False)

network.install(); KEYS.install()
iface = Iface(); dock = SemanticGisDock(iface)
c = dock.catalogue = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"], os.path.join(scratch, "cache"))[0]
KEYS.set_profiles(c.access_profiles); VARIABLES.update(c.variables); dock.rebuild()
expect("offline copy is v2 too", load_catalogue("/nonexistent", os.path.join(scratch, "cache"))[0].is_v2)
expect("v2 read", c.is_v2)
expect("retired leaves are gone", [x in c.leaves for x in ("soil-properties", "geological-strata")], [False, False])
expect("new leaves are there", all(x in c.leaves for x in ("clay-content", "geotope", "seabed-sediment")))
expect("geotope is in five twigs", sum(any(l.id == "geotope" for l in t.leaves) for t in c.twigs.values()), 5)
expect("joins still work", len(c.references) > 3)
def walk(item, out):
    for i in range(item.rowCount()):
        ch = item.child(i); out.append(ch); walk(ch, out)
    return out
items = walk(dock.model.invisibleRootItem(), [])
kinds = {i.data(ROLE)[0] for i in items if i.data(ROLE)}
expect("variables, versions and share files in the panel", {"variables", "variable", "version", "share", "sharefile"} <= kinds)
flag = [i for i in items if i.data(ROLE) and i.data(ROLE)[0] == "dataset" and i.text().endswith("⚑")]
expect("unreviewed links are flagged and explained", bool(flag) and "Nobody has checked" in flag[0].toolTip())
def ds_under(leaf_id):
    top = dock._leaf_item(c.leaves[leaf_id])      # keep a reference: Qt deletes the children with it
    return {i.data(ROLE)[1]: i.text() for i in walk(top, []) if i.data(ROLE)[0] == "dataset"}
u = ds_under("surface-deposit")
expect("links from variables are not flagged", u["sgis-000972"].endswith("⚑"), False)
expect("links from guesses say so", u["sgis-002184"].endswith(" ?"))
expect("a dataset with only share files is listed", "sgis-002186" in ds_under("clay-content"))
expect("shared variables are listed once per leaf", sorted(v.id for v in c.leaf_variables["clay-content"]), ["clay-mass-fraction", "clay-mass-fraction-whole-soil"])
expect("every v2 node has a page", [d for d in (i.data(ROLE) for i in items) if d and d[0] in ("variable", "version", "share", "sharefile") and not dock.page_for(d)], [])

# --- without the share's link and password
by = lambda sid, name: next(f for s in c.datasets[sid].share for f in s["files"] if f.layer == name)
f = by("sgis-002184", "jordklassificering_proever.parquet")
dock.add_layer(None, c.datasets["sgis-002184"], f)
expect("says what is missing", "link" in str(iface.said[-1]) and "password" in str(iface.said[-1]))

KEYS.use_environment()
if KEYS.has_key("ruc-data-share"):
    def load(sid, name):
        d = c.datasets[sid]; s = by(sid, name); l = build_layer(s, d); place_layer(l, s, None, d, d.owner); return l
    l = load("sgis-002184", "jordklassificering_proever.parquet")
    expect("GeoParquet from the share", (l.featureCount(), l.crs().authid()), (38313, "EPSG:25832"))
    al = {x.name(): l.attributeAlias(i) for i, x in enumerate(l.fields())}
    expect("column named after its variable", al["ler"], "Clay (< 2 µm), % of the whole soil [ler]")
    expect("field names unchanged", "ler" in al)
    r = load("sgis-002188", "climate_pca.tif"); expect("COG from the share", (r.isValid(), r.bandCount(), r.crs().authid()), (True, 3, "EPSG:25832"))
    g = load("sgis-002182", "geotoper_region_sjaelland.parquet"); expect("non-ASCII column mapped", g.attributeAlias(g.fields().indexOf("hældning")), "Terrain slope [hældning]")
    expect("attribution set", "Arealdatakontoret" in l.customProperty("semanticgis/attribution"))
    path = os.path.join(scratch, "p.qgs"); QgsProject.instance().write(path); text = open(path, encoding="utf-8").read()
    expect("password not in the project", os.environ["RUC_SHAR_TOKEN"] in text, False)
    expect("no password in settings unless saved", os.environ["RUC_SHAR_TOKEN"] in str([QgsSettings().value(k) for k in QgsSettings().allKeys() if "semanticgis" in k]), False)
    KEYS.use({"ruc-data-share": "wrong-password-1"})
    from osgeo import gdal; gdal.VSICurlClearCache()
    try: build_layer(by("sgis-002181", "JB2024_000_030_cm/JB_mean_000_030_cm.tif"), c.datasets["sgis-002181"]); expect("wrong password refused", False)
    except RuntimeError as e: expect("wrong password refused, not shown", "wrong-password-1" in str(e) + str(getattr(e, "detail", "")), False)
if os.environ.get("DATAFORDELER_API_KEY"):
    d = c.datasets["sgis-000940"]; s = next(s for s in d.services if s.layer == "dagi_v001:afstemningsomraade_current")
    from qgis.core import QgsRectangle
    w = build_layer(s, d, True, "EPSG:25832"); place_layer(w, s, None, d, d.owner)
    a = {x.name(): w.attributeAlias(i) for i, x in enumerate(w.fields())}
    expect("WFS columns named after the same variables", a.get("registreringFra"), "Registration time from [registreringFra]")
print("failures:", failures); QgsProject.instance().clear(); app.exitQgis(); sys.exit(1 if failures else 0)
