"""Flatten a snapshot series: one layer of pieces whose attributes hold every period, newest geometry first.

A register published as yearly snapshots (Marker 2020 … 2026) is often digitised anew each year, so the same
field boundary lies a little differently in each year. A plain union of the years gives thousands of slivers
along those boundaries. This tool starts from the newest period, which is usually the most correct, and adds
the older periods one at a time: each older period is first snapped to the boundaries built so far, then
overlaid (union), and pieces narrower than the tolerance are merged into the neighbour they share the longest
boundary with. Real changes (a field split, extended or ploughed up) are wider than that and stay.

The result has one column per chosen field and period, named after the period: y2026_crop, y2024_crop, … .
The tool only arranges the fields the user picks; joins (English names, simplified classes) come first.
"""
import re

from qgis.core import (
    QgsFeature, QgsFeatureRequest, QgsFeatureSink, QgsField, QgsFields, QgsGeometry, QgsProcessing,
    QgsProcessingAlgorithm, QgsProcessingException, QgsProcessingParameterDistance, QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterField, QgsProcessingParameterString,
    QgsProcessingUtils, QgsSpatialIndex, QgsVectorLayer, QgsWkbTypes,
)

try:
    FIELD_ANY = QgsProcessingParameterField.Any
except AttributeError:  # QGIS 4 enums
    from qgis.core import Qgis
    FIELD_ANY = Qgis.ProcessingFieldParameterDataType.Any

SNAP_ALIGN_NODES = 0   # native:snapgeometries: prefer aligning nodes, insert extra vertices where required


def period_text(value):
    """A period as text for a column name: 2026, 2026_03, 2026_03_01 (dates), week_12."""
    for method, fmt in (("toString", "yyyy_MM_dd"),):
        if hasattr(value, "isValid") and hasattr(value, method) and type(value).__name__ in ("QDate", "QDateTime"):
            value = value.date() if type(value).__name__ == "QDateTime" else value
            return value.toString(fmt)
    text = str(value)
    if re.fullmatch(r"-?\d+\.0+", text):   # 2026.0 from a decimal field
        text = text.split(".")[0]
    return re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_") or "period"


def period_prefix(value, prefix="y"):
    """The column prefix for a period: y2026_ (years), w2026_12_ (weeks), m2026_03_ (months). A name may not
    start with a digit, so an empty prefix becomes 'p'."""
    prefix = re.sub(r"[^0-9A-Za-z_]+", "_", prefix or "")
    name = f"{prefix}{period_text(value)}_"
    return name if not name[0].isdigit() else "p" + name


def _natural_key(value):
    """Order periods by time: dates and numbers by value, text by its numbers (week 5 before week 12)."""
    if type(value).__name__ in ("QDate", "QDateTime"):
        return (0, value.toString("yyyyMMddHHmmss") if type(value).__name__ == "QDateTime" else value.toString("yyyyMMdd"))
    try:
        return (1, float(value))
    except (TypeError, ValueError):
        parts = re.split(r"(\d+)", str(value))
        return (2, tuple(int(p) if p.isdigit() else p.lower() for p in parts))


def sort_periods(values):
    """Newest first."""
    return sorted(values, key=_natural_key, reverse=True)


def is_sliver(geometry, width):
    """Narrower than `width` everywhere: nothing is left after shrinking by half the width."""
    if width <= 0 or geometry.isEmpty():
        return False
    shrunk = geometry.buffer(-width / 2.0, 4)
    return shrunk is None or shrunk.isEmpty() or shrunk.area() <= 0


def memory_layer(crs, fields, name):
    layer = QgsVectorLayer(f"MultiPolygon?crs={crs.authid() or 'EPSG:4326'}", name, "memory")
    if not crs.authid():
        layer.setCrs(crs)
    layer.dataProvider().addAttributes(fields.toList())
    layer.updateFields()
    return layer


def polygons(geometry):
    """The polygon parts of a geometry, valid, as one multipolygon (None if nothing is left)."""
    if geometry is None or geometry.isEmpty():
        return None
    geometry = geometry.makeValid()
    if geometry.type() != QgsWkbTypes.PolygonGeometry:
        parts = [p for p in geometry.asGeometryCollection() if p.type() == QgsWkbTypes.PolygonGeometry] \
            if geometry.isMultipart() or QgsWkbTypes.flatType(geometry.wkbType()) == QgsWkbTypes.GeometryCollection else []
        if not parts:
            return None
        geometry = QgsGeometry.unaryUnion(parts)
    geometry.convertToMultiType()
    return geometry if not geometry.isEmpty() and geometry.area() > 0 else None


def without_overlaps(features):
    """Features of one period with overlaps removed: an area claimed twice stays with the first feature."""
    index, taken, out = QgsSpatialIndex(), {}, []
    for f in features:
        g = polygons(f.geometry())
        if g is None:
            continue
        for j in index.intersects(g.boundingBox()):
            if g.intersects(taken[j]):
                g = polygons(g.difference(taken[j]))
                if g is None:
                    break
        if g is None:
            continue
        nf = QgsFeature(f)
        nf.setGeometry(g)
        nf.setId(len(out))
        taken[len(out)] = g
        index.addFeature(nf)
        out.append(nf)
    return out


def eliminate_slivers(features, width, feedback):
    """Merge every piece narrower than `width` into the neighbour with the longest shared boundary.
    Repeats until no sliver has a neighbour left; a sliver with no neighbour is kept (it is all there is)."""
    geoms = {i: f.geometry() for i, f in enumerate(features)}
    merged = 0
    for _ in range(10):
        index = QgsSpatialIndex()
        for i, g in geoms.items():
            f = QgsFeature(i)
            f.setGeometry(g)
            index.addFeature(f)
        slivers = sorted((i for i, g in geoms.items() if is_sliver(g, width)), key=lambda i: geoms[i].area())
        done = 0
        for i in slivers:
            if feedback.isCanceled() or i not in geoms:
                continue
            g = geoms[i]
            best, best_length = None, 0.0
            for j in index.intersects(g.boundingBox()):
                if j == i or j not in geoms:
                    continue
                shared = g.intersection(geoms[j])
                length = shared.length() if shared and not shared.isEmpty() else 0.0
                if length > best_length:
                    best, best_length = j, length
            if best is None:
                continue
            union = geoms[best].combine(g)
            geoms[best] = polygons(union) or geoms[best]
            del geoms[i]
            done += 1
        merged += done
        if not done:
            break
    out = []
    for i, g in geoms.items():
        f = QgsFeature(features[i])
        f.setGeometry(g)
        out.append(f)
    return out, merged


def overlay(current, older, fields, n_current, feedback):
    """Union of two layers' features as polygon pieces: current ∩ older (both attribute sets), current − older,
    older − current. `current` is a partition (no overlaps); so are the older features. Lines and points that
    an overlay leaves where polygons only touch are dropped."""
    index = QgsSpatialIndex()
    olds = {}
    for i, f in enumerate(older):
        g = QgsFeature(i)
        g.setGeometry(f.geometry())
        index.addFeature(g)
        olds[i] = f
    covered = {i: [] for i in olds}   # parts of each older feature that a current piece covers
    pieces = []

    def piece(geometry, current_attrs, older_attrs):
        g = polygons(geometry)
        if g is None:
            return
        f = QgsFeature(fields)
        f.setAttributes(current_attrs + older_attrs)
        f.setGeometry(g)
        pieces.append(f)

    empty_old = [None] * (fields.count() - n_current)
    for c in current:
        if feedback.isCanceled():
            break
        cg = c.geometry()
        rest = QgsGeometry(cg)
        for i in index.intersects(cg.boundingBox()):
            og = olds[i].geometry()
            if not cg.intersects(og):
                continue
            inter = cg.intersection(og)
            if polygons(inter) is None:
                continue
            piece(inter, c.attributes()[:n_current], olds[i].attributes())
            covered[i].append(inter)
            rest = rest.difference(og)
        piece(rest, c.attributes()[:n_current], empty_old)
    for i, o in olds.items():
        rest = o.geometry()
        if covered[i]:
            rest = rest.difference(QgsGeometry.unaryUnion(covered[i]))
        piece(rest, [None] * n_current, o.attributes())
    return pieces


class FlattenSnapshots(QgsProcessingAlgorithm):
    INPUT, PERIOD, FIELDS, TOLERANCE, PREFIX, OUTPUT = "INPUT", "PERIOD_FIELD", "FIELDS", "TOLERANCE", "PREFIX", "OUTPUT"

    def name(self): return "flattensnapshots"
    def displayName(self): return "Flatten snapshot series (newest geometry first)"
    def group(self): return "Vector overlay"
    def groupId(self): return "vectoroverlay"
    def createInstance(self): return FlattenSnapshots()

    def shortHelpString(self):
        return (
            "Turns a layer with several periods (for example Marker 2020–2026, from <i>Download snapshot series</i>) "
            "into one layer of pieces, with one column per chosen field and period: <b>y2026_crop</b>, "
            "<b>y2024_crop</b>, …<br><br>"
            "The newest period is kept as it is. Each older period is snapped to the boundaries built so far, "
            "overlaid, and pieces narrower than the <b>tolerance</b> (slivers from boundaries digitised a little "
            "differently) are merged into the neighbour they share the longest boundary with. Differences wider "
            "than the tolerance are kept as real change.<br><br>"
            "<b>Period field</b>: the field that says which period a feature belongs to (snapshot_year).<br>"
            "<b>Fields to keep</b>: the fields to carry into the result, per period. Join what you need first "
            "(English names, simplified classes); this tool does no joins.<br>"
            "<b>Column prefix</b>: put before the period in the column names: y for years (y2026_crop), m for months "
            "(m2026_03_crop), w for weeks. Periods are ordered by time: dates and numbers by value, text by the "
            "numbers in it (week 5 before week 12).<br>"
            "<b>Tolerance</b>: in the layer's units (metres in a projected CRS). Strips narrower than this, such as "
            "a 2 m field margin with a 2 m tolerance, are merged away too, so choose it below the narrowest real "
            "feature you care about.<br>"
            "A column is empty where that period had no feature. Overlaps between features of the same period are "
            "given to the first feature. Where an older feature's outer edge is snapped onto a newer boundary, the "
            "narrow strip between them is no longer covered, so the result can cover slightly less than the input.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(self.INPUT, "Snapshot series (polygons)", [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterField(self.PERIOD, "Period field", "snapshot_year", self.INPUT, FIELD_ANY))
        self.addParameter(QgsProcessingParameterField(self.FIELDS, "Fields to keep (one column per period)", None, self.INPUT,
                                                      FIELD_ANY, allowMultiple=True))
        self.addParameter(QgsProcessingParameterDistance(self.TOLERANCE, "Tolerance (snapping and sliver width)", 2.0, self.INPUT, False, 0))
        self.addParameter(QgsProcessingParameterString(self.PREFIX, "Column prefix (y for years, m for months, w for weeks …)", "y",
                                                       optional=True))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Flattened", QgsProcessing.TypeVectorPolygon))

    def run_child(self, alg, params, context, feedback):
        import processing
        result = processing.run(alg, params, context=context, feedback=feedback, is_child_algorithm=True)
        return QgsProcessingUtils.mapLayerFromString(result["OUTPUT"], context)

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        period_field = self.parameterAsString(parameters, self.PERIOD, context)
        as_strings = getattr(self, "parameterAsStrings", None) or self.parameterAsFields   # parameterAsFields before QGIS 3.40
        keep = as_strings(parameters, self.FIELDS, context)
        tolerance = self.parameterAsDouble(parameters, self.TOLERANCE, context)
        prefix = self.parameterAsString(parameters, self.PREFIX, context)
        if not keep:
            raise QgsProcessingException("Choose at least one field to keep.")
        if source.sourceCrs().isGeographic():
            feedback.reportError("The layer is in degrees, so the tolerance is in degrees too. Reproject to a metric CRS (e.g. EPSG:25832) first.")
        fields_in = source.fields()
        period_index = fields_in.indexOf(period_field)
        if period_index < 0:
            raise QgsProcessingException(f"No field {period_field}.")

        # features per period
        by_period = {}
        for f in source.getFeatures():
            by_period.setdefault(f.attribute(period_index), []).append(f)
        by_period.pop(None, None)
        periods = sort_periods(list(by_period))
        if not periods:
            raise QgsProcessingException("No features with a period.")
        prefixes = {p: period_prefix(p, prefix) for p in periods}
        if len(set(prefixes.values())) < len(prefixes):
            raise QgsProcessingException("Two periods give the same column name; check the period field.")
        feedback.pushInfo(f"Periods, newest first: {', '.join(map(str, periods))}")

        # one layer per period, with prefixed fields and no overlaps inside the period
        crs = source.sourceCrs()
        period_layers = []
        for p in periods:
            fields = QgsFields()
            for name in keep:
                field = QgsField(fields_in.field(name))
                field.setName(prefixes[p] + name)
                fields.append(field)
            layer = memory_layer(crs, fields, str(p))
            renamed = []
            for f in by_period[p]:
                nf = QgsFeature(fields)
                nf.setAttributes([f[name] for name in keep])
                nf.setGeometry(f.geometry())
                renamed.append(nf)
            out = without_overlaps(renamed)
            layer.dataProvider().addFeatures(out)
            period_layers.append(layer)
            feedback.pushInfo(f"{p}: {len(out)} features")

        result = period_layers[0]
        steps = len(period_layers)
        for k, older in enumerate(period_layers[1:], start=1):
            if feedback.isCanceled():
                break
            feedback.setProgress(100 * k / steps)
            snapped = self.run_child("native:snapgeometries", {"INPUT": older, "REFERENCE_LAYER": result, "TOLERANCE": tolerance,
                                                               "BEHAVIOR": SNAP_ALIGN_NODES, "OUTPUT": "TEMPORARY_OUTPUT"}, context, feedback)
            # snapping can leave a polygon invalid (self-touching, collapsed) or push two features of the
            # period onto each other: repair, and give overlaps to one of them again
            fixed = without_overlaps(list(snapped.getFeatures()))
            fields = QgsFields(result.fields())
            for field in older.fields():
                fields.append(field)
            union = overlay(list(result.getFeatures()), fixed, fields, result.fields().count(), feedback)
            pieces = []
            for f in union:
                for part in f.geometry().asGeometryCollection():
                    g = polygons(part)
                    if g is not None:
                        nf = QgsFeature(f)
                        nf.setGeometry(g)
                        pieces.append(nf)
            pieces, merged = eliminate_slivers(pieces, tolerance, feedback)
            feedback.pushInfo(f"+ {periods[k]}: {len(pieces)} pieces, {merged} slivers merged")
            layer = memory_layer(crs, fields, f"flat{k}")
            layer.dataProvider().addFeatures(pieces)
            result = layer

        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, result.fields(), QgsWkbTypes.MultiPolygon, crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        for f in result.getFeatures():
            sink.addFeature(f, QgsFeatureSink.FastInsert)
        return {self.OUTPUT: dest}
