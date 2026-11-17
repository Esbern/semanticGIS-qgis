"""Processing: Clip project layers to one GeoPackage, and Surface partition 1 and 2. Offline: a study area with a
road (two kerb lines), a building, a hidden layer, a point layer and a raster; checks the clipped GeoPackage
(tables, styles, raster), the partition lines and gaps, the polygons with buildings marked and centre-line hints.

  python tests/surface_partition.py
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
import numpy as np
from osgeo import gdal, osr
from qgis.core import QgsFeature, QgsGeometry, QgsProject, QgsRasterLayer, QgsVectorLayer

from semanticgis.tools import SemanticGisProvider

provider = SemanticGisProvider()   # keep a reference, or Python frees it
QgsApplication.processingRegistry().addProvider(provider)
failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


def layer(kind, name, rows, fields="name:string"):
    lyr = QgsVectorLayer(f"{kind}?crs=EPSG:25832&field={fields}", name, "memory")
    for values, wkt in rows:
        f = QgsFeature(lyr.fields()); f.setAttributes(values); f.setGeometry(QgsGeometry.fromWkt(wkt))
        lyr.dataProvider().addFeature(f)
    QgsProject.instance().addMapLayer(lyr)
    return lyr


tmp = tempfile.mkdtemp()
area = layer("Polygon", "Study area", [(["a"], "POLYGON((0 0,100 0,100 100,0 100,0 0))")])
# a road crossing the area: two kerbs at y = 40 and y = 50 running past the edges; one kerb stops 0.2 m short
kerbs = layer("LineString", "Vejkant", [(["n"], "LINESTRING(-10 50,110 50)"), (["s"], "LINESTRING(0.2 40,110 40)")])
centre = layer("LineString", "Vejmidte", [(["Lokalvej"], "LINESTRING(-10 45,110 45)")], "vejkategori:string")
building = layer("Polygon", "Bygning", [(["house"], "POLYGON((20 60,40 60,40 80,20 80,20 60))"),
                                        (["far away"], "POLYGON((500 500,510 500,510 510,500 510,500 500))")])
trees = layer("Point", "Trae", [(["oak"], "POINT(70 70)")])
hidden = layer("Polygon", "Hidden", [(["x"], "POLYGON((5 5,15 5,15 15,5 15,5 5))")])
root = QgsProject.instance().layerTreeRoot()
root.findLayer(hidden.id()).setItemVisibilityChecked(False)
root.findLayer(centre.id()).setItemVisibilityChecked(False)   # centre lines are not edges of surfaces

path = os.path.join(tmp, "dem.tif")
ds = gdal.GetDriverByName("GTiff").Create(path, 200, 200, 1, gdal.GDT_Float32)
ds.SetGeoTransform((-50, 1, 0, 150, 0, -1))
srs = osr.SpatialReference(); srs.ImportFromEPSG(25832); ds.SetProjection(srs.ExportToWkt())
ds.GetRasterBand(1).WriteArray(np.arange(40000, dtype=np.float32).reshape(200, 200)); ds = None
dem = QgsRasterLayer(path, "Terræn"); QgsProject.instance().addMapLayer(dem)

# -- clip to GeoPackage
gpkg = os.path.join(tmp, "context.gpkg")
processing.run("semanticgis:cliptogeopackage", {"AREA": area, "INCLUDE_RASTERS": True, "LOAD_RESULT": False, "OUTPUT": gpkg})
con = sqlite3.connect(gpkg)
tables = sorted(r[0] for r in con.execute("select table_name from gpkg_contents where data_type = 'features'"))
expect("tables: visible layers + study area, not the hidden one", tables, ["bygning", "study_area", "trae", "vejkant"])
expect("the far-away building is clipped away", con.execute("select count(*) from bygning").fetchone()[0], 1)
expect("styles saved", con.execute("select count(*) from layer_styles").fetchone()[0] >= 4, True)
con.close()
clipped = gdal.Open(os.path.join(tmp, "context_rasters", "terraen.tif"))
expect("raster clipped to the area (100 x 100 px)", (clipped.RasterXSize, clipped.RasterYSize), (100, 100))
expect("raster values kept (top-left of the area)", float(clipped.ReadAsArray()[0, 0]), float(50 * 200 + 50))

# -- partition 1
r1 = processing.run("semanticgis:partitionlines", {"AREA": area, "SNAP": 0.3, "OUTPUT": "memory:", "DANGLES": "memory:"})
lines, gaps = r1["OUTPUT"], r1["DANGLES"]
sources = {f["src_layer"] for f in lines.getFeatures()}
expect("lines from kerbs, buildings and the boundary (no points, hidden or centre lines)", sorted(sources),
       ["Bygning", "Vejkant", "study_area"])
expect("the 0.2 m gap is snapped: no loose ends", gaps.featureCount(), 0)

r1b = processing.run("semanticgis:partitionlines", {"AREA": area, "SNAP": 0, "OUTPUT": "memory:", "DANGLES": "memory:"})
expect("without snapping the gap is marked", r1b["DANGLES"].featureCount(), 1)

# -- partition 2
r2 = processing.run("semanticgis:partitionpolygons", {"INPUT": lines, "BUILDINGS": building, "CENTERLINES": centre,
                                                      "CL_FIELD": "vejkategori", "OUTPUT": "memory:", "DANGLES": "memory:"})
polys = list(r2["OUTPUT"].getFeatures())
expect("polygons: below the road, road, above the road, building", len(polys), 4)
expect("the building is marked", sorted(str(p["surface"]) for p in polys).count("building"), 1)
road = [p for p in polys if p["hint"]]
expect("the road polygon got the centre line's hint", [p["hint"] for p in road], ["Lokalvej"])
expect("road area 100 x 10 m", round(road[0]["area_m2"]), 1000)
expect("no gaps remain", r2["DANGLES"].featureCount(), 0)

print("all good" if not failures else f"{failures} failure(s)")
sys.exit(1 if failures else 0)
