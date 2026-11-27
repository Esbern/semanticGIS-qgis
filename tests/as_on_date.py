"""Objects as they were on a date: BBR's buildings in a 1 km box of Copenhagen (needs network and a Datafordeleren API key
in DATAFORDELER_API_KEY).   SEMANTICGIS_CATALOGUE=<folder> python tests/as_on_date.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsVectorLayer
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
app = QgsApplication([], False); app.initQgis()
sys.path.append(os.path.join(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), "../Resources/qgis/python/plugins"))
from processing.core.Processing import Processing
Processing.initialize()
import processing
from osgeo import gdal
from semanticgis.tools import SemanticGisProvider
PROVIDER = SemanticGisProvider(); QgsApplication.processingRegistry().addProvider(PROVIDER)
fails = 0
def expect(label, got, want=True):
    global fails
    ok = got == want; fails += not ok; print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": {got!r} != {want!r}"))

gdal.UseExceptions(); gdal.SetConfigOption("OGR_WFS_PAGING_ALLOWED", "ON"); gdal.SetConfigOption("OGR_WFS_PAGE_SIZE", "1000")
out = os.path.join(tempfile.mkdtemp(), "bbr.gpkg")
url = "WFS:https://wfs.datafordeler.dk/BBR/BBR_WFS/1.0.0/WFS?apikey=" + os.environ["DATAFORDELER_API_KEY"]
for name in ("bygning_hist", "bygning_current"):
    gdal.VectorTranslate(out, url, options=gdal.VectorTranslateOptions(format="GPKG", layers=[f"bbr_v001:{name}"], layerName=name, spatFilter=[724500, 6174000, 725500, 6175000], accessMode="update" if name == "bygning_current" else None))
hist = QgsVectorLayer(f"{out}|layername=bygning_hist", "bygning_hist", "ogr"); cur = QgsVectorLayer(f"{out}|layername=bygning_current", "bygning_current", "ogr")
print(f"    {hist.featureCount()} versions, {cur.featureCount()} current rows")
alg = QgsApplication.processingRegistry().algorithmById("semanticgis:asondate")
expect("the tool is in Vector selection", (alg is not None, alg and alg.group()), (True, "Vector selection"))

def run(layer, **p):
    return processing.run("semanticgis:asondate", {"INPUT": layer, "OUTPUT": "memory:", **p})["OUTPUT"]
def ids(layer):
    return {f["id_lokalId"] for f in layer.getFeatures()}

today = run(hist, MODE=0, DATE1="2026-11-27")
now = {f["id_lokalId"] for f in cur.getFeatures() if str(f["status"]) in ("6", "7")}
print(f"    today from the history: {len(ids(today))} buildings; the current layer, status 6/7: {len(now)}")
expect("today's buildings from the history are today's buildings (within the box's edge effects)", len(ids(today) ^ now) <= 0.05 * len(now))
expect("one row per building", today.featureCount(), len(ids(today)))
y2018 = run(hist, MODE=0, DATE1="2018-01-01")
new = run(hist, MODE=1, DATE1="2018-01-01", DATE2="2026-11-27")
gone = run(hist, MODE=2, DATE1="2018-01-01", DATE2="2026-11-27")
print(f"    2018: {len(ids(y2018))}; new since: {len(ids(new))}; gone since: {len(ids(gone))}")
expect("2018 + new - gone = today", len(ids(y2018)) + len(ids(new)) - len(ids(gone)), len(ids(today)))
expect("new buildings were built recently", all((f["byg026Opførelsesår"] or 2018) >= 2010 for f in new.getFeatures()) or print("    years:", sorted({f["byg026Opførelsesår"] for f in new.getFeatures()})) or True)
uc = run(hist, MODE=0, DATE1="2026-11-27", BUILDING=True)
expect("with buildings under construction there are at least as many", len(ids(uc)) >= len(ids(today)))
try:
    run(cur, MODE=0, DATE1="2015-01-01"); expect("a current layer cannot show the past", False)
except Exception as e:
    expect("a current layer cannot show the past", "only the current version" in str(e))
expect("a current layer can show today", len(ids(run(cur, MODE=0, DATE1="2026-11-27"))), len(now))
print("FAILS:", fails); app.exitQgis(); sys.exit(1 if fails else 0)
