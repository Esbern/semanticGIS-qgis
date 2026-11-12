"""Snapshot series: one register for a period of years, within an area, as one layer.

Registers such as Marker, Markblokke and Økologiske arealer are published as a new layer each year
(Marker_2008 … Marker_2026), not as one layer with dates. To follow a landscape through time, students would add
each year's WFS layer, clip it and merge them by hand, and the attributes differ between years. This tool does it
in one step: it fetches every year in the period from the WFS, only within the area (with an optional buffer),
and writes one layer with a `snapshot_year` field. Fields that only some years have are kept, empty in the other
years, and the log says which years lack which fields.

The features are fetched with plain WFS GetFeature requests limited to the area's bounding box. QGIS's WFS
provider first asks the server about the whole national layer, which takes minutes for Marker.
"""
import json
import os
import re
import tempfile
from datetime import date

from qgis.core import (
    QgsApplication, QgsBlockingNetworkRequest, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
    QgsFeatureSink, QgsField, QgsFields, QgsGeometry, QgsJsonUtils, QgsProcessing, QgsProcessingAlgorithm,
    QgsProcessingException, QgsProcessingOutputString, QgsProcessingParameterBoolean, QgsProcessingParameterCrs,
    QgsProcessingParameterEnum, QgsProcessingParameterFeatureSink, QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber, QgsProject, QgsVectorLayer, QgsWkbTypes,
)
from qgis.PyQt.QtCore import QByteArray, QUrl, QUrlQuery
from qgis.PyQt.QtNetwork import QNetworkRequest

from ..catalogue import Catalogue, SERVICES_FILE, INDEX_FILE
from ..series import find_series
from .aoi_wkt import INTEGER_TYPE, DOUBLE_TYPE, local_metric_crs
from .compat import INTEGER64, STRING

PAGE = 5000             # features per GetFeature request
MAX_FEATURES = 200000   # per year: a guard against asking for the whole country
WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
try:
    NO_ERROR = QgsBlockingNetworkRequest.ErrorCode.NoError
except AttributeError:  # older PyQGIS
    NO_ERROR = QgsBlockingNetworkRequest.NoError


# -- the catalogue -------------------------------------------------------------------------------------------

_cache = {}


def catalogue_folder():
    """Where the catalogue is read from: SEMANTICGIS_CATALOGUE (a folder, for tests) or the panel's cache."""
    return os.environ.get("SEMANTICGIS_CATALOGUE") or os.path.join(QgsApplication.qgisSettingsDirPath(), "semanticgis", "cache")


def available_series():
    """The snapshot series in the cached catalogue (the SemanticGIS panel caches it when it loads)."""
    folder = catalogue_folder()
    services, index = os.path.join(folder, SERVICES_FILE), os.path.join(folder, INDEX_FILE)
    if not (os.path.exists(services) and os.path.exists(index)):
        return []
    stamp = (os.path.getmtime(services), os.path.getmtime(index))
    if _cache.get("stamp") != stamp:
        try:
            with open(index, encoding="utf-8") as f, open(services, encoding="utf-8") as g:
                catalogue = Catalogue(json.load(f), json.load(g))
            series = find_series(catalogue.datasets.values())
        except Exception:   # an old or broken cache must not stop the tool from opening
            series = []
        _cache.update(stamp=stamp, series=series)
    return _cache["series"]


# -- WFS ------------------------------------------------------------------------------------------------------

def crs_urn(crs):
    auth, _, code = crs.authid().partition(":")
    return f"urn:ogc:def:crs:{auth}::{code}"


def decode(data):
    """Some servers send Latin-1 while declaring UTF-8 (Marker 2026 from geodata.fvm.dk)."""
    raw = bytes(data)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def get(url, feedback):
    request = QgsBlockingNetworkRequest()
    error = request.get(QNetworkRequest(url), forceRefresh=True, feedback=feedback)
    reply = request.reply()
    status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) if hasattr(QNetworkRequest, "Attribute") \
        else reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
    return error, status, reply.content(), request.errorMessage()


def post(url, body, feedback):
    request = QgsBlockingNetworkRequest()
    req = QNetworkRequest(url)
    header = QNetworkRequest.KnownHeaders.ContentTypeHeader if hasattr(QNetworkRequest, "KnownHeaders") else QNetworkRequest.ContentTypeHeader
    req.setHeader(header, "text/xml")
    error = request.post(req, QByteArray(body.encode("utf-8")), forceRefresh=True, feedback=feedback)
    reply = request.reply()
    status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) if hasattr(QNetworkRequest, "Attribute") \
        else reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
    return error, status, reply.content(), request.errorMessage()


def getfeature_url(endpoint, layer, rect, crs, start, output_format):
    url = QUrl(endpoint)
    query = QUrlQuery(url)
    for key in [k for k, _ in query.queryItems()]:
        if key.lower() in ("service", "request", "version"):
            query.removeAllQueryItems(key)
    urn = crs_urn(crs)
    for key, value in (("SERVICE", "WFS"), ("VERSION", "2.0.0"), ("REQUEST", "GetFeature"), ("TYPENAMES", layer),
                       ("SRSNAME", urn), ("COUNT", str(PAGE)),
                       ("BBOX", f"{rect.xMinimum()},{rect.yMinimum()},{rect.xMaximum()},{rect.yMaximum()},{urn}"),
                       ("OUTPUTFORMAT", output_format)):
        query.addQueryItem(key, value)
    if start:   # only when paging: GeoServer refuses STARTINDEX on views without a primary key
        query.addQueryItem("STARTINDEX", str(start))
    url.setQuery(query)
    return url


def getfeature_post(layer, rect, crs, start):
    """The same request as XML in the body: Danmarks Miljøportal's firewall blocks filters in the URL."""
    urn = crs_urn(crs)
    return (
        '<wfs:GetFeature service="WFS" version="2.0.0" xmlns:wfs="http://www.opengis.net/wfs/2.0" '
        'xmlns:fes="http://www.opengis.net/fes/2.0" xmlns:gml="http://www.opengis.net/gml/3.2" '
        f'count="{PAGE}"' + (f' startIndex="{start}"' if start else "") + ' outputFormat="application/json">'
        f'<wfs:Query typeNames="{layer}" srsName="{urn}"><fes:Filter><fes:BBOX>'
        f'<gml:Envelope srsName="{urn}"><gml:lowerCorner>{rect.xMinimum()} {rect.yMinimum()}</gml:lowerCorner>'
        f'<gml:upperCorner>{rect.xMaximum()} {rect.yMaximum()}</gml:upperCorner></gml:Envelope>'
        '</fes:BBOX></fes:Filter></wfs:Query></wfs:GetFeature>')


def features_from_gml(data):
    """GML read through OGR, for servers without GeoJSON output."""
    with tempfile.NamedTemporaryFile("wb", suffix=".gml", delete=False) as f:
        f.write(bytes(data))
        path = f.name
    layer = QgsVectorLayer(path, "gml", "ogr")
    if not layer.isValid():
        return QgsFields(), []
    return layer.fields(), list(layer.getFeatures())


def fetch(endpoint, layer, rect, crs, feedback):
    """All features of `layer` in `rect` (in `crs`), page by page. Returns (fields, features)."""
    fields, features, start, use_post, output_format = None, [], 0, False, "application/json"
    while True:
        if feedback.isCanceled():
            break
        if use_post:
            error, status, data, message = post(QUrl(endpoint), getfeature_post(layer, rect, crs, start), feedback)
        else:
            error, status, data, message = get(getfeature_url(endpoint, layer, rect, crs, start, output_format), feedback)
        if status == 403 and not use_post:
            use_post = True
            continue
        text = decode(data) if data else ""
        if error != NO_ERROR:
            found = re.search(r"<(?:\w+:)?ExceptionText>(.*?)</", text, re.S)
            if start and found and "natural order" in found.group(1):
                feedback.reportError(f"{layer}: the server cannot page this layer; kept the first {len(features)} features")
                break
            raise QgsProcessingException(f"{layer}: {found.group(1).strip() if found else message or status}")
        if not text.lstrip().startswith("{"):
            if output_format == "application/json" and start == 0 and not use_post:
                output_format = "application/gml+xml; version=3.2"   # no GeoJSON on this server
                continue
            if "ExceptionReport" in text or "ServiceException" in text:
                found = re.search(r"<(?:\w+:)?ExceptionText>(.*?)</", text, re.S)
                raise QgsProcessingException(f"{layer}: {found.group(1).strip() if found else text[:300]}")
            page_fields, page = features_from_gml(data)
        else:
            page_fields = QgsJsonUtils.stringToFields(text)
            page = QgsJsonUtils.stringToFeatureList(text, page_fields)
        fields = fields or page_fields
        features += page
        if len(page) < PAGE or len(features) >= MAX_FEATURES:
            break
        start += len(page)
    return fields or QgsFields(), features


# -- merging years --------------------------------------------------------------------------------------------

def merged_fields(per_year):
    """The union of all years' fields, in order of first appearance; a field whose type differs between
    years becomes text. `fid` is renamed: GeoPackage keeps that name for its own key."""
    order, types = [], {}
    for fields in per_year:
        for f in fields:
            name = "source_fid" if f.name().lower() == "fid" else f.name()
            if name not in types:
                order.append(name)
                types[name] = f
            elif types[name].type() != f.type():
                types[name] = QgsField(name, STRING)
    out = QgsFields()
    out.append(QgsField("snapshot_year", INTEGER64))
    out.append(QgsField("snapshot_layer", STRING))
    for name in order:
        field = QgsField(types[name])
        field.setName(name)
        out.append(field)
    return out


SERIES_HELP = ("Open the SemanticGIS panel once (it saves the catalogue on this computer), then reopen this tool. "
               "The list holds the registers the catalogue knows as one WFS layer per year.")


class SnapshotSeries(QgsProcessingAlgorithm):
    SERIES, FIRST, LAST, AREA, BUFFER, EXACT, CLIP, CRS, OUTPUT, YEARS = (
        "SERIES", "FIRST_YEAR", "LAST_YEAR", "AREA", "BUFFER", "INTERSECTING_ONLY", "CLIP", "CRS", "OUTPUT", "YEARS")

    def name(self): return "snapshotseries"
    def displayName(self): return "Download snapshot series (WFS, one layer per year)"
    def group(self): return "Data access"
    def groupId(self): return "dataaccess"
    def createInstance(self): return SnapshotSeries()

    def shortHelpString(self):
        return (
            "Fetches a register that is published as one layer per year (Marker, Markblokke, Økologiske arealer …) "
            "for a period of years, within an area, and writes all years into one layer with a <b>snapshot_year</b> "
            "field: \"Marker 2020–2026 within my landscape plus 500 m\".<br><br>"
            "<b>Series</b>: the registers the SemanticGIS catalogue knows as one WFS layer per year. "
            "Years without a layer in the period are skipped and named in the log.<br>"
            "<b>Area</b>: a polygon layer (only its selected features, if you tick that), with a buffer in metres.<br>"
            "<b>Only features that touch the area</b>: otherwise everything in the area's bounding box is kept.<br>"
            "<b>Clip to the area</b>: cut the features at the edge. Leave it off when you need whole fields, for "
            "example because an attribute gives the area of the whole field.<br><br>"
            "The years do not all have the same attributes (Marker 2008 has no crop); fields missing in a year are "
            "left empty, and the log lists them. The attribution line for the data is in the log and in the "
            "ATTRIBUTION output.")

    def initAlgorithm(self, config=None):
        self.series = available_series()
        labels = [s.label for s in self.series] or ["(no series: open the SemanticGIS panel first)"]
        series = QgsProcessingParameterEnum(self.SERIES, "Series", options=labels, defaultValue=0)
        series.setHelp(SERIES_HELP) if hasattr(series, "setHelp") else None
        self.addParameter(series)
        this_year = date.today().year
        self.addParameter(QgsProcessingParameterNumber(self.FIRST, "First year", INTEGER_TYPE, this_year - 5, False, 1950, 2100))
        self.addParameter(QgsProcessingParameterNumber(self.LAST, "Last year", INTEGER_TYPE, this_year, False, 1950, 2100))
        self.addParameter(QgsProcessingParameterFeatureSource(self.AREA, "Area (e.g. your landscape)",
                                                              [QgsProcessing.TypeVectorAnyGeometry]))
        self.addParameter(QgsProcessingParameterNumber(self.BUFFER, "Buffer around the area (m)", DOUBLE_TYPE, 0, False, 0))
        self.addParameter(QgsProcessingParameterBoolean(self.EXACT, "Only features that touch the area (not its whole bounding box)", True))
        self.addParameter(QgsProcessingParameterBoolean(self.CLIP, "Clip features to the area", False))
        self.addParameter(QgsProcessingParameterCrs(self.CRS, "Output CRS", "EPSG:25832"))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Snapshot series", QgsProcessing.TypeVectorAnyGeometry))
        self.addOutput(QgsProcessingOutputString(self.YEARS, "Years fetched"))
        self.addOutput(QgsProcessingOutputString("ATTRIBUTION", "Attribution"))

    def area_geometry(self, parameters, context, crs, feedback):
        source = self.parameterAsSource(parameters, self.AREA, context)
        if source is None:
            raise QgsProcessingException("No area given.")
        geoms = [f.geometry() for f in source.getFeatures() if f.hasGeometry()]
        if not geoms:
            raise QgsProcessingException("The area has no features (or none selected).")
        area = QgsGeometry.unaryUnion(geoms)
        area.transform(QgsCoordinateTransform(source.sourceCrs(), crs, context.transformContext()))
        buffer = self.parameterAsDouble(parameters, self.BUFFER, context)
        if buffer > 0:
            if crs.isGeographic():   # buffer in metres in a projection centred on the area
                centre = area.centroid().asPoint()
                local = local_metric_crs(centre.x(), centre.y())
                area.transform(QgsCoordinateTransform(crs, local, context.transformContext()))
                area = area.buffer(buffer, 16)
                area.transform(QgsCoordinateTransform(local, crs, context.transformContext()))
            else:
                area = area.buffer(buffer, 16)
        return area.makeValid()

    def processAlgorithm(self, parameters, context, feedback):
        if not self.series:
            raise QgsProcessingException("No snapshot series in the catalogue. " + SERIES_HELP)
        series = self.series[self.parameterAsEnum(parameters, self.SERIES, context)]
        first, last = sorted((self.parameterAsInt(parameters, self.FIRST, context), self.parameterAsInt(parameters, self.LAST, context)))
        crs = self.parameterAsCrs(parameters, self.CRS, context)
        if not crs.isValid() or crs.isGeographic():
            raise QgsProcessingException("Choose a projected output CRS, such as EPSG:25832.")
        exact = self.parameterAsBool(parameters, self.EXACT, context)
        clip = self.parameterAsBool(parameters, self.CLIP, context)
        area = self.area_geometry(parameters, context, crs, feedback)
        rect = area.boundingBox()

        snapshots = series.between(first, last)
        missing = [y for y in range(first, last + 1) if y not in series.snapshots]
        feedback.pushInfo(f"{series.title}: {len(snapshots)} of the years {first}–{last} are published"
                          + (f"; no layer for {', '.join(map(str, missing))}" if missing else ""))
        if not snapshots:
            raise QgsProcessingException(f"{series.title} has no layer in {first}–{last}; it covers {series.years[0]}–{series.years[-1]}.")

        fetched = []
        for i, snap in enumerate(snapshots):
            if feedback.isCanceled():
                break
            feedback.setProgress(100 * i / len(snapshots))
            layer = snap.service.layer_name
            fields, features = fetch(snap.service.endpoint, layer, rect, crs, feedback)
            if exact:
                engine = QgsGeometry.createGeometryEngine(area.constGet())
                engine.prepareGeometry()
                features = [f for f in features if f.hasGeometry() and engine.intersects(f.geometry().constGet())]
            if clip:
                for f in features:
                    f.setGeometry(f.geometry().intersection(area))
                features = [f for f in features if f.hasGeometry() and not f.geometry().isEmpty()]
            if len(features) >= MAX_FEATURES:
                feedback.reportError(f"{snap.year}: stopped at {MAX_FEATURES} features; use a smaller area.")
            feedback.pushInfo(f"{snap.year}: {len(features)} features from {layer}")
            fetched.append((snap, fields, features))

        out_fields = merged_fields([f for _, f, _ in fetched])
        names = [f.name() for f in out_fields][2:]
        for snap, fields, features in fetched:
            if features:
                lacking = [n for n in names if n not in {("source_fid" if f.name().lower() == "fid" else f.name()) for f in fields}]
                if lacking:
                    feedback.pushInfo(f"{snap.year} has no {', '.join(lacking)}")

        wkb = QgsWkbTypes.Unknown
        for _, _, features in fetched:
            for f in features:
                if f.hasGeometry():
                    wkb = QgsWkbTypes.multiType(f.geometry().wkbType())
                    break
            if wkb != QgsWkbTypes.Unknown:
                break
        if wkb == QgsWkbTypes.Unknown:
            wkb = QgsWkbTypes.MultiPolygon
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, out_fields, wkb, crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        for snap, fields, features in fetched:
            source_names = [("source_fid" if f.name().lower() == "fid" else f.name()) for f in fields]
            for f in features:
                out = QgsFeature(out_fields)
                values = dict(zip(source_names, f.attributes()))
                out.setAttributes([snap.year, snap.service.layer_name] + [values.get(n) for n in names])
                geometry = f.geometry()
                if geometry and not geometry.isEmpty():
                    geometry.convertToMultiType()
                    out.setGeometry(geometry)
                sink.addFeature(out, QgsFeatureSink.FastInsert)

        attribution = ""
        dataset = snapshots[-1].dataset
        if dataset.attribution and dataset.attribution.text:
            attribution = dataset.attribution.line("download").replace("‹dato›", date.today().isoformat())
            feedback.pushInfo(f"Put this on your map: {attribution}")
        years = ", ".join(str(s.year) for s, _, _ in fetched)
        return {self.OUTPUT: dest, self.YEARS: years, "ATTRIBUTION": attribution}
