"""Processing: Download snapshot series. Offline: series are found in a small catalogue (layers per year, datasets
per year, layers listed under the wrong dataset) and fields of different years are merged. With
SEMANTICGIS_LIVE=1 it also fetches Marker for three years near Ledreborg from geodata.fvm.dk.

  python tests/snapshot_series.py
  SEMANTICGIS_LIVE=1 SEMANTICGIS_CATALOGUE=<folder with services.v1.json> python tests/snapshot_series.py
"""
import os, sys, tempfile, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), "../Resources/qgis/python/plugins"))
from qgis.core import QgsApplication
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], False); app.initQgis()
from processing.core.Processing import Processing; Processing.initialize()
import processing
from qgis.core import QgsFeature, QgsField, QgsFields, QgsGeometry, QgsProject, QgsVectorLayer

from semanticgis.catalogue import Catalogue
from semanticgis.series import find_series
from semanticgis.tools.compat import DOUBLE, INTEGER64, STRING

failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


def wfs(layer, url="https://example.org/geoserver/wfs", status="ok"):
    return {"id": f"wfs|{url}|{layer}", "type": "wfs", "url": url, "layer": layer, "auth": "none", "check": {"status": status}}


services = {"datasets": {
    "marker": {"title": "Marker", "services": [wfs("Marker:Marker_2019"), wfs("Marker:Marker_2020"), wfs("Marker:Marker_2021"),
                                             wfs("Marker:Marker_2022", status="layer-not-found")]},
    # a dataset that also lists another series' layers
    "natura": {"title": "Natura hydrologi etableret", "services": [wfs("Tilsagn:Oekologiske_arealer_2020"), wfs("Tilsagn:Oekologiske_arealer_2021")]},
    "oeko": {"title": "Økologiske arealer", "services": [wfs("Tilsagn:Oekologiske_arealer_2022")]},
    # one dataset per year, the layer name without a year
    "p24": {"title": "Arealer med pesticidfri drift 2024", "services": [wfs("GB:pesticidfri")]},
    "p25": {"title": "Arealer med pesticidfri drift 2025", "services": [wfs("GB:pesticidfri_ny")]},
    "single": {"title": "Kirker", "services": [wfs("Kirker:kirker_2020")]},
}}
catalogue = Catalogue({"version": "0.4", "leaves": [], "spheres": [], "twigs": [], "threads": []}, services)
found = {s.title: s for s in find_series(catalogue.datasets.values())}
expect("series found", sorted(found), ["Arealer med pesticidfri drift", "Marker", "Økologiske arealer"])
expect("Marker years (unverified 2022 left out)", found["Marker"].years, [2019, 2020, 2021])
expect("layers listed under another dataset join their own series", found["Økologiske arealer"].years, [2020, 2021, 2022])
expect("one dataset per year", found["Arealer med pesticidfri drift"].years, [2024, 2025])
expect("between", [s.year for s in found["Marker"].between(2020, 2030)], [2020, 2021])
expect("label", found["Marker"].label, "Marker (2019–2021, 3 years)")

from semanticgis.tools.snapshot_series import merged_fields, decode
a, b = QgsFields(), QgsFields()
for f in (QgsField("Marknr", STRING), QgsField("IMK_areal", DOUBLE), QgsField("fid", INTEGER64)):
    a.append(f)
for f in (QgsField("Marknr", STRING), QgsField("IMK_areal", DOUBLE), QgsField("Afgroede", STRING), QgsField("GB", INTEGER64)):
    b.append(f)
c = QgsFields(); c.append(QgsField("GB", STRING))
merged = merged_fields([a, b, c])
expect("merged field names", [f.name() for f in merged], ["snapshot_year", "snapshot_layer", "Marknr", "IMK_areal", "source_fid", "Afgroede", "GB"])
expect("type conflict becomes text", merged.field("GB").type() == QgsField("x", STRING).type(), True)
expect("Latin-1 decoded", decode("Vårbyg".encode("cp1252")), "Vårbyg")
expect("UTF-8 decoded", decode("Vårbyg".encode("utf-8")), "Vårbyg")

if os.environ.get("SEMANTICGIS_LIVE"):
    import semanticgis
    plugin = semanticgis.classFactory(type("Iface", (), {})())
    plugin.initProcessing()
    from qgis.core import QgsApplication as A
    alg = A.processingRegistry().algorithmById("semanticgis:snapshotseries")
    labels = alg.parameterDefinition("SERIES").options()
    index = next(i for i, l in enumerate(labels) if l.startswith("Marker ("))
    area = QgsVectorLayer("Polygon?crs=EPSG:25832", "landscape", "memory")
    f = QgsFeature(); f.setGeometry(QgsGeometry.fromWkt("POLYGON((691000 6161000, 693000 6161000, 693000 6163000, 691000 6163000, 691000 6161000))"))
    area.dataProvider().addFeature(f); QgsProject.instance().addMapLayer(area)
    out = os.path.join(tempfile.mkdtemp(), "marker.gpkg")
    r = processing.run("semanticgis:snapshotseries", {"SERIES": index, "FIRST_YEAR": 2007, "LAST_YEAR": 2010, "AREA": area,
                                                    "BUFFER": 200, "INTERSECTING_ONLY": True, "CLIP": False, "CRS": "EPSG:25832",
                                                    "OUTPUT": out})
    expect("years fetched (2007 has no layer)", r["YEARS"], "2008, 2009, 2010")
    lyr = QgsVectorLayer(out, "out", "ogr")
    years = sorted({f["snapshot_year"] for f in lyr.getFeatures()})
    expect("one layer with three years", years, [2008, 2009, 2010])
    expect("CRS", lyr.crs().authid(), "EPSG:25832")
    big = QgsGeometry.fromWkt("POLYGON((690800 6160800, 693200 6160800, 693200 6163200, 690800 6163200, 690800 6160800))").buffer(1, 4)
    expect("all features touch the buffered area", all(f.geometry().intersects(big) for f in lyr.getFeatures()), True)
    r = processing.run("semanticgis:snapshotseries", {"SERIES": index, "FIRST_YEAR": 2025, "LAST_YEAR": 2026, "AREA": area,
                                                    "BUFFER": 0, "CLIP": True, "CRS": "EPSG:25832", "OUTPUT": "TEMPORARY_OUTPUT"})
    lyr = r["OUTPUT"]
    inside = QgsGeometry.fromWkt("POLYGON((691000 6161000, 693000 6161000, 693000 6163000, 691000 6163000, 691000 6161000))").buffer(0.01, 4)
    expect("clipped features lie inside the area", all(inside.contains(f.geometry()) for f in lyr.getFeatures()), True)
    expect("crop names readable", any("ø" in (f["Afgroede"] or "") or "æ" in (f["Afgroede"] or "") for f in lyr.getFeatures()), True)
    expect("attribution with date", r["ATTRIBUTION"].startswith("Indeholder data fra") and "hentet 20" in r["ATTRIBUTION"], True)

print("all good" if not failures else f"{failures} failure(s)")
sys.exit(failures)
