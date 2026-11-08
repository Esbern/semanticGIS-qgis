"""Processing: Area of interest as WKT (WGS 84). Checks that every shape is WKT in longitude/latitude that
contains the input (and its buffer), that the outline keeps to its vertex limit, that 'Selected features only'
is honoured, that a layer already in WGS 84 is buffered in metres, and that points get an area. No network.

  python tests/aoi_wkt.py
"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), "../Resources/qgis/python/plugins"))
from qgis.core import QgsApplication, QgsFeature, QgsGeometry, QgsVectorLayer
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], False); app.initQgis()
from processing.core.Processing import Processing; Processing.initialize()
import processing
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsDistanceArea, QgsPointXY,
                       QgsProcessingFeatureSourceDefinition, QgsProject)

failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


def layer(kind, crs, wkts):
    lyr = QgsVectorLayer(f"{kind}?crs={crs}&field=name:string", "input", "memory")
    for i, wkt in enumerate(wkts):
        f = QgsFeature(lyr.fields()); f.setAttributes([str(i)]); f.setGeometry(QgsGeometry.fromWkt(wkt))
        lyr.dataProvider().addFeature(f)
    QgsProject.instance().addMapLayer(lyr)
    return lyr


def in_wgs84(lyr, buffer=0):
    """The input (buffered in its own metric CRS) in WGS 84, to test containment."""
    t = QgsCoordinateTransform(lyr.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
    geoms = [f.geometry() for f in lyr.getFeatures()]
    g = QgsGeometry.unaryUnion(geoms)
    if buffer:
        g = g.buffer(buffer, 32)
    g.transform(t)
    return g


def run(source, **kw):
    params = {"INPUT": source, "SHAPE": 0, "BUFFER": 0, "MAX_VERTICES": 200, "DECIMALS": 6, "SHOW": False, "OUTPUT": "TEMPORARY_OUTPUT"}
    params.update(kw)
    return processing.run("semanticgis:aoiwkt", params)


import semanticgis
plugin = semanticgis.classFactory(type("Iface", (), {})())
plugin.initProcessing()
alg = QgsApplication.processingRegistry().algorithmById("semanticgis:aoiwkt")
expect("tool in its group", (alg.displayName(), alg.group()), ("Area of interest as WKT (WGS 84)", "Vector geometry"))

# Two fields near Roskilde in UTM 32N, one of them a 400-vertex circle.
circle = QgsGeometry.fromPointXY(QgsPointXY(690000, 6170000)).buffer(500, 100).asWkt()
fields = layer("Polygon", "EPSG:25832", [circle, "POLYGON((692000 6171000, 693500 6171000, 693500 6172200, 692000 6171000))"])

r = run(fields)
wkt = r["WKT"]
aoi = QgsGeometry.fromWkt(wkt)
expect("bbox is a WKT polygon in capitals", wkt.startswith("POLYGON (("), True)
c = aoi.centroid().asPoint()
expect("coordinates are longitude latitude", (11.0 < c.x() < 13.0, 55.0 < c.y() < 56.5), (True, True))
back = QgsGeometry(aoi); back.transform(QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), fields.crs(), QgsProject.instance()))
expect("bbox is a rectangle in the layer's CRS (UTM 32N)", back.area() / QgsGeometry.fromRect(back.boundingBox()).area() > 0.9999, True)
expect("and its sides lie on the data's extent", [round(v) for v in back.boundingBox().toRectF().getCoords()] if hasattr(back.boundingBox(), "toRectF") else None,
       [round(v) for v in QgsGeometry.unaryUnion([f.geometry() for f in fields.getFeatures()]).boundingBox().toRectF().getCoords()])
wgs_box = QgsGeometry.fromWkt(run(fields, ALIGN_CRS="EPSG:4326")["WKT"])
expect("bbox aligned to WGS 84 has 5 vertices", sum(1 for _ in wgs_box.vertices()), 5)
expect("WGS 84 bbox contains the input", wgs_box.contains(in_wgs84(fields)), True)
back = QgsGeometry(wgs_box); back.transform(QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), fields.crs(), QgsProject.instance()))
expect("and is tilted in UTM 32N", back.area() / QgsGeometry.fromRect(back.boundingBox()).area() < 0.999, True)
expect("bbox contains the input", aoi.contains(in_wgs84(fields)), True)
expect("layer output holds the same polygon", next(r["OUTPUT"].getFeatures())["wkt"], wkt)
expect("6 decimals", len(wkt.split(",")[0].split(" ")[-1].split(".")[-1]), 6)

r = run(fields, BUFFER=1000)
aoi = QgsGeometry.fromWkt(r["WKT"])
expect("buffered bbox contains a 999 m buffer", aoi.contains(in_wgs84(fields, 999)), True)
d = QgsDistanceArea(); d.setEllipsoid("EPSG:7030")
to_utm = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), fields.crs(), QgsProject.instance())
inner = QgsGeometry.fromWkt(run(fields)["WKT"]); inner.transform(to_utm)
outer = QgsGeometry(aoi); outer.transform(to_utm)
expect("buffer 1000 m to the west, in UTM 32N", round(inner.boundingBox().xMinimum() - outer.boundingBox().xMinimum()), 1000)

r = run(fields, SHAPE=1, BUFFER=200)
aoi = QgsGeometry.fromWkt(r["WKT"])
expect("hull contains a 199 m buffer", aoi.contains(in_wgs84(fields, 199)), True)
expect("hull smaller than bbox", aoi.area() < QgsGeometry.fromWkt(run(fields, BUFFER=200)["WKT"]).area(), True)

r = run(fields, SHAPE=2, MAX_VERTICES=40)
aoi = QgsGeometry.fromWkt(r["WKT"])
n = sum(1 for _ in aoi.vertices())
expect("outline within 40 vertices", n <= 40, True)
expect("outline contains the input", aoi.contains(in_wgs84(fields)), True)
expect("outline keeps the two fields apart", aoi.isMultipart(), True)

fields.selectByExpression("\"name\" = '1'")
r = run(QgsProcessingFeatureSourceDefinition(fields.id(), selectedFeaturesOnly=True))
aoi = QgsGeometry.fromWkt(r["WKT"])
expect("selection only: the circle is outside", aoi.intersects(in_wgs84(layer("Polygon", "EPSG:25832", [circle]))), False)

wgs = layer("Polygon", "EPSG:4326", ["POLYGON((12.0 55.6, 12.1 55.6, 12.1 55.65, 12.0 55.6))"])
aoi = QgsGeometry.fromWkt(run(wgs, BUFFER=500)["WKT"]).boundingBox()  # geographic layer: box in WGS 84
west = d.measureLine(QgsPointXY(aoi.xMinimum(), 55.6), QgsPointXY(12.0, 55.6))
expect("a WGS 84 layer is buffered in metres", round(west / 10) * 10, 500)

points = layer("Point", "EPSG:25832", ["POINT(690000 6170000)", "POINT(690000 6170000)"])
aoi = QgsGeometry.fromWkt(run(points)["WKT"])
expect("one point gives a small area", 0 < aoi.area(), True)

out = os.path.join(tempfile.mkdtemp(), "aoi.wkt")
r = run(fields, WKT_FILE=out)
expect("WKT file written", open(out).read().strip(), r["WKT"])

print("all good" if not failures else f"{failures} failure(s)")
sys.exit(failures)
