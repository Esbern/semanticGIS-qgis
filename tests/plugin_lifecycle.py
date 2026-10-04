"""classFactory -> initGui -> open dock -> unload, with a stub iface (offscreen)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsSettings
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
app = QgsApplication([], True); app.initQgis()
from qgis.PyQt.QtWidgets import QMainWindow

class Stub:
    def __init__(self): self.window = QMainWindow(); self.calls = []; self.msgs = []
    def __getattr__(self, name):
        def record(*args): self.calls.append(name)
        return record
    def mainWindow(self): return self.window
    def messageBar(self):
        stub = self
        class Bar:
            def pushMessage(self, *a, **k): stub.msgs.append(a[1])
        return Bar()
    def addDockWidget(self, area, dock): self.calls.append("addDockWidget"); self.window.addDockWidget(area, dock)

QgsSettings().setValue("semanticgis/catalogue_source", os.environ["SEMANTICGIS_CATALOGUE"])
import semanticgis
iface = Stub()
plugin = semanticgis.classFactory(iface)
plugin.initGui()
plugin.action.setChecked(True)          # opens the dock and starts loading the catalogue
import time
deadline = time.time() + 60
while time.time() < deadline and plugin.dock.catalogue is None:
    QgsApplication.processEvents(); time.sleep(0.05)
print("dock status:", plugin.dock.status.text())
print("spheres in tree:", plugin.dock.model.rowCount())
plugin.unload()
print("iface calls:", iface.calls)
QgsSettings().remove("semanticgis/catalogue_source")
app.exitQgis()
