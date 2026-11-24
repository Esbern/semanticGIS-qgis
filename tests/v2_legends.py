"""Standard symbology (legends) from the data share, in the panel and on layers (network; share login from the environment).

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

import collections
from semanticgis import legends
from semanticgis.catalogue_v2 import SHARE
from qgis.core import Qgis, QgsExpressionContextUtils, QgsFeatureRequest, QgsMapLayer, QgsRenderContext
from qgis.PyQt.QtWidgets import QMenu
network.install(); KEYS.install(); iface = Iface(); dock = SemanticGisDock(iface)
c = dock.catalogue = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"], os.path.join(scratch, "cache"))[0]
KEYS.set_profiles(c.access_profiles); VARIABLES.update(c.variables); legends.LEGENDS.update(c.legends); dock.rebuild()
by = lambda sid, name: next(f for s in c.datasets[sid].share for f in s["files"] if f.layer == name)
expect("legends read", len(legends.LEGENDS) >= 4)
CASES = [("sgis-000972", "jordart_25000_v7_1.parquet", "tsym"), ("sgis-001831", "jordart_200000.parquet", "TSYM"), ("sgis-000539", "geomorfologi.parquet", "tsym"),
         ("sgis-000539", "geomorfologi_stribrogen.parquet", "TSYM"), ("sgis-001672", "praekvartaer_bornholm.parquet", "Bjergart")]
expect("the right column", [(legends.column_for(c.datasets[s], by(s, n)) or (0, 0))[1] for s, n, _ in CASES], [x[2] for x in CASES])
expect("no legend where no scheme has one", legends.column_for(c.datasets["sgis-002182"], by("sgis-002182", "geotoper_region_sjaelland.parquet")), None)
L25 = legends.LEGENDS["geus-jordart-25000"]
expect("the user's language first", [legends.choices(L25, "polygon", x)[0].id for x in ("da_DK", "en_GB", "de_DE")], ["official-da", "official-en", "official-en"])
m = QMenu(); dock._legend_menu(m, None, c.datasets["sgis-000539"], by("sgis-000539", "geomorfologi.parquet")); sub = m.actions()[0].menu()
expect("menu", sorted(a.text() for a in sub.actions() if a.text()), sorted(["Official (GEUS), Danish", "Official (GEUS), English", "Colours only", "No legend", "Show the legend's web page"]))
bad = '<qgis><renderer-v2 type="singleSymbol"><symbols/><x name="name" value="%s"/></renderer-v2></qgis>'
expect("a style pointing outside is refused", [legends.renderer_from_style(bad % x, "a", "a") for x in ("https://x.dk/a.svg", "/etc/a.svg", "file:///a", "C:/a.svg")] + [legends.renderer_from_style("<!DOCTYPE q [<!ENTITY a 'b'>]><qgis/>", "a", "a"), legends.renderer_from_style("not xml", "a", "a"), legends.renderer_from_style("", "a", "a")], [None] * 7)
expect("nothing fetched without the login", legends.fetch(L25["styles"][0]["href"]), None)
KEYS.use_environment()
def covered(l):
    ctx = QgsRenderContext(); ctx.expressionContext().appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(l)); r = l.renderer().clone(); r.startRender(ctx, l.fields()); n = hit = 0
    for f in l.getFeatures(QgsFeatureRequest().setFlags(Qgis.FeatureRequestFlag.NoGeometry)):
        n += 1; ctx.expressionContext().setFeature(f); hit += bool(r.symbolsForFeature(f, ctx))
    r.stopRender(ctx); return n == hit and n > 0
def load(sid, name, wanted=None, locale="en"):
    d, s = c.datasets[sid], by(sid, name); l = build_layer(s, d); legends.prepare(l, d, s, wanted, locale); place_layer(l, s, None, d, d.owner, wanted, locale); return l
expect("share login", KEYS.has_key(SHARE))
for sid, name, col in CASES[1:] + CASES[:1]:
    for loc in ("en", "da"):
        l = load(sid, name, None, loc); expect(f"{name} {loc}: official style, every feature", (l.renderer().type(), l.customProperty("semanticgis/legend").split(";")[0].startswith("official"), covered(l)), ("RuleRenderer", True, True))
l = load("sgis-001831", "jordart_200000.parquet", "lang:da", "en"); labels = [x.label() for x in l.renderer().legendSymbolItems()]
expect("asked for Danish", "Moræneler" in labels); expect("licence noted on the layer", "leveringsbetingelser" in l.customProperty("semanticgis/legend"))
for sid, name, col in CASES[1:]:
    l = load(sid, name, "colours"); expect(f"{name}: colours only", (l.renderer().type(), covered(l)), ("categorizedSymbol", True))
l = load("sgis-001672", "praekvartaer_bornholm.parquet", "none"); expect("no legend", (l.renderer().type(), l.customProperty("semanticgis/legend")), ("singleSymbol", None))
l = load("sgis-000972", "jordart_25000_v7_1.parquet"); t = legends._styles[L25["styles"][1]["href"]]
r = legends.renderer_from_style(t, "tsym", "jsym2"); expect("a style can be moved to another column", [x in r.dump() or '"jsym2"' in r.rootRule().dump() for x in ("jsym2",)], [True])
l.setRenderer(r); expect("and no rule keeps the old one", '"tsym"' in l.renderer().rootRule().dump(), False)
legends._styles.clear(); KEYS.use({SHARE: ""}); d, s = c.datasets["sgis-001831"], by("sgis-001831", "jordart_200000.parquet")
from qgis.core import QgsVectorLayer
p = os.path.expanduser("~/local_dev/SemanticGIS/ruc-share/" + s.url)
if os.path.exists(p):
    l = QgsVectorLayer(p, "x", "ogr"); said = legends.apply(l, d, s); expect("colours when the style cannot be read, and it says so", (l.renderer().type(), "could not be read" in said, covered(l)), ("categorizedSymbol", True, True))
print("failures:", failures, flush=True); QgsProject.instance().clear(); app.exitQgis(); sys.exit(1 if failures else 0)
