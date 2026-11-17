"""Building heights from elevation models: a robust height, eave height and roof type for every building
footprint, from a surface model (DSM) and a terrain model (DTM).

Footprint registers rarely carry reliable heights; elevation models carry heights but not buildings. For every
building only the pixels inside it (shrunk by an inward buffer) are used, spikes above the roof (chimneys,
antennas, trees) are removed with median + k·MAD, and the height is a high percentile of the rest. Left empty,
the surface and terrain models are Danmarks Højdemodel, fetched for the buildings' area only.
"""
from qgis.core import (
    QgsCoordinateTransform, QgsFeature, QgsFeatureSink, QgsField, QgsFields, QgsGeometry, QgsProcessing,
    QgsProcessingAlgorithm, QgsProcessingException, QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterNumber, QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
)

from .aoi_wkt import DOUBLE_TYPE, INTEGER_TYPE
from .compat import DOUBLE, INTEGER64, STRING, advanced
from . import elevation as el

HELP = (
    "Gives every building a height above ground, an eave height and a roof type, from a surface model (DSM: the "
    "top of everything) and a terrain model (DTM: the bare ground).<br><br>"
    "<b>Surface and terrain model</b>: leave both empty to use <b>Danmarks Højdemodel</b> (0.4 m), fetched only "
    "for the buildings' area; this needs your Datafordeleren key in the SemanticGIS panel's Settings. Or choose "
    "your own rasters (GeoTIFF or WCS layers) with the same CRS.<br><br>"
    "Tip: select the buildings of your study area first and tick <i>Selected features only</i>.<br><br>"
    "<b>How</b>: only pixels inside each building (shrunk by the inward buffer) count; chimneys, antennas and "
    "overhanging trees are removed with a median + k·MAD rule; the height is the chosen percentile of the rest. "
    "The eave is estimated from the low and high percentile, assuming roof heights spread evenly from eave to "
    "ridge. A roof rising less than the flat-roof threshold is <i>flat</i>.<br><br>"
    "<b>Fields</b>: <b>height</b> (top / ridge, for LOD1 extrusion), <b>eave</b> (for LOD2), roof_rise, roof_type, "
    "ground (terrain under the building), roof_abs, h_median, h_max_raw, n_px, <b>h_check</b> (ok, few pixels, "
    "no data, negative).<br><br>"
    "Check buildings with a large roof_rise by eye: a lower wing inside the same footprint gives too low an eave.")

FIELD_TYPES = {"roof_type": STRING, "h_check": STRING, "n_px": INTEGER64}


def height_fields(fields):
    """`fields` plus the height fields it does not have yet."""
    out = QgsFields(fields)
    for name in el.HEIGHT_FIELDS:
        if out.indexOf(name) < 0:
            field = QgsField(name, FIELD_TYPES.get(name, DOUBLE))
            if name not in FIELD_TYPES:
                field.setLength(10)
                field.setPrecision(2)
            out.append(field)
    return out


def with_heights(feature, fields, values):
    out = QgsFeature(fields)
    out.setGeometry(feature.geometry())
    attrs = list(feature.attributes())
    out.setAttributes(attrs + [None] * (fields.count() - len(attrs)))
    for name, value in values.items():
        out[name] = value
    return out


class BuildingHeights(QgsProcessingAlgorithm):
    BUILDINGS, DSM, DTM, RES, BUFFER, PCT, K, EAVE_PCT, FLAT_RISE, MIN_PX, OUTPUT, OUT_DSM, OUT_DTM, OUT_NDSM = (
        "BUILDINGS", "DSM", "DTM", "RES", "BUFFER", "PCT", "K", "EAVE_PCT", "FLAT_RISE", "MIN_PX", "OUTPUT",
        "OUT_DSM", "OUT_DTM", "OUT_NDSM")

    def name(self): return "buildingheights"
    def displayName(self): return "Building heights from elevation models (DSM − DTM)"
    def group(self): return "Raster analysis"
    def groupId(self): return "rasteranalysis"
    def tags(self): return ["building", "height", "dhm", "danmarks højdemodel", "dsm", "dtm", "ndsm", "lod1", "lod2",
                            "eave", "roof", "blender", "3d", "extrude", "urban design", "bygning", "højde"]
    def shortHelpString(self): return HELP
    def createInstance(self): return BuildingHeights()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(self.BUILDINGS, "Buildings (footprints)",
                                                              [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DSM, "Surface model (DSM) — empty: Danmarks Højdemodel, Overflade", optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DTM, "Terrain model (DTM) — empty: Danmarks Højdemodel, Terræn", optional=True))
        options = [
            QgsProcessingParameterNumber(self.RES, "Resolution (m); Danmarks Højdemodel is 0.4", DOUBLE_TYPE, 0.4, False, 0.1),
            QgsProcessingParameterNumber(self.BUFFER, "Inward buffer (m): ignore pixels at the walls", DOUBLE_TYPE, 0.5, False, 0.0),
            QgsProcessingParameterNumber(self.PCT, "Percentile for the height", DOUBLE_TYPE, 95.0, False, 50.0, 100.0),
            QgsProcessingParameterNumber(self.K, "Outlier filter k (median + k·MAD); 0 = off", DOUBLE_TYPE, 3.0, False, 0.0),
            QgsProcessingParameterNumber(self.EAVE_PCT, "Low percentile for the eave estimate", DOUBLE_TYPE, 10.0, False, 0.0, 49.0),
            QgsProcessingParameterNumber(self.FLAT_RISE, "Flat roof if ridge − eave is below (m)", DOUBLE_TYPE, 1.0, False, 0.0),
            QgsProcessingParameterNumber(self.MIN_PX, "Minimum pixels for a reliable height", INTEGER_TYPE, 10, False, 1),
        ]
        for p in options:
            self.addParameter(advanced(p))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Buildings with heights",
                                                            QgsProcessing.TypeVectorPolygon))
        for name, label in ((self.OUT_DTM, "Terrain (DTM) of the area, e.g. for Blender"),
                            (self.OUT_DSM, "Surface (DSM) of the area"),
                            (self.OUT_NDSM, "Height above ground (nDSM), to check the result")):
            self.addParameter(QgsProcessingParameterRasterDestination(name, label + " — optional GeoTIFF",
                                                                      optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.BUILDINGS, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.BUILDINGS))
        dsm = self.parameterAsRasterLayer(parameters, self.DSM, context)
        dtm = self.parameterAsRasterLayer(parameters, self.DTM, context)
        if (dsm is None) != (dtm is None):
            raise QgsProcessingException("Choose both a surface and a terrain model, or leave both empty to use "
                                         "Danmarks Højdemodel.")
        if dsm is None:
            feedback.pushInfo("Using Danmarks Højdemodel (Datafordeleren WCS)")
            dsm, dtm = el.dhm_layer(el.DHM_SURFACE), el.dhm_layer(el.DHM_TERRAIN)
        if dsm.crs() != dtm.crs():
            raise QgsProcessingException("The surface and terrain models must have the same CRS.")
        crs = dsm.crs()
        options = dict(res=self.parameterAsDouble(parameters, self.RES, context),
                       buffer=self.parameterAsDouble(parameters, self.BUFFER, context),
                       pct=self.parameterAsDouble(parameters, self.PCT, context),
                       k=self.parameterAsDouble(parameters, self.K, context),
                       eave_pct=self.parameterAsDouble(parameters, self.EAVE_PCT, context),
                       flat_rise=self.parameterAsDouble(parameters, self.FLAT_RISE, context),
                       min_px=self.parameterAsInt(parameters, self.MIN_PX, context))

        xform = QgsCoordinateTransform(source.sourceCrs(), crs, context.transformContext())
        features = list(source.getFeatures())
        if not features:
            raise QgsProcessingException("No buildings (or none selected).")
        geoms = []
        for f in features:
            g = QgsGeometry(f.geometry())
            if not g.isNull():
                g.transform(xform)
            geoms.append(g)

        values, grids = el.building_heights(geoms, dsm, dtm, crs, feedback=feedback, **options)
        if values is None:
            return {}

        fields = height_fields(source.fields())
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, source.wkbType(), source.sourceCrs())
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        for f, v in zip(features, values):
            sink.addFeature(with_heights(f, fields, v), QgsFeatureSink.FastInsert)
        feedback.setProgress(90)

        results = {self.OUTPUT: dest}
        extent, a_dsm, a_dtm = grids
        for name, arr in ((self.OUT_DSM, a_dsm), (self.OUT_DTM, a_dtm), (self.OUT_NDSM, a_dsm - a_dtm)):
            path = self.parameterAsOutputLayer(parameters, name, context)
            if path:
                el.write_tif(path, arr, extent, options["res"], crs)
                results[name] = path
        feedback.pushInfo("Done: " + el.summary(values))
        return results
