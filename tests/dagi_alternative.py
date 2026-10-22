"""Layers QGIS opens without geometry (Datafordeleren DAGIM) are not offered as working; the dataset
prefers the DAGI WFS entiteter alternative, which loads with geometry (network, Datafordeleren key).

  SEMANTICGIS_CATALOGUE=<folder or URL> DATAFORDELER_API_KEY=… python tests/dagi_alternative.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsWkbTypes
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], True); app.initQgis()
from qgis.PyQt.QtWidgets import QMainWindow

from semanticgis import network
from semanticgis.access import KEYS
from semanticgis.catalogue import load_catalogue
from semanticgis.dock import ROLE, STATUS_TEXT, SemanticGisDock
from semanticgis.layers import build_layer

failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


class Iface:
    def __init__(self): self.w = QMainWindow()
    def messageBar(self): return type("Bar", (), {"pushMessage": lambda *a, **k: None})()
    def mainWindow(self): return self.w


network.install()
dock = SemanticGisDock(Iface())
dock.catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
KEYS.set_profiles(dock.catalogue.access_profiles); KEYS.use_environment(); KEYS.install()

dataset = next(d for d in dock.catalogue.datasets.values() if d.title == "Kommuneinddeling (DAGI, 1:10.000)")
broken = next(s for s in dataset.services if s.status == "no-geometry")
preferred = dock._preferred(dataset)
expect("DAGIM layer marked", (broken.layer_name, STATUS_TEXT["no-geometry"]), ("dagi10:Kommuneinddeling", "no geometry in QGIS: opens as a table"))
expect("not usable", dock._usable(broken), False)
expect("preferred is the alternative", (preferred.layer_name, preferred.alternative_for), ("dagi_v001:kommuneinddeling_current", broken.id))
layer = build_layer(preferred, dataset, True, "EPSG:25832")
expect("loads with geometry", QgsWkbTypes.displayString(layer.wkbType()), "MultiPolygon")

dock.verified_only.setChecked(False); dock.rebuild()
texts = []
def walk(item):
    for i in range(item.rowCount()):
        child = item.child(i)
        if (child.data(ROLE) or ("",))[0] == "service" and child.data(ROLE)[1] == dataset.id:
            texts.append((child.text(), child.toolTip()))
        walk(child)
walk(dock.model.invisibleRootItem())
expect("tree shows the status", any("no geometry in QGIS" in t for t, _ in texts), True)
expect("tooltip explains the alternative", any("use this one instead" in tip for _, tip in texts), True)

app.exitQgis()
sys.exit(1 if failures else 0)
