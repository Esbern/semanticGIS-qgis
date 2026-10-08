"""WCS must be requested in a CRS the coverage supports, whatever the project CRS (network).

Loading OSM first makes the project Web Mercator; the DHM WCS only supports EPSG:25832 and
returns empty cells when asked for EPSG:3857. Checks the height at Himmelbjerget (~147 m).

  DATAFORDELER_API_KEY=… SEMANTICGIS_CATALOGUE=<folder or URL> python tests/wcs_crs.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY, QgsProject
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], False); app.initQgis()
from semanticgis import network; network.install()
from semanticgis.catalogue import load_catalogue
from semanticgis.layers import build_layer

from semanticgis.access import KEYS
catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
KEYS.set_profiles(catalogue.access_profiles); KEYS.use_environment(); KEYS.install()   # keys added at request time
dataset, service = next((d, s) for d in catalogue.datasets.values() for s in d.services
                        if s.type == "wcs" and s.layer_name == "dhm_terraen")
failures = 0
for variant, check in (("with CRS list", dict(service.check)), ("without CRS list", {k: v for k, v in service.check.items() if k != "crs"})):
    service.check = check
    for project_crs in ("EPSG:25832", "EPSG:3857", "EPSG:4326"):
        QgsProject.instance().setCrs(QgsCoordinateReferenceSystem(project_crs))
        layer = build_layer(service, dataset, True, project_crs)
        to_layer = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), layer.crs(), QgsProject.instance())
        value, ok = layer.dataProvider().sample(to_layer.transform(QgsPointXY(9.68397, 56.10094)), 1)
        good = ok and 140 < value < 155
        failures += not good
        print(f"{'ok  ' if good else 'FAIL'} {variant:17} project {project_crs:10} -> {layer.crs().authid():10} Himmelbjerget {value if ok else 'no value'}")
app.exitQgis()
sys.exit(1 if failures else 0)
