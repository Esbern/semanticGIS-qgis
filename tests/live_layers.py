"""Headless check: load the catalogue and build real layers from verified services.

Run with QGIS's Python, e.g.
  SEMANTICGIS_CATALOGUE=<folder or URL> python tests/live_layers.py [per_type]
"""
import os, sys, random, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsProject

QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], False)
app.initQgis()

from semanticgis.catalogue import load_catalogue
from semanticgis.layers import add_service_layer

source = os.environ.get("SEMANTICGIS_CATALOGUE", "https://semanticgis.org/assets/")
per_type = int(sys.argv[1]) if len(sys.argv) > 1 else 4
catalogue, _ = load_catalogue(source)
from semanticgis import network; network.install()
from semanticgis.access import KEYS
KEYS.set_profiles(catalogue.access_profiles); KEYS.use_environment(); KEYS.install()   # keys from .env, at request time
print(f"catalogue v{catalogue.version}: {len(catalogue.spheres)} spheres, {len(catalogue.leaves)} leaves, "
      f"{len(catalogue.datasets)} datasets")
print("search 'drikkevand|drinking water':", [l.id for l in catalogue.search("drinking water")])

QgsProject.instance().setCrs(__import__("qgis.core", fromlist=["QgsCoordinateReferenceSystem"]).QgsCoordinateReferenceSystem("EPSG:25832"))
random.seed(1)
candidates = {}
for leaf in catalogue.leaves.values():
    for dataset in leaf.datasets:
        for service in dataset.services:
            if service.verified and service.loadable:
                candidates.setdefault(service.type, []).append((leaf, dataset, service))
failures = 0
for kind, items in sorted(candidates.items()):
    sample = random.sample(items, min(per_type, len(items)))
    ok = 0
    for leaf, dataset, service in sample:
        t0 = time.time()
        try:
            layer = add_service_layer(service, leaf, dataset)
            ok += 1
            print(f"  ok   {kind} {time.time()-t0:5.1f}s {service.endpoint} {service.layer_name}", flush=True)
        except RuntimeError as e:
            failures += 1
            print(f"  FAIL {kind} {time.time()-t0:5.1f}s {service.endpoint} {service.layer_name}: {str(e)[:120]}", flush=True)
    print(f"{kind}: {ok}/{len(sample)} sampled layers valid ({len(items)} verified in catalogue)")
group = QgsProject.instance().layerTreeRoot().findGroup("SemanticGIS")
print("layer tree groups:", [g.name() for g in group.children()][:5], "...")
lyr = next(iter(QgsProject.instance().mapLayers().values()))
print("provenance:", {k: lyr.customProperty(k) for k in lyr.customPropertyKeys() if k.startswith("semanticgis/")})
app.exitQgis()
sys.exit(1 if failures else 0)
