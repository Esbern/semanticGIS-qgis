"""Area of interest as WKT: one polygon around a layer (or its selected features), in WGS 84, ready to paste
into a web portal such as the Copernicus Browser.

Portals like the Copernicus Browser take an area of interest as WKT or GeoJSON in WGS 84 (EPSG:4326) only, and
QGIS has no single step that gives one: Copy features as WKT keeps the layer's CRS and every vertex. This tool
buffers in metres (in a projection centred on the data, so any input CRS works), reprojects to WGS 84, reduces
the shape to a bounding box, a convex hull or a simplified outline that still contains the data, and copies the
WKT to the clipboard.
"""
import math
import re

from qgis.core import (
    Qgis, QgsRectangle, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsFeatureRequest, QgsFeatureSink,
    QgsField, QgsFields, QgsGeometry, QgsProcessingAlgorithm, QgsProcessingException, QgsProcessingOutputString,
    QgsProcessingParameterBoolean, QgsProcessingParameterCrs, QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterFileDestination, QgsProcessingParameterNumber,
    QgsProject, QgsWkbTypes,
)

from .compat import DOUBLE, INTEGER64, STRING

SHAPES = ["Bounding box (rectangle)", "Convex hull", "Outline (simplified, follows the features)"]
BBOX, HULL, OUTLINE = range(3)
WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

try:
    INTEGER_TYPE, DOUBLE_TYPE = Qgis.ProcessingNumberParameterType.Integer, Qgis.ProcessingNumberParameterType.Double
except AttributeError:  # QGIS < 3.36
    INTEGER_TYPE, DOUBLE_TYPE = QgsProcessingParameterNumber.Integer, QgsProcessingParameterNumber.Double

try:
    NO_THREADING = Qgis.ProcessingAlgorithmFlag.NoThreading
except AttributeError:  # QGIS < 3.36
    NO_THREADING = QgsProcessingAlgorithm.FlagNoThreading


def local_metric_crs(lon, lat):
    """An azimuthal equidistant projection centred on the data: distances in metres are right near the centre."""
    return QgsCoordinateReferenceSystem.fromProj(
        f"+proj=aeqd +lat_0={lat:.6f} +lon_0={lon:.6f} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs")


def vertex_count(geometry):
    return sum(1 for _ in geometry.vertices())


def simplified_outline(geometry, max_vertices):
    """Simplify until at most max_vertices, then grow by the tolerance so the outline still contains the input
    (Douglas–Peucker moves no edge further than the tolerance)."""
    if vertex_count(geometry) <= max_vertices:
        return geometry
    extent = geometry.boundingBox()
    tolerance = max(extent.width(), extent.height()) / 2000
    for _ in range(30):
        candidate = geometry.simplify(tolerance).buffer(tolerance, 2, Qgis.EndCapStyle.Round, Qgis.JoinStyle.Miter, 2)
        candidate = candidate.simplify(tolerance / 10).makeValid()
        if not candidate.isEmpty() and vertex_count(candidate) <= max_vertices:
            return candidate
        tolerance *= 1.6
    return geometry.convexHull()


def to_clipboard(text):
    """Copy when QGIS runs with its interface (not in qgis_process or a headless script)."""
    try:
        from qgis.utils import iface
        if iface is None:
            return False
        from qgis.PyQt.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
        return True
    except Exception:
        return False


def show_window(wkt, copied):
    """A window with the WKT in a text box, to select, check or copy again (QGIS interface only)."""
    try:
        from qgis.utils import iface
        if iface is None:
            return
        from qgis.PyQt.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QLabel, QPlainTextEdit,
                                         QPushButton, QVBoxLayout)
        dialog = QDialog(iface.mainWindow())
        dialog.setWindowTitle("Area of interest (WKT, WGS 84)")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Paste this where the portal asks for an area of interest. "
                                + ("It is already on the clipboard." if copied else "")))
        text = QPlainTextEdit(wkt)
        text.setReadOnly(True)
        text.selectAll()
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(wkt))
        buttons.addButton(copy, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(640, 260)
        dialog.exec()
    except Exception:
        pass


class AreaOfInterestWkt(QgsProcessingAlgorithm):
    def name(self): return "aoiwkt"
    def displayName(self): return "Area of interest as WKT (WGS 84)"
    def group(self): return "Vector geometry"
    def groupId(self): return "vectorgeometry"
    def tags(self): return ["wkt", "aoi", "area of interest", "copernicus", "browser", "sentinel", "wgs84", "4326",
                            "bounding box", "extent", "convex hull", "clipboard", "download", "polygon"]
    def flags(self): return super().flags() | NO_THREADING  # the clipboard belongs to the main thread
    def createInstance(self): return AreaOfInterestWkt()

    def shortHelpString(self):
        return ("Makes one polygon around a layer, or its selected features, and gives it as WKT in WGS 84 "
                "(EPSG:4326, longitude latitude), copied to the clipboard. Paste it where a web portal asks for an area "
                "of interest, for example in the Copernicus Browser (Create an area of interest › Upload).\n\n"
                "Shape: a bounding box is the simplest and what most portals expect; a convex hull or an outline "
                "follows the features more closely, so a download covers less. The outline is simplified to at most "
                "the given number of vertices, and stays outside the features.\n\n"
                "Buffer: a margin in metres around the features, whatever the layer's CRS.\n\n"
                "Rectangle aligned to: the CRS in which the bounding box has straight, axis-parallel sides; by default "
                "the layer's. Choose the CRS you will download in (e.g. UTM 32N, EPSG:32632, for Sentinel-2 over "
                "Denmark), so that the download is a rectangle in that CRS. In WGS 84 the box then looks slightly "
                "tilted, which is right.\n\n"
                "The polygon is also added as a layer, so you can check it on the map, and can be saved as a .wkt "
                "file for portals that want a file upload.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            "INPUT", "Layer (tick 'Selected features only' to use the selection)"))
        self.addParameter(QgsProcessingParameterEnum("SHAPE", "Shape", SHAPES, defaultValue=BBOX))
        self.addParameter(QgsProcessingParameterNumber("BUFFER", "Buffer (metres)", DOUBLE_TYPE, defaultValue=0, minValue=0))
        self.addParameter(QgsProcessingParameterCrs(
            "ALIGN_CRS", "Bounding box: rectangle aligned to CRS (empty = the layer's CRS)", optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            "MAX_VERTICES", "Most vertices in an outline", INTEGER_TYPE, defaultValue=200, minValue=8))
        self.addParameter(QgsProcessingParameterNumber(
            "DECIMALS", "Decimals in the coordinates (6 ≈ 0.1 m)", INTEGER_TYPE, defaultValue=6, minValue=1, maxValue=12))
        self.addParameter(QgsProcessingParameterFeatureSink("OUTPUT", "Area of interest"))
        self.addParameter(QgsProcessingParameterFileDestination(
            "WKT_FILE", "Save the WKT as a file", "WKT files (*.wkt)", optional=True, createByDefault=False))
        self.addParameter(QgsProcessingParameterBoolean("SHOW", "Show the WKT in a window", defaultValue=True))
        self.addOutput(QgsProcessingOutputString("WKT", "WKT (WGS 84)"))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, "INPUT", context)
        shape = self.parameterAsEnum(parameters, "SHAPE", context)
        buffer = self.parameterAsDouble(parameters, "BUFFER", context)
        max_vertices = self.parameterAsInt(parameters, "MAX_VERTICES", context)
        decimals = self.parameterAsInt(parameters, "DECIMALS", context)
        wkt_file = self.parameterAsFileOutput(parameters, "WKT_FILE", context)
        show = self.parameterAsBool(parameters, "SHOW", context)
        if source is None:
            raise QgsProcessingException("No input layer.")
        if not source.sourceCrs().isValid():
            raise QgsProcessingException("The layer has no coordinate reference system; set one first (Layer › Set CRS).")

        transform_context = context.transformContext() if context else QgsProject.instance().transformContext()
        to_wgs84 = QgsCoordinateTransform(source.sourceCrs(), WGS84, transform_context)
        centre = to_wgs84.transformBoundingBox(source.sourceExtent()).center() if not source.sourceExtent().isEmpty() else None

        geometries = []
        request = QgsFeatureRequest().setNoAttributes()
        for feature in source.getFeatures(request):
            if feedback.isCanceled():
                return {}
            if feature.hasGeometry() and not feature.geometry().isEmpty():
                geometries.append(QgsGeometry(feature.geometry()))
        if not geometries:
            raise QgsProcessingException("The layer (or the selection) has no features with a geometry.")
        if centre is None:
            g = QgsGeometry(geometries[0]); g.transform(to_wgs84); centre = g.centroid().asPoint()

        # Work in metres around the data's centre, so the buffer is right in any CRS.
        metric = local_metric_crs(centre.x(), centre.y())
        to_metric = QgsCoordinateTransform(source.sourceCrs(), metric, transform_context)
        metric_to_wgs84 = QgsCoordinateTransform(metric, WGS84, transform_context)
        for g in geometries:
            g.transform(to_metric)
        if shape == OUTLINE:
            area = QgsGeometry.unaryUnion(geometries)
        else:
            area = QgsGeometry.collectGeometry(geometries).convexHull()  # bbox and hull of a buffer = of the hull
        if area.type() != QgsWkbTypes.PolygonGeometry and buffer <= 0:
            buffer = 1.0  # points and lines need some area
            feedback.pushInfo("Points or lines without a buffer: a 1 m buffer is used to give the area a surface.")
        if buffer > 0:
            area = area.buffer(buffer, 16)
        if shape != OUTLINE:  # straight edges in metres are curves in degrees: add vertices before reprojecting
            extent = area.boundingBox()
            area = area.densifyByDistance(max(extent.width(), extent.height(), 1.0) / 100)

        # Rounding to the chosen decimals may move an edge inwards by half a unit: grow the shape by one unit first.
        unit = 10 ** -decimals
        align = self.parameterAsCrs(parameters, "ALIGN_CRS", context) if parameters.get("ALIGN_CRS") else source.sourceCrs()
        aligned = shape == BBOX and not align.isGeographic()
        if aligned:
            # The rectangle is drawn in the alignment CRS and its sides densified, so it stays a rectangle there
            # when the portal reprojects the WGS 84 polygon back.
            area.transform(QgsCoordinateTransform(metric, align, transform_context))
            box = area.boundingBox()
            box.grow(2 * unit * 111_320)  # more than the rounding, in metres
            rect = QgsGeometry.fromRect(box).densifyByCount(3)
            rect.transform(QgsCoordinateTransform(align, WGS84, transform_context))
            area = rect
            feedback.pushInfo(f"Rectangle aligned to {align.authid() or align.description()}.")
        else:
            area.transform(metric_to_wgs84)
        if shape == BBOX and not aligned:
            box = area.boundingBox()
            area = QgsGeometry.fromRect(QgsRectangle(
                math.floor(box.xMinimum() / unit) * unit, math.floor(box.yMinimum() / unit) * unit,
                math.ceil(box.xMaximum() / unit) * unit, math.ceil(box.yMaximum() / unit) * unit))
        elif shape == HULL:
            area = area.convexHull().buffer(unit, 1, Qgis.EndCapStyle.Square, Qgis.JoinStyle.Miter, 2).convexHull()
        elif shape == OUTLINE:
            area = simplified_outline(area.makeValid(), max_vertices)
            area = area.buffer(unit, 1, Qgis.EndCapStyle.Square, Qgis.JoinStyle.Miter, 2)

        wkt = re.sub(r"^[A-Za-z]+", lambda m: m.group(0).upper(), area.asWkt(decimals))  # POLYGON, as most parsers expect
        sink_fields = QgsFields()
        for name, kind in (("shape", STRING), ("buffer_m", DOUBLE), ("vertices", INTEGER64), ("wkt", STRING)):
            sink_fields.append(QgsField(name, kind))
        sink, dest = self.parameterAsSink(parameters, "OUTPUT", context, sink_fields, area.wkbType(), WGS84)
        out = QgsFeature(sink_fields)
        out.setGeometry(area)
        out.setAttributes([SHAPES[shape].split(" (")[0], buffer, vertex_count(area), wkt])
        sink.addFeature(out, QgsFeatureSink.FastInsert)

        if wkt_file:
            with open(wkt_file, "w", encoding="utf-8") as f:
                f.write(wkt + "\n")
        copied = to_clipboard(wkt)
        if show:
            show_window(wkt, copied)
        extent = area.boundingBox()
        feedback.pushInfo(f"{vertex_count(area)} vertices; lon {extent.xMinimum():.4f} to {extent.xMaximum():.4f}, "
                          f"lat {extent.yMinimum():.4f} to {extent.yMaximum():.4f}; {len(wkt):,} characters.")
        if copied:
            feedback.pushInfo("Copied to the clipboard.")
        feedback.pushInfo(wkt if len(wkt) < 4000 else wkt[:4000] + " …")
        return {"OUTPUT": dest, "WKT": wkt, "WKT_FILE": wkt_file}
