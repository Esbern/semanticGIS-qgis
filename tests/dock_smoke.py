"""Offscreen smoke test of the dock panel with a stub iface.

  QT_QPA_PLATFORM=offscreen SEMANTICGIS_CATALOGUE=<folder or URL> python tests/dock_smoke.py
"""
import os, sys
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
    def pushMessage(self, title, text, level=None, duration=None): self.messages.append(f"{title}: {text} [{level}]")

class StubIface:
    def __init__(self):
        self.window = QMainWindow(); self.bar = StubBar()
    @property
    def messages(self): return self.bar.messages
    def messageBar(self): return self.bar
    def mainWindow(self): return self.window

from semanticgis.catalogue import load_catalogue
from semanticgis.dock import ROLE, SemanticGisDock

iface = StubIface()
dock = SemanticGisDock(iface)
dock.catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
dock.rebuild()
root = dock.model.invisibleRootItem()
print("spheres:", [root.child(i).text() for i in range(root.rowCount())])
planning = next(root.child(i) for i in range(root.rowCount()) if root.child(i).text() == "Anthroposphere")
twigs = [planning.child(i).text() for i in range(planning.rowCount())]
print("anthroposphere twigs:", twigs)
dock.search.setText("protection")
root = dock.model.invisibleRootItem()  # clear() replaces the root item in Qt6
print("search 'protection':", [root.child(i).text() for i in range(root.rowCount())])

def first_service(item):
    for r in range(item.rowCount()):
        child = item.child(r); data = child.data(ROLE)
        if data and data[0] == "service" and "WMS ·" in child.text(): return child
        found = first_service(child)
        if found: return found
service_item = first_service(root)
print("activating:", service_item.text())
dock.activate(service_item.index())
# Layers load in a background QgsTask; pump the event loop until it reports back.
import time
deadline = time.time() + 120
while time.time() < deadline and not any("Added" in m or "[2]" in m for m in iface.messages):
    QgsApplication.processEvents()
    time.sleep(0.05)
print("messages:", iface.messages)
print("layers in project:", [l.name() for l in QgsProject.instance().mapLayers().values()])
dock.search.setText("")
root = dock.model.invisibleRootItem()
count = lambda item: item.rowCount() + sum(count(item.child(i)) for i in range(item.rowCount()))
verified_rows = count(root)
dock.verified_only.setChecked(False)
root = dock.model.invisibleRootItem()
print("tree rows verified-only:", verified_rows, "all services:", count(root))
app.exitQgis()
