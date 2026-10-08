"""Offscreen test of the join dialog: detect and join through the UI (needs network).

  QT_QPA_PLATFORM=offscreen SEMANTICGIS_CATALOGUE=<folder or URL> python tests/join_dialog_smoke.py table.csv
"""
import os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsProject, QgsVectorLayer
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], True); app.initQgis()

import semanticgis.join_dialog as jd
from semanticgis.catalogue import load_catalogue
cache = tempfile.mkdtemp(prefix="semanticgis-refs-"); jd.cache_dir = lambda: cache

class Bar:
    def __init__(self): self.messages = []
    def pushMessage(self, title, text, *a): self.messages.append(text)
class Iface:
    def __init__(self): self.bar = Bar()
    def messageBar(self): return self.bar

def pump(condition, seconds=120):
    deadline = time.time() + seconds
    while time.time() < deadline and not condition():
        QgsApplication.processEvents(); time.sleep(0.05)
    return condition()

table = QgsVectorLayer(f"file://{sys.argv[1]}?delimiter=;&detectTypes=yes&geomType=none", os.path.basename(sys.argv[1]), "delimitedtext")
QgsProject.instance().addMapLayer(table)
catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
iface = Iface()
dialog = jd.JoinDialog(iface, catalogue)
dialog.table.setLayer(table)
dialog.run_detect()
assert pump(lambda: dialog.detect_button.isEnabled() and dialog.results.count() > 0), dialog.details.text()
print("results:", [dialog.results.item(i).text() for i in range(dialog.results.count())])
print("scales:", [dialog.scale.itemText(i) for i in range(dialog.scale.count())])
dialog.scale.setCurrentIndex(dialog.scale.count() - 1)     # coarsest scale
before = len(QgsProject.instance().mapLayers())
dialog.run_join()
assert pump(lambda: len(QgsProject.instance().mapLayers()) > before), dialog.details.text()
joined = [l for l in QgsProject.instance().mapLayers().values() if l.name() != table.name()][0]
print("joined:", joined.name(), joined.featureCount(), "features;", dialog.details.text())
print("tree:", [g.name() for g in QgsProject.instance().layerTreeRoot().findGroup("SemanticGIS").children()])
print("messages:", iface.bar.messages)
app.exitQgis()
