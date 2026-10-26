"""Research-group servers never hold up the panel: the public catalogue shows at once, each server
loads on its own, unreachable ones are reported, and the tree keeps the user's open folders when
group data arrives. Throw-away QGIS settings folder; the two servers fail on purpose (one name does
not exist, one address never answers).

  SEMANTICGIS_CATALOGUE=<folder or URL> python tests/extension_loading.py
"""
import os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.PyQt.QtCore import QCoreApplication, QSettings
from qgis.core import QgsApplication, QgsSettings
QCoreApplication.setOrganizationName("SemanticGISTestLoading")
app = QgsApplication([], True); app.initQgis()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from qgis.PyQt.QtWidgets import QMainWindow

from semanticgis import extensions as ext, network
from semanticgis.catalogue import Dataset, Service
from semanticgis.dock import ROLE, SemanticGisDock
from semanticgis.settings import SETTINGS_PREFIX

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


def pump_until(condition, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline and not condition():
        QgsApplication.processEvents(); time.sleep(0.05)
    return condition()


def find(item, predicate):
    for i in range(item.rowCount()):
        child = item.child(i)
        if predicate(child):
            return child
        hit = find(child, predicate)
        if hit:
            return hit


network.install()
QgsSettings().setValue(SETTINGS_PREFIX + "catalogue_source", os.environ["SEMANTICGIS_CATALOGUE"])
QgsSettings().setValue(SETTINGS_PREFIX + "extension_servers", "nonexistent-server.invalid, 10.255.255.1")
iface = Iface()
dock = SemanticGisDock(iface)

start = time.time()
dock.reload()
shown = pump_until(lambda: dock.model.rowCount() > 0, 10)
expect("public catalogue shown at once", (shown, time.time() - start < 10), (True, True))
expect("status says it is waiting", "waiting for" in dock.status.text(), True)

# Open a folder, then let the servers fail.
root = dock.model.invisibleRootItem()
sphere = find(root, lambda i: i.text() == "SPHERE")
dock.tree.setExpanded(sphere.index(), True)
done = pump_until(lambda: "waiting for" not in dock.status.text(), dock.EXTENSION_TIMEOUT_MS / 1000 + 15)
expect("both servers finished", done, True)
expect("status names them", "not reachable: nonexistent-server.invalid, 10.255.255.1" in dock.status.text()
       or "not reachable: 10.255.255.1, nonexistent-server.invalid" in dock.status.text(), True)
expect("warned in the message bar", sum("Research group server" in m for m in iface.bar.messages), 2)
expect("public tree intact, no Extensions branch", (dock.model.rowCount() > 5, find(root, lambda i: i.text() == "Extensions") is None), (True, True))

# A server answers while the user browses: folders stay open.
leaf_item = find(dock.model.invisibleRootItem(), lambda i: (i.data(ROLE) or ("",))[0] == "leaf" and i.data(ROLE)[1] == "elevation")
parents = []
item = leaf_item
while item is not None:
    parents.append(item)
    item = item.parent()
for item in reversed(parents):
    dock.tree.setExpanded(item.index(), True)
extension = ext.Extension(server="example.org", project_id=1, path="group/project", title="Example group", web_url="https://example.org/group/project", branch="main")
dataset = Dataset(id="ext:example.org:group/project:private", title="Private terrain", page="https://example.org", leaves=["elevation"],
                  services=[Service(id="cog|x|", type="cog", url="https://example.org/x.tif", layer=None, title="x", auth="none", check={"status": "ok"})],
                  extension=extension)
extension.datasets.append(dataset)
ext.merge(dock.catalogue, [extension])
dock.rebuild(keep_view=True)
leaf_item = find(dock.model.invisibleRootItem(), lambda i: (i.data(ROLE) or ("",))[0] == "leaf" and i.data(ROLE)[1] == "elevation")
expect("opened leaf still open", dock.tree.isExpanded(leaf_item.index()), True)
expect("group data under it", any(leaf_item.child(i).text() == "🔐 Private terrain" for i in range(leaf_item.rowCount())), True)
expect("Extensions branch appeared", find(dock.model.invisibleRootItem(), lambda i: i.text() == "Extensions") is not None, True)

# A reload while a server is still slow ignores that server's stale answer.
generation = dock._generation
dock.reload()
expect("new generation", dock._generation, generation + 1)

app.exitQgis()
sys.exit(1 if failures else 0)
