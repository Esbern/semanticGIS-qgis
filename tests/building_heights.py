"""Processing: Building heights from elevation models. Offline: synthetic DSM/DTM GeoTIFFs with a flat roof with a
chimney, a pitched roof, a building outside the rasters and a sloping terrain; checks height, eave, roof type, the
chimney filter, ground and the optional GeoTIFFs. With SEMANTICGIS_LIVE=1 (and DATAFORDELER_API_KEY in the
environment) it also reads Danmarks Højdemodel for two buildings in Copenhagen with the rasters left empty.

  python tests/building_heights.py
"""
import os, sys, tempfile
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

provider = SemanticGisProvider()
QgsApplication.processingRegistry().addProvider(provider)
failures = 0


def expect(label, got, want, tol=None):
    global failures
    good = (got is not None and abs(got - want) <= tol) if tol is not None else got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


tmp = tempfile.mkdtemp()
X0, Y0, RES, N = 700000.0, 6170000.0, 0.4, 250   # 100 x 100 m


def raster(name, array):
    path = os.path.join(tmp, name + ".tif")
    ds = gdal.GetDriverByName("GTiff").Create(path, N, N, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((X0, RES, 0, Y0 + N * RES, 0, -RES))
    srs = osr.SpatialReference(); srs.ImportFromEPSG(25832)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(-9999)
    ds.GetRasterBand(1).WriteArray(array)
    ds = None
    return QgsRasterLayer(path, name)


# pixel centres
xs = X0 + (np.arange(N) + 0.5) * RES
ys = Y0 + N * RES - (np.arange(N) + 0.5) * RES
X, Y = np.meshgrid(xs, ys)
dtm = 10 + (X - X0) * 0.02                     # terrain sloping 2 m over 100 m
dsm = dtm.copy()
flat = (X > X0 + 10) & (X < X0 + 30) & (Y > Y0 + 10) & (Y < Y0 + 30)
dsm[flat] = dtm[flat] + 12                      # flat roof 12 m
chimney = (X > X0 + 19) & (X < X0 + 20) & (Y > Y0 + 19) & (Y < Y0 + 20)
dsm[chimney] = dtm[chimney] + 20                # 1 x 1 m chimney at 20 m
pitched = (X > X0 + 50) & (X < X0 + 70) & (Y > Y0 + 50) & (Y < Y0 + 70)
rise = 4 * (1 - np.abs(Y - (Y0 + 60)) / 10)     # eave 6 m at the long walls, ridge 10 m in the middle
dsm[pitched] = dtm[pitched] + 6 + rise[pitched]
dsm_layer, dtm_layer = raster("dsm", dsm), raster("dtm", dtm)

buildings = QgsVectorLayer("Polygon?crs=EPSG:25832&field=name:string", "buildings", "memory")
for name, (x1, y1, x2, y2) in {"flat": (10, 10, 30, 30), "pitched": (50, 50, 70, 70), "outside": (200, 200, 210, 210)}.items():
    f = QgsFeature(buildings.fields()); f.setAttributes([name])
    f.setGeometry(QgsGeometry.fromWkt(f"POLYGON(({X0+x1} {Y0+y1},{X0+x2} {Y0+y1},{X0+x2} {Y0+y2},{X0+x1} {Y0+y2},{X0+x1} {Y0+y1}))"))
    buildings.dataProvider().addFeature(f)
QgsProject.instance().addMapLayers([buildings, dsm_layer, dtm_layer])

ndsm_path = os.path.join(tmp, "ndsm.tif")
result = processing.run("semanticgis:buildingheights", {"BUILDINGS": buildings, "DSM": dsm_layer, "DTM": dtm_layer,
                                                        "OUTPUT": "memory:", "OUT_NDSM": ndsm_path})
out = {f["name"]: f for f in result["OUTPUT"].getFeatures()}
expect("flat: height 12 m (chimney removed)", out["flat"]["height"], 12.0, 0.05)
expect("flat: raw maximum shows the chimney", out["flat"]["h_max_raw"], 20.0, 0.05)
expect("flat: roof type", out["flat"]["roof_type"], "flat")
expect("flat: eave = height", out["flat"]["eave"], out["flat"]["height"])
expect("flat: ground (terrain under the middle)", out["flat"]["ground"], 10.4, 0.05)
expect("flat: roof_abs = ground + height", out["flat"]["roof_abs"], out["flat"]["ground"] + out["flat"]["height"], 0.02)
expect("flat: check", out["flat"]["h_check"], "ok")
expect("pitched: height near the ridge", out["pitched"]["height"], 9.8, 0.3)
expect("pitched: eave near 6 m", out["pitched"]["eave"], 6.0, 0.6)
expect("pitched: roof type", out["pitched"]["roof_type"], "pitched")
expect("outside the rasters: no data", out["outside"]["h_check"], "no data")
expect("outside: no height", out["outside"]["height"], None)
expect("original field kept", sorted(out), ["flat", "outside", "pitched"])
ndsm = gdal.Open(ndsm_path)
expect("nDSM written", ndsm is not None and ndsm.RasterXSize > 0, True)

# DSM without DTM is refused with a message
try:
    processing.run("semanticgis:buildingheights", {"BUILDINGS": buildings, "DSM": dsm_layer, "OUTPUT": "memory:"})
    expect("DSM without DTM refused", False, True)
except Exception as e:
    expect("DSM without DTM refused", "both" in str(e), True)

if os.environ.get("SEMANTICGIS_LIVE"):
    from semanticgis.access import KEYS
    KEYS.use_environment(); KEYS.install()
    cph = QgsVectorLayer("Polygon?crs=EPSG:25832&field=name:string", "cph", "memory")
    # two blocks of flats on Israels Plads (rough footprints): about 5-6 storeys
    for name, wkt in {"block": "POLYGON((724180 6176310,724200 6176310,724200 6176325,724180 6176325,724180 6176310))"}.items():
        f = QgsFeature(cph.fields()); f.setAttributes([name]); f.setGeometry(QgsGeometry.fromWkt(wkt))
        cph.dataProvider().addFeature(f)
    live = processing.run("semanticgis:buildingheights", {"BUILDINGS": cph, "OUTPUT": "memory:"})
    f = next(live["OUTPUT"].getFeatures())
    print("     live:", {k: f[k] for k in ("height", "eave", "roof_type", "ground", "n_px", "h_check")})
    expect("live: Danmarks Højdemodel read", f["h_check"] in ("ok", "few pixels"), True)
    expect("live: ground in Copenhagen is low", f["ground"] is not None and -2 < f["ground"] < 30, True)

print("all good" if not failures else f"{failures} failure(s)")
sys.exit(1 if failures else 0)
