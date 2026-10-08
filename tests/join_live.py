"""End-to-end auto-join against live reference geometries (needs network).

  SEMANTICGIS_CATALOGUE=<folder or URL> python tests/join_live.py table.csv [table2.csv ...]

Each CSV (semicolon-separated, e.g. a Danmarks Statistik Statbank export) is loaded as a
delimited-text layer, the join is detected, and the joined layer is built at the most
detailed scale.
"""
import os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsProject, QgsVectorLayer
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], False); app.initQgis()

from semanticgis import references
from semanticgis.catalogue import load_catalogue
from semanticgis.join import detect
from semanticgis.joiner import add_joined_layer, build_joined_layer, matched_units, table_columns
from semanticgis.join_dialog import load_indexes
import semanticgis.join_dialog as jd

cache = tempfile.mkdtemp(prefix="semanticgis-refs-")
jd.cache_dir = lambda: cache          # keep the test out of the QGIS profile
catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
t = time.time(); indexes, problems = load_indexes(catalogue.references, "DK")
print(f"indexes: {[(i.source.id_scheme, len(i.units)) for i in indexes]} in {time.time()-t:.1f}s {problems or ''}")

failures = 0
for path in sys.argv[1:]:
    uri = f"file://{path}?delimiter=;&detectTypes=yes&geomType=none"
    table = QgsVectorLayer(uri, os.path.basename(path), "delimitedtext")
    assert table.isValid(), path
    candidates = detect(table_columns(table), indexes)
    print(f"\n{os.path.basename(path)}: {table.featureCount()} rows")
    for c in candidates[:3]:
        print("  ", c.describe(), "| unmatched:", c.unmatched[:6])
    if not candidates:
        failures += 1; continue
    best = candidates[0]
    units = matched_units(table, best)
    geometry = best.source.geometries_by_detail()[0]
    t = time.time()
    gpkg = references.fetch_geometries(best.source, geometry, list(units), cache, references.union_bbox(units.values()))
    joined, matched, unmatched = build_joined_layer(table, best, gpkg, geometry.scale)
    add_joined_layer(joined)
    valid = sum(1 for f in joined.getFeatures() if f.hasGeometry() and f.geometry().isGeosValid())
    print(f"   joined at {geometry.scale} in {time.time()-t:.1f}s: {matched} rows, {valid} valid geometries, "
          f"CRS {joined.crs().authid()}, unmatched {unmatched[:6]}")
    sample = next(joined.getFeatures())
    print("   first row:", {k: sample[k] for k in joined.fields().names()})
    print("   provenance:", {k.split('/')[-1]: joined.customProperty(k) for k in joined.customPropertyKeys() if k.startswith('semanticgis/join')})
app.exitQgis()
sys.exit(1 if failures else 0)
