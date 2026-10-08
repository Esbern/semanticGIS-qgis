"""The panel must survive being tabbed together with another dock (offscreen, no network).

  QT_QPA_PLATFORM=offscreen SEMANTICGIS_CATALOGUE=<folder or URL> python tests/dock_tabs.py
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsSettings
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
app = QgsApplication([], True); app.initQgis()
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QDockWidget, QLabel, QMainWindow


class Iface:
    def __init__(self):
        self.window = QMainWindow(); self.window.setCentralWidget(QLabel("map")); self.window.resize(1000, 700)
    def mainWindow(self): return self.window
    def messageBar(self):
        class Bar:
            def pushMessage(self, *a, **k): pass
        return Bar()
    def addDockWidget(self, area, dock): self.window.addDockWidget(area, dock)
    def removeDockWidget(self, dock): self.window.removeDockWidget(dock)
    def __getattr__(self, name): return lambda *a, **k: None


def pump(seconds=0.5):
    end = time.time() + seconds
    while time.time() < end:
        QgsApplication.processEvents(); time.sleep(0.02)


QgsSettings().setValue("semanticgis/catalogue_source", os.environ["SEMANTICGIS_CATALOGUE"])
import semanticgis
iface = Iface(); iface.window.show()
plugin = semanticgis.classFactory(iface); plugin.initGui()
plugin.action.trigger()                      # open the panel as a user would
pump(1)
other = QDockWidget("Layers"); other.setObjectName("Layers"); other.setWidget(QLabel("layers"))
iface.window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, other)
iface.window.tabifyDockWidget(plugin.dock, other)   # dock them together as tabs
other.raise_(); pump()                              # the other tab is in front
plugin.dock.visibilityChanged.emit(False)           # what Qt reports on a real desktop for the tab behind
pump()
state = lambda: f"hidden={plugin.dock.isHidden()} visible={plugin.dock.isVisible()} button={plugin.action.isChecked()}"
print("other tab in front:      ", state())
in_tabs = plugin.dock in iface.window.tabifiedDockWidgets(other)
print("still a tab next to Layers:", in_tabs)
plugin.dock.raise_(); pump()
print("our tab raised again:    ", state())
plugin.action.trigger(); pump()                     # toolbar button: close
print("closed with the button:  ", state())
plugin.action.trigger(); pump()                     # toolbar button: open again
print("reopened with the button:", state())
other.raise_(); plugin.dock.visibilityChanged.emit(False); pump()
plugin.action.trigger(); pump()                      # button while our tab is behind: bring it to front
print("button while behind:     ", state())
ok = (in_tabs and not plugin.dock.isHidden() and plugin.action.isChecked()
      and plugin.dock in iface.window.tabifiedDockWidgets(other))
plugin.unload(); QgsSettings().remove("semanticgis/catalogue_source")
app.exitQgis()
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
