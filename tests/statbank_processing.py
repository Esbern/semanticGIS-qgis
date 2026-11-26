"""The StatBank import as a Processing tool (needs the network).   python tests/statbank_processing.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
app = QgsApplication([], False); app.initQgis()
sys.path.append(os.path.join(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), "../Resources/qgis/python/plugins"))
from processing.core.Processing import Processing
Processing.initialize()
import processing
from semanticgis.tools import SemanticGisProvider
PROVIDER = SemanticGisProvider(); QgsApplication.processingRegistry().addProvider(PROVIDER)
fails = 0
def expect(label, got, want=True):
    global fails
    ok = got == want; fails += not ok; print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": {got!r} != {want!r}"))
alg = QgsApplication.processingRegistry().algorithmById("semanticgis:statbanktable")
expect("the tool is in Data access", (alg is not None, alg and alg.group()), (True, "Data access"))
r = processing.run("semanticgis:statbanktable", {"TABLE": "https://www.statistikbanken.dk/FOLK1A", "SELECTION": "KØN=1,2", "TIME_ROWS": False, "OUTPUT": "memory:"})
L = r["OUTPUT"]; names = [f.name() for f in L.fields()]; print("   ", names, L.featureCount())
expect("municipalities with women and men as columns", (names, L.featureCount() > 100), (["OMRAADE_kode", "OMRAADE", "Maend", "Kvinder"], True))
r = processing.run("semanticgis:statbanktable", {"TABLE": "POSTNR1", "SELECTION": "", "TIME_ROWS": False, "OUTPUT": "memory:"})
L = r["OUTPUT"]; expect("all postcodes (the long selection that gave 404)", (L.featureCount() > 1000, [f.name() for f in L.fields()][-1]), (True, "INDHOLD"))
try:
    processing.run("semanticgis:statbanktable", {"TABLE": "FOLK1A", "SELECTION": "KØN=9", "OUTPUT": "memory:"}); expect("a wrong code is refused", False)
except Exception as e:
    expect("a wrong code is refused with its reason", "KØN has no value 9" in str(e))
app.exitQgis(); print("failures:", fails); sys.exit(bool(fails))
