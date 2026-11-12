"""Processing: Combination of values (unordered). Two pieces in the same rotation at different stages get the
same combination; counts, empty values and the number of distinct values. No network.

  python tests/value_combination.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), "../Resources/qgis/python/plugins"))
from qgis.core import QgsApplication
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], False); app.initQgis()
from processing.core.Processing import Processing; Processing.initialize()
import processing
from qgis.core import QgsFeature, QgsGeometry, QgsProject, QgsVectorLayer
import semanticgis
plugin = semanticgis.classFactory(type("Iface", (), {})())
plugin.initProcessing()
from semanticgis.tools.value_combination import combination

failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


expect("unordered and unique", combination(["Winter wheat", "Spring barley", "Winter wheat"]), ("Spring barley, Winter wheat", 2))
expect("other order, same result", combination(["Spring barley", "Winter wheat", "Winter wheat"])[0], "Spring barley, Winter wheat")
expect("with counts", combination(["Winter wheat", "Spring barley", "Winter wheat"], with_counts=True)[0], "Spring barley ×1, Winter wheat ×2")
expect("empty left out", combination(["Oats", None, ""]), ("Oats", 1))
expect("empty as (none)", combination(["Oats", None], keep_empty=True), ("(none), Oats", 2))
expect("nothing at all", combination([None, None]), ("", 0))

layer = QgsVectorLayer("Polygon?crs=EPSG:25832&field=id:integer&field=y2026_c:string&field=y2025_c:string&field=y2024_c:string",
                       "flat", "memory")
rows = [(1, "Winter wheat", "Spring barley", "Winter wheat"), (2, "Spring barley", "Winter wheat", "Winter wheat"),
        (3, "Permanent grass", "Permanent grass", None)]
for r in rows:
    f = QgsFeature(layer.fields()); f.setAttributes(list(r)); f.setGeometry(QgsGeometry.fromWkt("POLYGON((0 0,1 0,1 1,0 0))"))
    layer.dataProvider().addFeature(f)
QgsProject.instance().addMapLayer(layer)
out = processing.run("semanticgis:valuecombination", {"INPUT": layer, "FIELDS": ["y2026_c", "y2025_c", "y2024_c"],
                                                    "FIELD_NAME": "rotation", "OUTPUT": "TEMPORARY_OUTPUT"})["OUTPUT"]
got = {f["id"]: (f["rotation"], f["rotation_n"]) for f in out.getFeatures()}
expect("filled periods", sorted((f["id"], f["rotation_periods"]) for f in out.getFeatures()), [(1, 3), (2, 3), (3, 2)])
expect("same rotation at different stages", got[1] == got[2], True)
expect("values", got, {1: ("Spring barley, Winter wheat", 2), 2: ("Spring barley, Winter wheat", 2), 3: ("Permanent grass", 1)})
expect("fields kept", [f.name() for f in out.fields()], ["id", "y2026_c", "y2025_c", "y2024_c", "rotation", "rotation_n", "rotation_periods"])
out = processing.run("semanticgis:valuecombination", {"INPUT": layer, "FIELDS": ["y2026_c", "y2025_c", "y2024_c"], "FIELD_NAME": "rot",
                                                    "SEPARATOR": " + ", "WITH_COUNTS": True, "ADD_NUMBER": False, "OUTPUT": "TEMPORARY_OUTPUT"})["OUTPUT"]
expect("separator and counts, no number field", sorted(f["rot"] for f in out.getFeatures()),
       ["Permanent grass ×2", "Spring barley ×1 + Winter wheat ×2", "Spring barley ×1 + Winter wheat ×2"])
expect("no number field", "rot_n" in [f.name() for f in out.fields()], False)

print("all good" if not failures else f"{failures} failure(s)")
sys.exit(failures)
