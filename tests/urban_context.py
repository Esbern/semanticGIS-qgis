"""Processing: Urban context for a study area (Denmark). Live only (Datafordeleren: GeoDanmark WFS and Danmarks
Højdemodel WCS), needs DATAFORDELER_API_KEY in the environment: a 250 x 250 m rectangle around Israels Plads in
Copenhagen, given as an extent the way a beginner draws it. Checks the GeoPackage tables, that buildings are kept
whole and other layers clipped, the heights, the GeoTIFFs and the layers added to the project.

  DATAFORDELER_API_KEY=… python tests/urban_context.py
"""
import os, sys, sqlite3, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), "../Resources/qgis/python/plugins"))
from qgis.core import QgsApplication
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], False); app.initQgis()
from processing.core.Processing import Processing; Processing.initialize()
import processing
from osgeo import gdal
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsPointXY, QgsProject,
                       QgsRectangle, QgsVectorLayer)

from semanticgis.access import KEYS
from semanticgis.tools import SemanticGisProvider

if not os.environ.get("DATAFORDELER_API_KEY"):
    print("skipped: set DATAFORDELER_API_KEY"); sys.exit(0)
provider = SemanticGisProvider()
QgsApplication.processingRegistry().addProvider(provider)
failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


# Without a key the tool stops with a message that says where to put it
try:
    processing.run("semanticgis:urbancontext", {"EXTENT": "724000,724100,6176000,6176100 [EPSG:25832]",
                                                "OUTPUT": os.path.join(tempfile.mkdtemp(), "x.gpkg")})
    expect("no key: refused", False, True)
except Exception as e:
    expect("no key: says where to enter it", "Settings" in str(e), True)

KEYS.use_environment(); KEYS.install()
centre = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), QgsCoordinateReferenceSystem("EPSG:25832"),
                                QgsProject.instance()).transform(QgsPointXY(12.5683, 55.6838))   # Israels Plads
x, y = centre.x(), centre.y()
extent = f"{x - 125},{x + 125},{y - 125},{y + 125} [EPSG:25832]"
gpkg = os.path.join(tempfile.mkdtemp(), "israels_plads.gpkg")
result = processing.run("semanticgis:urbancontext", {"EXTENT": extent, "OUTPUT": gpkg})
print("     attribution:", result["ATTRIBUTION"])

con = sqlite3.connect(gpkg)
tables = {r[0] for r in con.execute("select table_name from gpkg_contents where data_type = 'features'")}
print("     tables:", sorted(tables))
expect("study area, buildings, kerbs and centre lines saved", {"study_area", "buildings", "road_edges", "road_centrelines"} <= tables, True)
n, ok = con.execute("select count(*), sum(h_check = 'ok') from buildings").fetchone()
print(f"     {n} buildings, {ok} with h_check ok")
expect("buildings found", n > 10, True)
expect("most buildings have a reliable height", ok >= 0.8 * n, True)
lo, hi, med = con.execute("select min(height), max(height), avg(height) from buildings where h_check = 'ok'").fetchone()
print(f"     heights {lo:.1f}–{hi:.1f} m, mean {med:.1f} m")
expect("heights of a city centre (mean 8–30 m)", 8 < med < 30, True)
expect("roof types filled", {r[0] for r in con.execute("select distinct roof_type from buildings")} <= {"flat", "pitched", None}, True)
con.close()

area = QgsGeometry.fromRect(QgsRectangle(x - 125, y - 125, x + 125, y + 125))
buildings = QgsVectorLayer(f"{gpkg}|layername=buildings", "b", "ogr")
edges = QgsVectorLayer(f"{gpkg}|layername=road_edges", "e", "ogr")
expect("buildings kept whole (some reach outside)", any(not area.contains(f.geometry()) for f in buildings.getFeatures()), True)
expect("road edges clipped to the area", all(area.buffer(0.01, 4).contains(f.geometry()) for f in edges.getFeatures()), True)
expect("2D geometries", buildings.wkbType() in (6, 3), True)   # MultiPolygon / Polygon

folder = os.path.splitext(gpkg)[0] + "_rasters"
terrain = gdal.Open(os.path.join(folder, "terrain.tif"))
expect("terrain GeoTIFF covers the study area", terrain is not None and terrain.RasterXSize >= 625, True)
expect("height above ground GeoTIFF", os.path.exists(os.path.join(folder, "height_above_ground.tif")), True)

for name in ("orthophoto", "openstreetmap"):
    image = gdal.Open(os.path.join(folder, name + ".tif"))
    expect(f"{name}: 250 m at 0.1 m = 2500 px", image is not None and (image.RasterXSize, image.RasterYSize), (2500, 2500))
    if image is not None:
        bands = image.RasterCount
        sample = image.ReadAsArray(1000, 1000, 500, 500)
        print(f"     {name}: {bands} bands, mean {sample[:3].mean():.0f}, std {sample[:3].std():.0f}")
        expect(f"{name}: a real picture (not blank)", sample[:3].std() > 10, True)
expect("attribution names the orthophoto and OSM", "Ortofoto" in result["ATTRIBUTION"] and "OpenStreetMap" in result["ATTRIBUTION"], True)

group = QgsProject.instance().layerTreeRoot().findGroup("Urban context – israels_plads.gpkg")
expect("added to the project in a group", group is not None, True)
if group is not None:
    names = {n.name(): n.itemVisibilityChecked() for n in group.findLayers()}
    print("     layers:", names)
    expect("centre lines added switched off", names.get("Road centre lines (Vejmidte)"), False)
    expect("buildings shown", names.get("Buildings (Bygning)"), True)
    expect("orthophoto shown", names.get("Orthophoto (newest)"), True)
    expect("OpenStreetMap added switched off", names.get("OpenStreetMap"), False)
    b = next(n.layer() for n in group.findLayers() if n.name() == "Buildings (Bygning)")
    expect("buildings coloured by height", b.renderer().type(), "graduatedSymbol")

print("all good" if not failures else f"{failures} failure(s)")
KEYS.uninstall(); QgsProject.instance().clear(); app.exitQgis()
sys.stdout.flush(); os._exit(1 if failures else 0)   # skip interpreter teardown: QGIS network threads abort it
