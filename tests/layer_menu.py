"""The plugin's entries appear on the Layers panel's right-click menu through QGIS's own signal
(QgsLayerTreeView.contextMenuAboutToShow), and are gone after unload. A real layer tree view,
a stub for the rest of the interface; throw-away QGIS settings folder.

  python tests/layer_menu.py
"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.PyQt.QtCore import QCoreApplication, QSettings
from qgis.core import QgsApplication, QgsLayerTreeModel, QgsProject, QgsSettings, QgsVectorLayer
QCoreApplication.setOrganizationName("SemanticGISTest")
app = QgsApplication([], True); app.initQgis()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
assert "SemanticGISTest" in QgsSettings().fileName()
from qgis.gui import QgsLayerTreeView
from qgis.PyQt.QtWidgets import QMainWindow, QMenu

from semanticgis.plugin import SemanticGisPlugin

failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


view = QgsLayerTreeView()
model = QgsLayerTreeModel(QgsProject.instance().layerTreeRoot())   # keep a reference: the view does not own it
view.setModel(model)


class Iface:
    def __init__(self): self.window = QMainWindow()
    def __getattr__(self, name): return lambda *args, **kwargs: None
    def mainWindow(self): return self.window
    def layerTreeView(self): return view


layer = QgsVectorLayer("Point?crs=EPSG:25832", "result", "memory")
QgsProject.instance().addMapLayer(layer)
view.setCurrentLayer(layer)


def menu_texts():
    menu = QMenu()
    view.contextMenuAboutToShow.emit(menu)   # what QGIS does before showing the menu
    texts = [a.text() for a in menu.actions() if a.text()]
    for action in menu.actions():
        if action.menu():
            texts += [f"{action.text()} › {a.text()}" for a in action.menu().actions()]
    return texts


plugin = SemanticGisPlugin(Iface())
plugin.initGui()
texts = menu_texts()
expect("hooked to the Layers panel", plugin.layer_menu_signal is not None, True)
expect("copy attribution", "Copy attribution (SemanticGIS)" in texts, True)
import importlib.util
if importlib.util.find_spec("semanticgis.publish"):   # the extensions prototype's Link/Publish/Save submenu
    expect("SemanticGIS submenu", any(t.startswith("SemanticGIS › ") for t in texts), True)
plugin.unload()
expect("gone after unload", [t for t in menu_texts() if "SemanticGIS" in t], [])

app.exitQgis()
sys.exit(1 if failures else 0)
