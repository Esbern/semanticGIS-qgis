"""Subsets: a realisation's categorical attribute shows as "Subsets by …" under its dataset, and a
subset loads as the same WFS layer filtered on the server (network: Danmarks Miljøportal).

  SEMANTICGIS_CATALOGUE=<folder or URL> python tests/subsets.py
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsFeatureRequest, QgsProject, QgsRectangle
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], True); app.initQgis()
from qgis.PyQt.QtWidgets import QMainWindow

from semanticgis import network
from semanticgis.catalogue import load_catalogue
from semanticgis.dock import ROLE, SemanticGisDock

failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


class Bar:
    def __init__(self): self.messages = []
    def pushMessage(self, title, text, *a, **k): self.messages.append(text)


class Iface:
    def __init__(self): self.w = QMainWindow(); self.bar = Bar()
    def messageBar(self): return self.bar
    def mainWindow(self): return self.w


def find(item, predicate):
    for i in range(item.rowCount()):
        child = item.child(i)
        if predicate(child):
            return child
        hit = find(child, predicate)
        if hit:
            return hit


network.install()
iface = Iface()
dock = SemanticGisDock(iface)
dock.catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
dock.rebuild()
root = dock.model.invisibleRootItem()
leaf = find(root, lambda i: (i.data(ROLE) or ("",))[0] == "leaf" and i.data(ROLE)[1] == "nature-protection-areas")
dataset = find(leaf, lambda i: (i.data(ROLE) or ("",))[0] == "dataset" and i.data(ROLE)[1] == "sgis-001263") if leaf else None
expect("Beskyttede naturtyper under Nature Protection Areas", dataset is not None, True)
group = find(dataset, lambda i: (i.data(ROLE) or ("",))[0] == "subsets") if dataset else None
labels = [group.child(i).text() for i in range(group.rowCount())] if group else []
expect("subset group", group.text() if group else None, "Subsets by Natyp_navn")
expect("subsets", labels, ["Hede (heath)", "Overdrev (dry grassland)", "Mose (bog and fen)", "Eng (meadow)",
                           "Strandeng (salt marsh)", "Sø (lake and pond)"])
expect("subset page is the realisation", dock.page_for(group.child(0).data(ROLE)) if group else None,
       "/Realisations/Beskyttede_naturtyper_nature_protection_areas")

data = group.child(0).data(ROLE)
dock.add_subset(dock.catalogue.leaves["nature-protection-areas"], dock.catalogue.datasets[data[1]], data[2])
deadline = time.time() + 120
while time.time() < deadline and not any(m.startswith(("Added", "Cannot", "Provider")) for m in iface.bar.messages):
    QgsApplication.processEvents(); time.sleep(0.05)
layer = next((l for l in QgsProject.instance().mapLayers().values() if l.name().endswith("Hede (heath)")), None)
expect("layer added", layer.name() if layer else iface.bar.messages[-1:], "Beskyttede naturtyper — Hede (heath)")
expect("filter", layer.subsetString() if layer else None, "\"Natyp_navn\" = 'Hede'")
request = QgsFeatureRequest().setFilterRect(QgsRectangle(440000, 6200000, 600000, 6400000)).setLimit(50)   # Jutland, EPSG:25832
types = {f["Natyp_navn"] for f in layer.getFeatures(request)} if layer else set()
expect("only heath comes back", types, {"Hede"})

app.exitQgis()
sys.exit(1 if failures else 0)
