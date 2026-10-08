"""Offscreen: the panel marks services that need a key (🔒 without, 🔑 with), refuses to add one
without its key, offers 'Set your key' and 'How to get' on the right-click menu, and the settings
dialog has a field per access profile. Nothing is stored (keys are set for the session only).

  QT_QPA_PLATFORM=offscreen SEMANTICGIS_CATALOGUE=<folder or URL> python tests/dock_keys.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsProject
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
app = QgsApplication([], True); app.initQgis()
from qgis.PyQt.QtWidgets import QMainWindow, QMenu

from semanticgis.access import KEYS
from semanticgis.catalogue import load_catalogue
from semanticgis.dock import ROLE, SemanticGisDock
from semanticgis.settings import SettingsDialog


class StubBar:
    def __init__(self): self.messages = []
    def pushMessage(self, title, text, level=None, duration=None): self.messages.append(text)


class StubIface:
    def __init__(self): self.window = QMainWindow(); self.bar = StubBar()
    def messageBar(self): return self.bar
    def mainWindow(self): return self.window


failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


def find(item, predicate):
    for i in range(item.rowCount()):
        child = item.child(i)
        if predicate(child):
            return child
        hit = find(child, predicate)
        if hit:
            return hit


iface = StubIface()
dock = SemanticGisDock(iface)
dock.catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
KEYS.set_profiles(dock.catalogue.access_profiles)
KEYS._keys, KEYS.loaded = {}, True   # no keys, nothing read from or written to the settings
dock.verified_only.setChecked(False)  # CARTO is unverified until checked with a key
dock.rebuild()

carto_item = lambda: find(dock.model.invisibleRootItem(), lambda i: (i.data(ROLE) or ("",))[0] == "bmservice" and i.data(ROLE)[1] == "carto-positron")
expect("locked marker", carto_item().text()[:2], "🔒 ")
expect("tooltip says how", "carto.com/basemaps/apikey" in carto_item().toolTip(), True)

basemap = dock._basemap("carto-positron")
dock.add_basemap(basemap)
expect("refused without key", iface.bar.messages[-1].startswith("This service needs your own CARTO basemaps API key"), True)
expect("no layer added", len(QgsProject.instance().mapLayers()), 0)

menu = QMenu()
dock._key_actions(menu, carto_item().data(ROLE))
expect("menu without key", [a.text() for a in menu.actions() if a.text()], ["Set your CARTO basemaps API key…", "How to get a CARTO basemaps API key"])

KEYS.use({"carto": "session-only"})
dock.rebuild()
expect("key marker", carto_item().text()[:2], "🔑 ")
menu = QMenu()
dock._key_actions(menu, carto_item().data(ROLE))
expect("menu with key", [a.text() for a in menu.actions() if a.text()][0], "Change your CARTO basemaps API key…")

dock.verified_only.setChecked(True)   # unverified only for lack of a key the user now has
dock.rebuild()
expect("offered under verified only", (carto_item() is not None, dock._preferred(basemap) is not None), (True, True))

dialog = SettingsDialog(dock.catalogue.access_profiles)
expect("settings fields", sorted(dialog.keys), ["carto", "datafordeler", "dataforsyningen"])
expect("settings shows key", dialog.keys["carto"].text(), "session-only")

dhm = find(dock.model.invisibleRootItem(), lambda i: (i.data(ROLE) or ("",))[0] == "service" and "datafordeler" in (i.data(ROLE)[2] or ""))
expect("datafordeler service locked", dhm.text()[:2], "🔒 ")

app.exitQgis()
sys.exit(1 if failures else 0)
