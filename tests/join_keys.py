"""Join keys: StatBank-style tables join to DAGI's units through the catalogue's codes (needs network and a
Datafordeleren API key in DATAFORDELER_API_KEY).   SEMANTICGIS_CATALOGUE=<folder> python tests/join_keys.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsFeature, QgsField, QgsVectorLayer
from qgis.PyQt.QtCore import QMetaType
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
app = QgsApplication([], False); app.initQgis()
from semanticgis import references
from semanticgis.access import KEYS
from semanticgis.catalogue import load_catalogue
from semanticgis.join import detect, explain
from semanticgis.joiner import build_joined_layer, matched_units, table_columns
from semanticgis.join_dialog import load_indexes
fails = 0
def expect(label, got, want=True):
    global fails
    ok = got == want; fails += not ok; print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": {got!r} != {want!r}"))

def table(name, rows):
    t = QgsVectorLayer("None", name, "memory"); p = t.dataProvider()
    p.addAttributes([QgsField("OMRÅDE", QMetaType.Type.QString), QgsField("INDHOLD", QMetaType.Type.Int)]); t.updateFields()
    fs = []
    for code, n in rows:
        f = QgsFeature(t.fields()); f["OMRÅDE"] = code; f["INDHOLD"] = n; fs.append(f)
    p.addFeatures(fs); return t

catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
keys = [s for s in catalogue.references if getattr(s, "join_key", None)]
expect("the catalogue's join keys are reference sources", sorted(s.join_key for s in keys), ["municipality-code", "parish-code", "postcode", "region-code"])
indexes, problems = load_indexes(keys, "DK")
expect("their units come from the catalogue, no download", ({i.source.join_key: len(i.units) for i in indexes}["parish-code"] > 2000, problems), (True, []))

# municipalities as StatBank writes them, with two that ended in 2006
mun = table("FOLK1A", [("101 København", 1), ("147 Frederiksberg", 2), ("751 Aarhus", 3), ("171 Ledøje-Smørum", 4), ("181 Søllerød", 5)])
cols = table_columns(mun); c = detect(cols, indexes)[0]
expect("a municipality column is found, by its codes", (c.source.join_key, c.field, c.matched), ("municipality-code", "OMRÅDE", 3))
notes = explain(c, cols["OMRÅDE"]); print("   ", notes)
expect("codes from before 2007 are explained", bool(notes) and "171 Ledøje-Smørum" in notes[0] and "2007" in notes[0])

reg = table("REG", [("1081", 1), ("1084", 2), ("1085", 3)])
c = detect(table_columns(reg), indexes)[0]
expect("a region column is found, and the 2027 change is said", (c.source.join_key, any("Østdanmark" in n for n in explain(c, ["1081", "1084", "1085"]))), ("region-code", True))

# a parish table: no other reference has parishes; the geometry comes from DAGI's WFS with the key
KEYS.use_environment()
cache = tempfile.mkdtemp(prefix="semanticgis-joinkeys-")
codes = [x["code"] for x in catalogue.join_keys["parish-code"]["codes"][:3]]
par = table("SOGN", [(code, i) for i, code in enumerate(codes)])
c = next(x for x in detect(table_columns(par), indexes) if x.source.join_key == "parish-code")
units = matched_units(par, c)
path = references.fetch_geometries(c.source, c.source.geometries[0], list(units), cache)
joined, matched, unmatched = build_joined_layer(par, c, path, "full detail")
expect("three parishes joined with geometry from DAGI", (matched, unmatched, joined.featureCount(), all(not f.geometry().isEmpty() for f in joined.getFeatures())), (3, [], 3, True))
expect("the joined layer carries no key", "apikey" not in joined.source().lower() and "apikey" not in str(path).lower())
# 40 municipalities: more than a filter can carry in the URL, so the whole layer is read once and filtered locally
codes = [x["code"] for x in catalogue.join_keys["municipality-code"]["codes"][:40]]
mun = table("KOM40", [(str(int(code)), i) for i, code in enumerate(codes)])
c = next(x for x in detect(table_columns(mun), indexes) if x.source.join_key == "municipality-code")
path = references.fetch_geometries(c.source, c.source.geometries[0], list(matched_units(mun, c)), cache)
joined, matched, unmatched = build_joined_layer(mun, c, path, "full detail")
expect("40 municipalities (codes without leading zeros) joined through the whole layer", (matched, unmatched), (40, []))
app.exitQgis(); print("failures:", fails); sys.exit(bool(fails))
