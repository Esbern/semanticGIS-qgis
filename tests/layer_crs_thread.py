"""Layers built in a background task keep a usable CRS after QGIS retires the worker thread
(~30 s idle): their CRS still has a definition, saving records it, and drawing reprojects them.
Without the fix the CRS definition becomes empty and saved files get 'Undefined SRS'.

  python tests/layer_crs_thread.py     (takes ~45 s; reads NUTS 1:60M from GISCO)
"""
import os, sys, sqlite3, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem as C, QgsCoordinateTransform as T, QgsPointXY, QgsProject,
                       QgsVectorFileWriter)
app = QgsApplication([], True); app.initQgis()
from qgis.PyQt.QtWidgets import QMainWindow
from semanticgis.catalogue import ReferenceGeometry, ReferenceSource
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


def pump(seconds, until=lambda: False):
    end = time.time() + seconds
    while time.time() < end and not until():
        QgsApplication.processEvents(); time.sleep(0.05)


iface = Iface()
dock = SemanticGisDock(iface)
nuts = ReferenceSource("nuts", "NUTS", None, "NUTS regions", "NUTS_ID", "2024", "EPSG:3035",
                       [ReferenceGeometry("1:60 000 000", "https://gisco-services.ec.europa.eu/distribution/v2/nuts/gpkg/NUTS_RG_60M_2024_3035.gpkg")])
dock.add_reference_layer(nuts, nuts.geometries[0])
pump(60, lambda: any(m.startswith("Added") for m in iface.bar.messages))
layer = next(iter(QgsProject.instance().mapLayers().values()))
pump(40)   # the worker thread that built the layer is retired after ~30 s idle

crs = layer.crs()
expect("CRS still EPSG:3035", crs.authid(), "EPSG:3035")
expect("CRS definition kept", len(crs.toWkt()) > 100, True)
out = os.path.join(tempfile.mkdtemp(), "nuts.gpkg")
opts = QgsVectorFileWriter.SaveVectorOptions(); opts.driverName = "GPKG"
QgsVectorFileWriter.writeAsVectorFormatV3(layer, out, QgsProject.instance().transformContext(), opts)
expect("saved file records the CRS", sqlite3.connect(out).execute("select srs_id from gpkg_geometry_columns").fetchone(), (3035,))
t = T(crs, C("EPSG:25832"), QgsProject.instance())
expect("reprojects (Copenhagen)", round(t.transform(QgsPointXY(4482727, 3622394)).x()), 724437)

app.exitQgis()
sys.exit(1 if failures else 0)
