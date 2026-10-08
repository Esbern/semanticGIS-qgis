"""Offscreen smoke test of the dock panel with a stub iface (needs network for the layers).

  QT_QPA_PLATFORM=offscreen SEMANTICGIS_CATALOGUE=<folder or URL> python tests/dock_smoke.py
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsProject
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], True)
app.initQgis()
from qgis.PyQt.QtWidgets import QMainWindow


class StubBar:
    def __init__(self): self.messages = []
    def pushMessage(self, title, text, level=None, duration=None): self.messages.append(text)


class StubIface:
    def __init__(self): self.window = QMainWindow(); self.bar = StubBar()
    def messageBar(self): return self.bar
    def mainWindow(self): return self.window


from semanticgis.catalogue import load_catalogue
from semanticgis.dock import ROLE, SemanticGisDock


def children(item):
    return [item.child(i) for i in range(item.rowCount())]


def find(item, predicate):
    for child in children(item):
        if predicate(child):
            return child
        hit = find(child, predicate)
        if hit:
            return hit


def count(item):
    return item.rowCount() + sum(count(c) for c in children(item))


def pump(condition, seconds=120):
    deadline = time.time() + seconds
    while time.time() < deadline and not condition():
        QgsApplication.processEvents(); time.sleep(0.05)
    return condition()


iface = StubIface()
dock = SemanticGisDock(iface)
dock.catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
t = time.time(); dock.rebuild(); built = time.time() - t
root = dock.model.invisibleRootItem()
tops = {c.text(): c for c in children(root)}
print(f"top level ({built:.2f}s, {count(root)} rows):", list(tops))
print("SPHERE:", [c.text() for c in children(tops["SPHERE"])])
print("Reference Framework:", [c.text() for c in children(tops["Reference Framework"])])
print("Classical:", [c.text() for c in children(tops["Classical Classifications"])],
      "| INSPIRE themes:", children(tops["Classical Classifications"])[0].rowCount())
print("Collection Methods:", [c.text() for c in children(tops["Collection Methods"])])
print("Collections:", [c.text() for c in children(tops["Datasets by Collection"])], "→",
      len(children(children(tops["Datasets by Collection"])[0])), "registers")
print("Owners:", tops["Datasets by Owner"].rowCount(), "e.g.", [c.text() for c in children(tops["Datasets by Owner"])][:3])

layers = lambda: QgsProject.instance().mapLayers()
service = find(tops["Datasets by Owner"], lambda c: (c.data(ROLE) or ("",))[0] == "service" and c.text().startswith("WMS"))
print("activating owner service:", service.text())
dock.activate(service.index())
assert pump(lambda: len(layers()) == 1), iface.bar.messages
refgeom = find(tops["Reference Framework"], lambda c: (c.data(ROLE) or ("",))[0] == "refgeom" and "60 000 000" in c.text())
print("activating reference geometry:", refgeom.parent().text(), refgeom.text())
dock.activate(refgeom.index())
assert pump(lambda: len(layers()) == 2), iface.bar.messages
for layer in layers().values():
    print("  layer:", layer.name(), "| features:", layer.featureCount() if hasattr(layer, "featureCount") else "-",
          "| group:", QgsProject.instance().layerTreeRoot().findLayer(layer.id()).parent().name())
dock.search.setText("natura")
root = dock.model.invisibleRootItem()
print("search 'natura':", [c.text() for c in children(root)])
print("messages:", iface.bar.messages)
app.exitQgis()
