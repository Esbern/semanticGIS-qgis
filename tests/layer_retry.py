"""A layer that fails in the background task but is valid on the main thread is added after one
retry (seen with Datafordeleren's WMTS in some QGIS installations); a layer that fails both
times is reported, with QGIS's full error in the log.

  python tests/layer_retry.py
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsMessageLog, QgsProject, QgsVectorLayer
app = QgsApplication([], True); app.initQgis()
from qgis.PyQt.QtCore import QThread
from qgis.PyQt.QtWidgets import QMainWindow

from semanticgis.dock import SemanticGisDock

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


import semanticgis.dock as dock_module

log = []


class RecordingLog:   # records what the dock writes to QGIS's message log
    @staticmethod
    def logMessage(message, tag="", level=None):
        log.append((tag, message))


dock_module.QgsMessageLog = RecordingLog
iface = Iface()
dock = SemanticGisDock(iface)
main_thread = QThread.currentThread()


def wait_for(count):
    deadline = time.time() + 30
    while time.time() < deadline and len(iface.bar.messages) < count:
        QgsApplication.processEvents(); time.sleep(0.02)


def only_on_main_thread():
    if QThread.currentThread() is not main_thread and QThread.currentThread() != main_thread:
        raise RuntimeError("Cannot calculate extent")
    return QgsVectorLayer("Point?crs=EPSG:25832", "retried", "memory")


def always_fails():
    raise RuntimeError("Provider is not valid")


added = []
dock._run_layer_task("Retried layer", only_on_main_thread, lambda layer: (QgsProject.instance().addMapLayer(layer), added.append(layer)))
wait_for(2)
expect("added after retry on the main thread", (iface.bar.messages[-1], [l.name() for l in added]), ("Added Retried layer", ["retried"]))
expect("background failure logged", any(tag == "SemanticGIS" and "Cannot calculate extent" in m for tag, m in log), True)

dock._run_layer_task("Broken layer", always_fails, lambda layer: None)
wait_for(4)
expect("real failure reported", iface.bar.messages[-1].startswith("Provider is not valid (details: View › Panels › Log Messages"), True)

app.exitQgis()
sys.exit(1 if failures else 0)
