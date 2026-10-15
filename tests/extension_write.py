"""Writing back to an extension project on a real GitLab server (network; it commits, then cleans up):
publish a slope raster derived from the project's DHM snapshot, read it back under its leaf, edit
and save its metadata, and get a refused save (not an overwrite) after a colleague's change.
Runs in a throw-away QGIS settings folder.

  SEMANTICGIS_CATALOGUE=<folder or URL> EXTENSION_SERVER=<gitlab host> EXTENSION_PROJECT=<group/project> \\
  EXTENSION_TOKEN=… python tests/extension_write.py
"""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
scratch = tempfile.mkdtemp(prefix="sgis-write-")
os.environ["QGIS_AUTH_DB_DIR_PATH"] = scratch
from qgis.PyQt.QtCore import QCoreApplication, QSettings
from qgis.core import QgsApplication, QgsPointXY, QgsProject, QgsRasterLayer, QgsSettings
QCoreApplication.setOrganizationName("SemanticGISTest")
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], True); app.initQgis()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, scratch)
assert QgsSettings().fileName().startswith(scratch)
sys.path.insert(0, "/Applications/QGIS.app/Contents/Resources/qgis/python/plugins")
import processing
from processing.core.Processing import Processing
Processing.initialize()

from qgis.PyQt.QtWidgets import QMainWindow
from semanticgis import extensions as ext, gitlab, network, publish
from semanticgis.access import KEYS
from semanticgis.catalogue import load_catalogue
from semanticgis.layers import build_layer, place_layer

server, project, token = os.environ["EXTENSION_SERVER"], os.environ["EXTENSION_PROJECT"], os.environ["EXTENSION_TOKEN"]
failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


class Bar:
    def __init__(self): self.messages = []
    def pushMessage(self, title, text, level=None, duration=None): self.messages.append(text)


class Iface:
    def __init__(self): self.w = QMainWindow(); self.bar = Bar()
    def messageBar(self): return self.bar
    def mainWindow(self): return self.w


warnings = []
publish.QMessageBox.warning = lambda parent, title, text: warnings.append((title, text))   # no modal dialogs


def wait(task, seconds=300):
    deadline = time.time() + seconds
    while task is not None and time.time() < deadline and task.status() not in (task.TaskStatus.Complete, task.TaskStatus.Terminated):
        QgsApplication.processEvents(); time.sleep(0.05)
    for _ in range(20):   # let on_finished run
        QgsApplication.processEvents(); time.sleep(0.02)


def load():
    catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
    found, problems = ext.load_extensions([server], network.get, catalogue.leaves)
    ext.merge(catalogue, found)
    return catalogue, next(e for e in found if e.path == project), problems


network.install()
profile = ext.server_profile(server)
KEYS.set_extra_profiles({profile.id: profile})
KEYS.use({profile.id: token})
KEYS.install()
iface = Iface()

catalogue, extension, problems = load()
expect("extension loads", problems, [])
snapshot = next(d for d in extension.datasets if "snapshot" in d.title.lower())
snapshot_layer = place_layer(build_layer(snapshot.services[0], snapshot), snapshot.services[0], None, snapshot)
expect("snapshot layer metadata from STAC", (snapshot_layer.metadata().title(), snapshot_layer.metadata().keywords().get("semanticgis:leaf")),
       (snapshot.stac["title"], ["elevation"]))

# A result derived from the snapshot: slope in degrees.
slope_path = os.path.join(scratch, "slope.tif")
processing.run("native:slope", {"INPUT": snapshot_layer, "Z_FACTOR": 1, "OUTPUT": slope_path})
slope = QgsRasterLayer(slope_path, "Slope Himmelbjerget")
QgsProject.instance().addMapLayer(slope)

stamp = time.strftime("%H%M%S")
title = f"Test publish: slope Himmelbjerget {stamp}"
done = []
task = publish.start_publish(iface, catalogue, slope, extension, title, "Slope (degrees) from the DHM snapshot, test of publishing.",
                             ["elevation"], snapshot, "Indeholder data fra Klimadatastyrelsen, Danmarks Højdemodel - Terræn, hentet 15.10.2026",
                             lambda: done.append(True))
wait(task)
expect("published", (bool(done), iface.bar.messages[-1].startswith(f"Published {title}")), (True, True))

# Read back as a colleague would after reloading the panel.
catalogue, extension, problems = load()
published = next((d for d in extension.datasets if d.title == title), None)
expect("listed in the catalog", published is not None, True)
expect("under Elevation", published in catalogue.leaves["elevation"].datasets if published else None, True)
expect("derived from the snapshot", published.stac.get("semanticgis:derived_from", [{}])[0].get("dataset") if published else None, snapshot.id)
remote = place_layer(build_layer(published.services[0], published), published.services[0], None, published)
value, ok = remote.dataProvider().sample(QgsPointXY(542546, 6217525), 1)
expect("published COG reads back (slope 0-60°)", ok and 0 <= value <= 60, True)
expect("checksum recorded", published.stac["assets"]["data"]["file:checksum"].startswith("1220"), True)

# Edit the description in the layer metadata and save it.
metadata = remote.metadata()
metadata.setAbstract("Slope in degrees, from the DHM snapshot. Edited in QGIS's metadata editor.")
remote.setMetadata(metadata)
done.clear()
wait(publish.save_metadata(iface, catalogue, remote, lambda: done.append(True)))
expect("metadata saved", bool(done), True)
text, _ = ext.read_file(extension, published.stac_path, network.get)
expect("saved description on GitLab", json.loads(text)["description"], "Slope in degrees, from the DHM snapshot. Edited in QGIS's metadata editor.")

# A colleague changes the same description; our next save started from the older version.
colleague = json.loads(text)
colleague["description"] = "Changed by a colleague."
gitlab.commit(extension, "Test: a colleague's edit", [{"action": "update", "file_path": published.stac_path,
              "last_commit_id": published.stac_commit, "content": json.dumps(colleague, indent=2) + "\n"}])
metadata.setAbstract("My later edit, made without reloading.")
remote.setMetadata(metadata)
done.clear()
wait(publish.save_metadata(iface, catalogue, remote, lambda: done.append(True)))
expect("conflicting save refused", (bool(done), warnings[-1][0] if warnings else None), (False, "Changed by someone else"))
expect("conflict message names the colleague's commit", "Test: a colleague's edit" in (warnings[-1][1] if warnings else ""), True)
text, catalog_commit = ext.read_file(extension, published.stac_path, network.get)
expect("colleague's version kept", json.loads(text)["description"], "Changed by a colleague.")

# Clean up: remove the test dataset's description and the catalog's link to it.
catalog_text, catalog_commit = ext.read_file(extension, extension.catalog_path, network.get)
catalog = json.loads(catalog_text)
catalog["links"] = [l for l in catalog["links"] if published.stac_path not in l.get("href", "")]
gitlab.commit(extension, f"Test: remove {title}", [
    {"action": "delete", "file_path": published.stac_path},
    {"action": "update", "file_path": extension.catalog_path, "last_commit_id": catalog_commit,
     "content": json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"},
])
catalogue, extension, problems = load()
expect("cleaned up", [d.title for d in extension.datasets if d.title.startswith("Test publish")], [])
print("  test data file left in the package registry:", published.services[0].url.rsplit("/", 1)[-1])

KEYS.uninstall()
app.exitQgis()
sys.exit(1 if failures else 0)
