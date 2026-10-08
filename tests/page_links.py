"""Every node in the panel that has a web page: check that the page exists on the site (network).

  QT_QPA_PLATFORM=offscreen SEMANTICGIS_CATALOGUE=<folder or URL> python tests/page_links.py
"""
import os, sys, urllib.parse, urllib.request, urllib.error, collections
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
app = QgsApplication([], True); app.initQgis()
from qgis.PyQt.QtWidgets import QMainWindow
from semanticgis.catalogue import load_catalogue
from semanticgis.dock import ROLE, SemanticGisDock

class Iface:
    window = QMainWindow()
    def mainWindow(self): return self.window
    def messageBar(self): return None

dock = SemanticGisDock(Iface())
dock.catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
dock.verified_only.setChecked(False)
dock.rebuild()

by_kind, without = collections.defaultdict(set), collections.Counter()
def walk(item):
    for i in range(item.rowCount()):
        child = item.child(i); data = child.data(ROLE)
        page = dock.page_for(data)
        if page: by_kind[data[0]].add(dock.page_url(page))
        elif data and data[0] != "note": without[data[0]] += 1   # e.g. draft twigs
        walk(child)
walk(dock.model.invisibleRootItem())

def status(url):
    try:
        url = urllib.parse.quote(url, safe=":/")
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "semanticGIS-qgis-test"})
        return urllib.request.urlopen(req, timeout=30).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return "error"

urls = sorted({u for us in by_kind.values() for u in us})
with ThreadPoolExecutor(8) as pool:
    result = dict(zip(urls, pool.map(status, urls)))
bad = [u for u, st in result.items() if st != 200]
for kind, us in sorted(by_kind.items()):
    print(f"{kind:12} {len(us):5} pages, {sum(result[u] != 200 for u in us)} broken")
print("nodes without a page:", dict(without))
for u in bad[:15]: print("  BROKEN", result[u], u)
app.exitQgis()
sys.exit(1 if bad else 0)
