"""WFS layers whose schema declares 2D geometry but whose features carry Z (GeoDanmark) load their
features (network, Datafordeleren key).

  DATAFORDELER_API_KEY=… python tests/wfs_z_geometry.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsFeatureRequest, QgsRectangle, QgsVectorLayer, QgsWkbTypes
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], False); app.initQgis()

from semanticgis import network
from semanticgis.access import KEYS
from semanticgis.layers import wfs_uri

network.install(); KEYS.set_profiles({}); KEYS.use_environment(); KEYS.install()
layer = QgsVectorLayer(wfs_uri("https://wfs.datafordeler.dk/GeoDanmarkVektor/GeoDanmark60_NOHIST_GML3/1.0.0/Wfs", "gdk60:Bykerne"), "Bykerne", "WFS")
features = list(layer.getFeatures(QgsFeatureRequest().setFilterRect(QgsRectangle(720000, 6170000, 730000, 6180000))))
failures = 0
for label, got, want in [
    ("layer type has Z", QgsWkbTypes.displayString(layer.wkbType()), "PolygonZ"),
    ("features load", len(features) > 0, True),
]:
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))
app.exitQgis()
sys.exit(1 if failures else 0)
