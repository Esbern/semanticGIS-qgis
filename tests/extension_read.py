"""Extensions on a real GitLab server (network): the plugin finds the projects with the topic,
reads their STAC catalogs, puts their datasets under the leaves they name and loads a COG from the
project's package registry with the token sent as a header, never in the layer or project.
Runs in a throw-away QGIS settings folder.

  SEMANTICGIS_CATALOGUE=<folder or URL> EXTENSION_SERVER=<gitlab host> EXTENSION_PROJECT=<group/project> \
  EXTENSION_TOKEN=… python tests/extension_read.py
The project must hold the DHM Himmelbjerget snapshot (see the extension prototype notes).
"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
scratch = tempfile.mkdtemp(prefix="sgis-ext-")
os.environ["QGIS_AUTH_DB_DIR_PATH"] = scratch
from qgis.PyQt.QtCore import QCoreApplication, QSettings
from qgis.core import QgsApplication, QgsPointXY, QgsProject, QgsSettings
QCoreApplication.setOrganizationName("SemanticGISTest")
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], True); app.initQgis()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, scratch)
assert QgsSettings().fileName().startswith(scratch)

from osgeo import gdal
from qgis.PyQt.QtWidgets import QMainWindow
from semanticgis import extensions as ext
from semanticgis import network
from semanticgis.access import KEYS
from semanticgis.catalogue import load_catalogue
from semanticgis.layers import build_layer, place_layer

server = os.environ["EXTENSION_SERVER"]
project = os.environ["EXTENSION_PROJECT"]
token = os.environ["EXTENSION_TOKEN"]
failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


network.install()
profile = ext.server_profile(server)
KEYS.set_extra_profiles({profile.id: profile})
KEYS.install()

catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])

# Without the token: the snapshot file is refused (the package registry needs it).
gdal.VSICurlClearCache()
found, problems = ext.load_extensions([server], network.get, catalogue.leaves)
print("  without token:", [e.path for e in found], problems[:1])

# With the token.
KEYS.use({profile.id: token})
gdal.VSICurlClearCache()
found, problems = ext.load_extensions([server], network.get, catalogue.leaves)
expect("projects found by topic", [e.path for e in found], [project])
expect("problems", problems + [p for e in found for p in e.problems], [])
ext.merge(catalogue, found)
dataset = found[0].datasets[0]
expect("dataset", dataset.title, "DHM terrain, Himmelbjerget (snapshot 2026-10-15)")
expect("linked to leaf", dataset.leaves, ["elevation"])
expect("listed under the leaf", dataset in catalogue.leaves["elevation"].datasets, True)
expect("service", (dataset.services[0].type, dataset.services[0].access, dataset.services[0].loadable), ("cog", profile.id, True))
expect("attribution", dataset.attribution.line("download"), "Indeholder data fra Klimadatastyrelsen, Danmarks Højdemodel - Terræn, hentet 15.10.2026")
expect("page is the GitLab file", dataset.page.startswith(f"https://{server}/{project}/-/blob/main/collections/"), True)

layer = place_layer(build_layer(dataset.services[0], dataset), dataset.services[0], catalogue.leaves["elevation"], dataset)
value, ok = layer.dataProvider().sample(QgsPointXY(542546, 6217525), 1)
expect("COG from GitLab: Himmelbjerget ~147 m", ok and 140 < value < 155, True)
expect("token not in layer source", token in layer.source(), False)
expect("layer records its profile", layer.customProperty("semanticgis/access"), profile.id)
project_file = os.path.join(scratch, "project.qgs")
QgsProject.instance().write(project_file)
expect("token not in saved project", token in open(project_file, encoding="utf8").read(), False)

# The panel: an Extensions branch and the private dataset under Elevation.
from semanticgis.dock import ROLE, SemanticGisDock


class Bar:
    def pushMessage(self, *a, **k): pass


class Iface:
    def __init__(self): self.w = QMainWindow()
    def messageBar(self): return Bar()
    def mainWindow(self): return self.w


dock = SemanticGisDock(Iface())
dock.catalogue = catalogue
dock.rebuild()
root = dock.model.invisibleRootItem()
tops = [root.child(i).text() for i in range(root.rowCount())]
expect("Extensions branch", "Extensions" in tops, True)


def find(item, predicate):
    for i in range(item.rowCount()):
        child = item.child(i)
        if predicate(child):
            return child
        hit = find(child, predicate)
        if hit:
            return hit


leaf_item = find(root, lambda i: (i.data(ROLE) or ("",))[0] == "leaf" and i.data(ROLE)[1] == "elevation")
private = find(leaf_item, lambda i: i.text().startswith("🔐 "))
expect("private dataset under Elevation", private.text() if private else None, "🔐 DHM terrain, Himmelbjerget (snapshot 2026-10-15)")
expect("its service is unlocked", private.child(0).text()[:2] if private else None, "🔑 ")

KEYS.uninstall()
app.exitQgis()
sys.exit(1 if failures else 0)
