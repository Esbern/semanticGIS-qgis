"""Processing: Renumber feature ids (fid) fixes the duplicate-fid problem after Multipart to singleparts.
Reproduces the failure first (saving the parts to a GeoPackage fails), then checks that the renumbered
layer saves, with unique fids, the old fid kept on request, and a fid added to a layer without one. No network.

  python tests/renumber_fid.py
"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), "../Resources/qgis/python/plugins"))
from qgis.core import QgsApplication, QgsFeature, QgsGeometry, QgsVectorLayer
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], False); app.initQgis()
from processing.core.Processing import Processing; Processing.initialize()
import processing
from qgis.core import QgsProcessingException

failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


tmp = tempfile.mkdtemp()
# Two multipolygons with 2 and 3 parts, saved as a GeoPackage (fid 1 and 2).
src = QgsVectorLayer("MultiPolygon?crs=EPSG:25832&field=name:string", "patches", "memory")
for name, parts in (("forest", 2), ("lake", 3)):
    f = QgsFeature(src.fields()); f.setAttributes([name])
    f.setGeometry(QgsGeometry.fromWkt("MULTIPOLYGON(" + ",".join(
        f"(({x} 0,{x + 5} 0,{x + 5} 5,{x} 5,{x} 0))" for x in range(0, 10 * parts, 10)) + ")"))
    src.dataProvider().addFeature(f)
gpkg = os.path.join(tmp, "patches.gpkg")
processing.run("native:savefeatures", {"INPUT": src, "OUTPUT": gpkg})
parts = processing.run("native:multiparttosingleparts", {"INPUT": gpkg, "OUTPUT": "TEMPORARY_OUTPUT"})["OUTPUT"]
expect("parts keep their parent's fid", sorted(f["fid"] for f in parts.getFeatures()), [1, 1, 2, 2, 2])
try:
    processing.run("native:savefeatures", {"INPUT": parts, "OUTPUT": os.path.join(tmp, "broken.gpkg")})
    broke = False
except QgsProcessingException:
    broke = True
expect("saving the parts to a GeoPackage fails (the bug)", broke, True)

import semanticgis
plugin = semanticgis.classFactory(type("Iface", (), {})())
plugin.initProcessing()
alg = QgsApplication.processingRegistry().algorithmById("semanticgis:renumberfid")
expect("tool in its group", (alg.displayName(), alg.group()), ("Renumber feature ids (fid)", "Vector table"))

fixed = os.path.join(tmp, "fixed.gpkg")
processing.run("semanticgis:renumberfid", {"INPUT": parts, "KEEP_OLD": True, "OUTPUT": fixed})
layer = QgsVectorLayer(fixed, "fixed")
expect("renumbered layer saved as GeoPackage", layer.featureCount(), 5)
expect("unique fids in order", [f["fid"] for f in layer.getFeatures()], [1, 2, 3, 4, 5])
expect("old fid kept", [f["old_fid"] for f in layer.getFeatures()], [1, 1, 2, 2, 2])
expect("other fields kept", [f["name"] for f in layer.getFeatures()], ["forest", "forest", "lake", "lake", "lake"])

plain = processing.run("semanticgis:renumberfid", {"INPUT": parts, "OUTPUT": os.path.join(tmp, "plain.gpkg")})["OUTPUT"]
expect("without old_fid by default", QgsVectorLayer(plain, "p").fields().names(), ["fid", "name"])

nofid = QgsVectorLayer("Point?crs=EPSG:25832&field=name:string", "points", "memory")
for name in ("a", "b"):
    f = QgsFeature(nofid.fields()); f.setAttributes([name]); f.setGeometry(QgsGeometry.fromWkt("POINT(0 0)")); nofid.dataProvider().addFeature(f)
added = processing.run("semanticgis:renumberfid", {"INPUT": nofid, "OUTPUT": "TEMPORARY_OUTPUT"})["OUTPUT"]
expect("fid added when missing", [(f["fid"], f["name"]) for f in added.getFeatures()], [(1, "a"), (2, "b")])

QgsApplication.processingRegistry().removeProvider(plugin.provider)
app.exitQgis()
sys.exit(1 if failures else 0)
