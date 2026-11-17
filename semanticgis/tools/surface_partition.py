"""Surface partition: divide the ground of a study area into polygons (road, pavement, green …) from the edges in
existing line and polygon layers, in two steps with hand editing in between.

1. Build lines: every visible line and polygon layer becomes lines, clipped to the study area with its boundary
   added, snapped and split where they cross; loose line ends (gaps) are marked.
2. Polygons: the edited lines become polygons with a 'surface' drop-down field and a pale plan colour per class;
   footprints can mark buildings, centre lines (GeoDanmark Vejmidte) can give each polygon a hint.
"""
from qgis.core import (
    Qgis, QgsCategorizedSymbolRenderer, QgsCoordinateTransform, QgsEditorWidgetSetup, QgsFeature, QgsFeatureRequest,
    QgsFeatureSink, QgsField, QgsFields, QgsFillSymbol, QgsGeometry, QgsPointXY, QgsProcessing,
    QgsProcessingAlgorithm, QgsProcessingException, QgsProcessingLayerPostProcessorInterface,
    QgsProcessingParameterBoolean, QgsProcessingParameterDistance, QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterField, QgsProcessingParameterNumber,
    QgsProcessingParameterVectorLayer, QgsProject, QgsRendererCategory, QgsSpatialIndex, QgsVectorLayer,
)

from .aoi_wkt import DOUBLE_TYPE
from .compat import DOUBLE, INTEGER64, STRING

LINE, POLYGON = Qgis.GeometryType.Line, Qgis.GeometryType.Polygon

# Classes offered in the drop-down, with a pale plan colour each
CLASSES = [
    ("road", "#d9d9d9"), ("cycle path", "#e6d3c3"), ("footpath / pavement", "#eeeeee"), ("plaza", "#f3eee6"),
    ("parking", "#cfcfcf"), ("green", "#dfe8d2"), ("water", "#c9d9e6"), ("building", "#ffffff"), ("other", "#f7f7f7"),
]


def find_dangles(line_geoms, tolerance=0.001):
    """End points that meet no other line end: the gaps that stop areas from closing (or overshoots)."""
    ends = {}
    for g in line_geoms:
        for part in (g.asMultiPolyline() if g.isMultipart() else [g.asPolyline()]):
            if len(part) < 2:
                continue
            for p in (part[0], part[-1]):
                ends.setdefault((round(p.x() / tolerance), round(p.y() / tolerance)), [p, 0])[1] += 1
    return [QgsPointXY(p) for p, n in ends.values() if n == 1]


def snap_ends(lines, tol):
    """Move each line END to the nearest vertex (or else the nearest point on a line) within tol. Only ends are
    moved, so shapes are not distorted."""
    index = QgsSpatialIndex()
    for i, (g, _) in enumerate(lines):
        index.addFeature(i, g.boundingBox())
    result = []
    for i, (g, src) in enumerate(lines):
        pts = g.asPolyline()
        if len(pts) < 2:
            result.append((g, src))
            continue
        for end in (0, -1):
            p = pts[end]
            rect = QgsGeometry.fromPointXY(p).boundingBox().buffered(tol)
            best_d, best_pt = None, None
            for j in index.intersects(rect):
                if j == i:
                    continue
                other = lines[j][0]
                vpt, _, _, _, vd2 = other.closestVertex(p)
                vd = vd2 ** 0.5
                if vd <= tol and (best_d is None or vd < best_d - 1e-9):
                    best_d, best_pt = vd, QgsPointXY(vpt)
                sd2, spt, _, _ = other.closestSegmentWithContext(p)
                sd = sd2 ** 0.5
                if sd <= tol and (best_d is None or sd < best_d * 0.5):
                    best_d, best_pt = sd, QgsPointXY(spt)
            if best_pt is not None:
                pts[end] = best_pt
        result.append((QgsGeometry.fromPolylineXY(pts), src))
    return result


def dangle_sink(alg, parameters, name, context, crs, points):
    fields = QgsFields()
    fields.append(QgsField("check", STRING))
    sink, dest = alg.parameterAsSink(parameters, name, context, fields, Qgis.WkbType.Point, crs)
    if sink is None:
        return None, 0
    for pt in points:
        f = QgsFeature(fields)
        f.setGeometry(QgsGeometry.fromPointXY(pt))
        f["check"] = "loose end"
        sink.addFeature(f, QgsFeatureSink.FastInsert)
    return dest, len(points)


class BuildPartitionLines(QgsProcessingAlgorithm):
    AREA, ONLY_VISIBLE, SNAP, OUTPUT, DANGLES = "AREA", "ONLY_VISIBLE", "SNAP", "OUTPUT", "DANGLES"

    def name(self): return "partitionlines"
    def displayName(self): return "Surface partition 1: build lines (and mark gaps)"
    def group(self): return "Vector geometry"
    def groupId(self): return "vectorgeometry"
    def tags(self): return ["partition", "surface", "polygonize", "lines", "dangles", "gaps", "kerb", "vejkant",
                            "urban design", "plan", "topology"]
    def createInstance(self): return BuildPartitionLines()

    def shortHelpString(self):
        return ("Step 1 of 2 for dividing the ground of a study area into surfaces (road, pavement, green …).<br><br>"
                "Merges all <b>visible line and polygon layers</b> into one line layer: polygons (e.g. buildings) "
                "become outlines, everything is clipped to the study area and its boundary is added, so all areas "
                "close at the edge. Lines are snapped together within the snap tolerance and split where they "
                "cross. The field src_layer tells which layer each line came from.<br><br>"
                "<b>Gaps</b>: the second output marks loose line ends. Close each gap by editing the line layer "
                "(with snapping on), or delete overshoots. Then run <i>Surface partition 2: polygons from lines</i>.<br><br>"
                "Point layers and the study area layer itself are ignored.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(self.AREA, "Study area (polygon layer)",
                                                            [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterBoolean(self.ONLY_VISIBLE, "Only visible layers", True))
        self.addParameter(QgsProcessingParameterDistance(self.SNAP, "Snap tolerance (0 = no snapping)", 0.3,
                                                         self.AREA, minValue=0.0))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Partition lines", QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(self.DANGLES, "Gaps (loose line ends)", QgsProcessing.TypeVectorPoint))

    def processAlgorithm(self, parameters, context, feedback):
        area = self.parameterAsVectorLayer(parameters, self.AREA, context)
        if area is None or area.featureCount() == 0:
            raise QgsProcessingException("The study area layer is empty: draw a polygon in it first.")
        only_visible = self.parameterAsBool(parameters, self.ONLY_VISIBLE, context)
        snap = self.parameterAsDouble(parameters, self.SNAP, context)
        crs = area.crs()
        project = context.project() or QgsProject.instance()
        area_geom = QgsGeometry.unaryUnion([f.geometry() for f in area.getFeatures()])
        engine = QgsGeometry.createGeometryEngine(area_geom.constGet())
        engine.prepareGeometry()

        lines = []   # (line geometry, source layer name)
        for node in project.layerTreeRoot().findLayers():
            layer = node.layer()
            if (not isinstance(layer, QgsVectorLayer) or not layer.isValid() or not layer.isSpatial()
                    or layer.id() == area.id() or (only_visible and not node.isVisible())):
                continue
            gtype = layer.geometryType()
            if gtype not in (LINE, POLYGON):
                continue
            feedback.pushInfo(f"Collecting {layer.name()} …")
            xform = QgsCoordinateTransform(layer.crs(), crs, context.transformContext())
            request = QgsFeatureRequest().setFilterRect(
                xform.transformBoundingBox(area_geom.boundingBox(), Qgis.TransformDirection.Reverse))
            n = 0
            for f in layer.getFeatures(request):
                if feedback.isCanceled():
                    return {}
                g = QgsGeometry(f.geometry())
                if g.isNull() or g.isEmpty():
                    continue
                g.transform(xform)
                if gtype == POLYGON:
                    g = g.convertToType(LINE, True)
                    if g is None or g.isNull():
                        continue
                if not engine.intersects(g.constGet()):
                    continue
                g = g.intersection(area_geom)
                for part in g.asGeometryCollection():
                    if part.type() == LINE and part.length() > 0:
                        lines.append((part, layer.name()))
                        n += 1
            feedback.pushInfo(f"  {n} line parts")

        for part in area_geom.convertToType(LINE, True).asGeometryCollection():
            lines.append((part, "study_area"))
        if len(lines) <= 1:
            raise QgsProcessingException("No visible line or polygon layers inside the study area.")

        if snap > 0:
            feedback.pushInfo(f"Snapping line ends within {snap} …")
            lines = snap_ends(lines, snap)
        feedback.pushInfo("Splitting lines where they cross …")
        union = QgsGeometry.unaryUnion([g for g, _ in lines])
        pieces = [p for p in union.asGeometryCollection() if p.length() > 0]

        index = QgsSpatialIndex()
        for i, (g, _) in enumerate(lines):
            index.addFeature(i, g.boundingBox())
        fields = QgsFields()
        fields.append(QgsField("src_layer", STRING))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, Qgis.WkbType.LineString, crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        for p in pieces:   # each piece gets the name of the layer it came from
            mid = p.interpolate(p.length() / 2)
            src, best = "", None
            for i in index.intersects(mid.buffer(0.05, 4).boundingBox()):
                d = lines[i][0].distance(mid)
                if best is None or d < best:
                    best, src = d, lines[i][1]
            f = QgsFeature(fields)
            f.setGeometry(p)
            f["src_layer"] = src
            sink.addFeature(f, QgsFeatureSink.FastInsert)

        ddest, n_gaps = dangle_sink(self, parameters, self.DANGLES, context, crs, find_dangles(pieces))
        feedback.pushInfo(f"Done: {len(pieces)} line segments, {n_gaps} loose ends to check.")
        return {self.OUTPUT: dest, self.DANGLES: ddest}


class _Styler(QgsProcessingLayerPostProcessorInterface):
    """The 'surface' drop-down and a pale colour per class; unclassified polygons outlined in red."""
    instance = None

    def postProcessLayer(self, layer, context, feedback):
        idx = layer.fields().indexOf("surface")
        if idx < 0:
            return
        layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup("ValueMap", {"map": [{n: n} for n, _ in CLASSES]}))
        cats = [QgsRendererCategory(n, QgsFillSymbol.createSimple(
            {"color": c, "outline_color": "#9a9a9a", "outline_width": "0.1"}), n) for n, c in CLASSES]
        cats.append(QgsRendererCategory(None, QgsFillSymbol.createSimple(
            {"color": "#ffffff00", "outline_color": "#e31a1c", "outline_width": "0.3"}), "not classified"))
        layer.setRenderer(QgsCategorizedSymbolRenderer("surface", cats))
        layer.triggerRepaint()

    @staticmethod
    def create():
        _Styler.instance = _Styler()   # Processing does not keep a reference
        return _Styler.instance


class PolygonizePartition(QgsProcessingAlgorithm):
    INPUT, BUILDINGS, REMOVE_BUILDINGS, CENTERLINES, CL_FIELD, MIN_AREA, OUTPUT, DANGLES = (
        "INPUT", "BUILDINGS", "REMOVE_BUILDINGS", "CENTERLINES", "CL_FIELD", "MIN_AREA", "OUTPUT", "DANGLES")

    def name(self): return "partitionpolygons"
    def displayName(self): return "Surface partition 2: polygons from lines"
    def group(self): return "Vector geometry"
    def groupId(self): return "vectorgeometry"
    def tags(self): return ["partition", "surface", "polygonize", "classify", "urban design", "plan", "vejmidte"]
    def createInstance(self): return PolygonizePartition()

    def shortHelpString(self):
        return ("Step 2 of 2. Makes area polygons from the edited lines of <i>Surface partition 1</i>. Only closed "
                "rings become polygons: if an area is missing there is still a gap in the lines (see the Gaps "
                "output).<br><br>"
                "<b>Buildings</b> (optional): polygons inside a building footprint are set to 'building', or "
                "removed.<br>"
                "<b>Centre lines</b> (optional): each polygon gets a <i>hint</i> from the centre line running longest "
                "through it, e.g. the road type from GeoDanmark Vejmidte, to speed up classifying.<br><br>"
                "The result has a <b>surface</b> drop-down field and a pale plan colour per class: classify by "
                "editing the field. Polygons not yet classified are outlined in red.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(self.INPUT, "Partition lines (from step 1, edited)",
                                                              [QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterFeatureSource(self.BUILDINGS, "Buildings (optional)",
                                                              [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterBoolean(self.REMOVE_BUILDINGS, "Remove building polygons instead of marking them", False))
        self.addParameter(QgsProcessingParameterFeatureSource(self.CENTERLINES, "Centre lines for hints (optional, e.g. Vejmidte)",
                                                              [QgsProcessing.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterField(self.CL_FIELD, "Centre line field to copy as hint",
                                                      parentLayerParameterName=self.CENTERLINES, optional=True))
        self.addParameter(QgsProcessingParameterNumber(self.MIN_AREA, "Drop slivers smaller than (m²)", DOUBLE_TYPE, 0.5, False, 0.0))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Surface polygons", QgsProcessing.TypeVectorPolygon))
        self.addParameter(QgsProcessingParameterFeatureSink(self.DANGLES, "Remaining gaps", QgsProcessing.TypeVectorPoint,
                                                            optional=True, createByDefault=True))

    def load_index(self, parameters, name, context, crs, field=None):
        source = self.parameterAsSource(parameters, name, context)
        if source is None:
            return None, None, None
        xform = QgsCoordinateTransform(source.sourceCrs(), crs, context.transformContext())
        index, geoms, vals = QgsSpatialIndex(), {}, {}
        for k, f in enumerate(source.getFeatures()):
            if not f.hasGeometry():
                continue
            g = QgsGeometry(f.geometry())
            g.transform(xform)
            geoms[k] = g
            if field:
                vals[k] = f[field]
            index.addFeature(k, g.boundingBox())
        return index, geoms, vals

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))
        crs = source.sourceCrs()
        geoms = [QgsGeometry(f.geometry()) for f in source.getFeatures() if f.hasGeometry() and not f.geometry().isEmpty()]
        if not geoms:
            raise QgsProcessingException("The line layer is empty.")

        feedback.pushInfo(f"Noding {len(geoms)} lines …")
        union = QgsGeometry.unaryUnion(geoms)
        feedback.setProgress(30)
        feedback.pushInfo("Building polygons …")
        polys = [p for p in QgsGeometry.polygonize([union]).asGeometryCollection() if p.type() == POLYGON]
        feedback.setProgress(60)

        bld_index, bld_geoms, _ = self.load_index(parameters, self.BUILDINGS, context, crs)
        field = self.parameterAsString(parameters, self.CL_FIELD, context)
        cl_index, cl_geoms, cl_vals = self.load_index(parameters, self.CENTERLINES, context, crs, field)
        remove_b = self.parameterAsBool(parameters, self.REMOVE_BUILDINGS, context)
        min_area = self.parameterAsDouble(parameters, self.MIN_AREA, context)

        fields = QgsFields()
        for name, typ in (("pid", INTEGER64), ("surface", STRING), ("hint", STRING), ("area_m2", DOUBLE)):
            fields.append(QgsField(name, typ))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, Qgis.WkbType.Polygon, crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        n_out = n_bld = n_sliver = 0
        for i, p in enumerate(polys):
            if feedback.isCanceled():
                return {}
            a = p.area()
            if a < min_area:
                n_sliver += 1
                continue
            surface, hint = None, None
            if bld_index is not None:
                inside = p.pointOnSurface()
                if any(bld_geoms[j].contains(inside) for j in bld_index.intersects(inside.boundingBox())):
                    surface = "building"
                    n_bld += 1
                    if remove_b:
                        continue
            if cl_index is not None and surface is None:
                best = 0
                for j in cl_index.intersects(p.boundingBox()):
                    inter = cl_geoms[j].intersection(p)
                    if not inter.isEmpty() and inter.length() > best:
                        best, hint = inter.length(), cl_vals.get(j)
            n_out += 1
            f = QgsFeature(fields)
            f.setGeometry(p)
            f.setAttributes([n_out, surface, None if hint is None else str(hint), round(a, 1)])
            sink.addFeature(f, QgsFeatureSink.FastInsert)
            feedback.setProgress(60 + 35 * i / max(len(polys), 1))

        results = {self.OUTPUT: dest}
        ddest, n_gaps = dangle_sink(self, parameters, self.DANGLES, context, crs, find_dangles(union.asGeometryCollection()))
        if ddest:
            results[self.DANGLES] = ddest
        if context.willLoadLayerOnCompletion(dest):
            context.layerToLoadOnCompletionDetails(dest).setPostProcessor(_Styler.create())
        feedback.pushInfo(f"Done: {n_out} polygons ({n_bld} buildings{' removed' if remove_b else ''}, "
                          f"{n_sliver} slivers dropped), {n_gaps} loose ends remaining.")
        return results
