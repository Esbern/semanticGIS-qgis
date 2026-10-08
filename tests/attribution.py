"""Attribution: layers added by SemanticGIS carry their credit line, and the lines of a project
can be collected for a print layout (offline: memory layers stand in for the services).

  SEMANTICGIS_CATALOGUE=<folder or URL> python tests/attribution.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import (QgsApplication, QgsExpression, QgsExpressionContext, QgsExpressionContextUtils, QgsLayout,
                       QgsLayoutItemMap, QgsProject, QgsRectangle, QgsVectorLayer)
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
app = QgsApplication([], False); app.initQgis()
from semanticgis.attribution import map_attributions
from semanticgis.catalogue import load_catalogue
from semanticgis.layers import PROPERTY_PREFIX, place_basemap_layer, place_layer

catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


def memory(name):
    return QgsVectorLayer("Point?crs=EPSG:25832", name, "memory")


# A Datafordeleren WCS dataset (verified KDS wording), a default-wording owner, an unknown owner, a basemap.
dhm, wcs = next((d, s) for d in catalogue.datasets.values() for s in d.services if s.type == "wcs" and s.layer_name == "dhm_terraen")
mst = next(d for d in catalogue.datasets.values() if d.owner == "miljoestyrelsen" and d.services)
unknown = next(d for d in catalogue.datasets.values() if d.owner == "unknown" and d.services)
osm = next(b for b in catalogue.basemaps if b.id == "openstreetmap")

expect("DHM status", dhm.attribution.status, "verified")
expect("DHM line", dhm.attribution.line("wcs"), f"Indeholder data fra Klimadatastyrelsen, {dhm.title}, WCS-tjeneste")
expect("download line", dhm.attribution.line("download").endswith("hentet ‹dato›"), True)
expect("Miljøstyrelsen status", mst.attribution.status, "default")
expect("unknown owner text", unknown.attribution.text, "")

layers = []
for dataset, service in ((dhm, wcs), (mst, mst.services[0]), (unknown, unknown.services[0])):
    layers.append(place_layer(memory(dataset.title), service, None, dataset))
dhm2 = place_layer(memory("DHM again"), wcs, None, dhm)   # the same line twice is listed once
base = place_basemap_layer(memory("OSM"), osm, osm.services[0])
legacy = memory("Added by an older plugin version")   # no stored line: derived from the catalogue
legacy.setCustomProperty(PROPERTY_PREFIX + "dataset", dhm.id)
legacy.setCustomProperty(PROPERTY_PREFIX + "service", wcs.id)
QgsProject.instance().addMapLayer(legacy)

expect("stored on layer", layers[0].customProperty(PROPERTY_PREFIX + "attribution"), dhm.attribution.line("wcs"))
expect("metadata rights", layers[0].metadata().rights(), [dhm.attribution.line("wcs")])
lines, missing = map_attributions(layers + [dhm2, base, legacy], catalogue)
expect("map lines", lines, [dhm.attribution.line("wcs"), mst.attribution.line(mst.services[0].type), "© OpenStreetMap contributors"])
expect("missing", missing, [unknown.title])

# A layout label can follow the map with map_credits().
layout = QgsLayout(QgsProject.instance()); layout.initializeDefaults()
item = QgsLayoutItemMap(layout); item.setId("Map 1"); layout.addLayoutItem(item)
item.setLayers([layers[0], base]); item.setKeepLayerSet(True); item.zoomToExtent(QgsRectangle(0, 0, 1, 1))
context = QgsExpressionContext([QgsExpressionContextUtils.globalScope(), QgsExpressionContextUtils.projectScope(QgsProject.instance()),
                                QgsExpressionContextUtils.layoutScope(layout)])
credits = QgsExpression("map_credits('Map 1')").evaluate(context)
expect("map_credits", sorted(credits or []), sorted([dhm.attribution.line("wcs"), "© OpenStreetMap contributors"]))

app.exitQgis()
sys.exit(1 if failures else 0)
