"""Tabulate area with legend labels: the pixels of each class of a categorical raster inside each polygon,
one column per legend label (like ArcGIS's Tabulate Area, or QGIS's Zonal histogram with readable names).

Labels come from the raster layer's paletted style or from a .qml file. Values sharing a label are added
into one column (Basemap04's "Lake" is 1, 63 and 144); values the legend does not name get "value <n>".
A pixel counts when its centre lies inside the polygon. Counting is done here with GDAL rather than by
Zonal histogram, whose column names keep only six significant digits (50996500 and 50996504 collide).
"""
import math
import re

import numpy as np
from osgeo import gdal, ogr, osr
from qgis.core import (
    QgsCoordinateTransform, QgsFeature, QgsFeatureSink, QgsField, QgsFields, QgsPalettedRasterRenderer,
    QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException, QgsProcessingParameterBand,
    QgsProcessingParameterBoolean, QgsProcessingParameterEnum, QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterFile, QgsProcessingParameterRasterLayer,
)

from .compat import DOUBLE as AREA_TYPE, INTEGER64 as COUNT_TYPE

UNITS = ["Pixel count", "Area (map units²)", "Percent of the zone's classified pixels"]
STRIP = 2048   # raster rows read at a time, so a zone as large as Denmark still fits in memory


def legend_from_qml(path):
    """{value: label} from the paletteEntry lines of a QGIS style file."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    legend = {}
    for entry in re.findall(r"<paletteEntry\b[^>]*>", text):
        value, label = re.search(r'\bvalue="([^"]*)"', entry), re.search(r'\blabel="([^"]*)"', entry)
        if value and label:
            legend[float(value.group(1))] = label.group(1)
    return legend


def legend_from_layer(layer):
    renderer = layer.renderer()
    if isinstance(renderer, QgsPalettedRasterRenderer):
        return {float(c.value): (c.label or str(c.value)) for c in renderer.classes()}
    return {}


def value_label(value, legend):
    if value in legend:
        return legend[value]
    return f"value {int(value)}" if value.is_integer() else f"value {value}"


def count_zones(raster_path, band_no, zones, raster_crs, legend, context, feedback):
    """[(zone feature, {label: pixels})] and the total pixels per label over all zones."""
    ds = gdal.Open(raster_path)
    if ds is None:
        raise QgsProcessingException(f"GDAL cannot open {raster_path}")
    band = ds.GetRasterBand(band_no)
    nodata = band.GetNoDataValue()
    gt = ds.GetGeoTransform()
    if gt[2] or gt[4]:
        raise QgsProcessingException("The raster is rotated; warp it to a north-up grid first.")
    srs = osr.SpatialReference()
    srs.ImportFromWkt(ds.GetProjection())
    to_raster = QgsCoordinateTransform(zones.sourceCrs(), raster_crs, context.transformContext())
    rows, totals = [], {}
    n = max(zones.featureCount(), 1)
    for i, feature in enumerate(zones.getFeatures()):
        if feedback.isCanceled():
            break
        counts = {}
        geometry = feature.geometry()
        if not geometry.isEmpty():
            geometry.transform(to_raster)
            box = geometry.boundingBox()
            x0 = max(0, math.floor((box.xMinimum() - gt[0]) / gt[1]))
            x1 = min(ds.RasterXSize, math.ceil((box.xMaximum() - gt[0]) / gt[1]))
            y0 = max(0, math.floor((box.yMaximum() - gt[3]) / gt[5]))
            y1 = min(ds.RasterYSize, math.ceil((box.yMinimum() - gt[3]) / gt[5]))
            source = ogr.GetDriverByName("Memory").CreateDataSource("")
            layer = source.CreateLayer("zone", srs)
            zone = ogr.Feature(layer.GetLayerDefn())
            zone.SetGeometry(ogr.CreateGeometryFromWkb(bytes(geometry.asWkb())))
            layer.CreateFeature(zone)
            for ys in range(y0, y1, STRIP):
                w, h = x1 - x0, min(STRIP, y1 - ys)
                if w <= 0 or h <= 0:
                    continue
                mask = gdal.GetDriverByName("MEM").Create("", w, h, 1, gdal.GDT_Byte)
                mask.SetGeoTransform((gt[0] + x0 * gt[1], gt[1], 0, gt[3] + ys * gt[5], 0, gt[5]))
                mask.SetProjection(ds.GetProjection())
                gdal.RasterizeLayer(mask, [1], layer, burn_values=[1])
                values = band.ReadAsArray(x0, ys, w, h)[mask.ReadAsArray() == 1]
                if nodata is not None:
                    values = values[values != nodata]
                for value, c in zip(*np.unique(values, return_counts=True)):
                    label = value_label(float(value), legend)
                    counts[label] = counts.get(label, 0) + int(c)
        for label, c in counts.items():
            totals[label] = totals.get(label, 0) + c
        rows.append((feature, counts))
        feedback.setProgress(100 * (i + 1) / n)
    return rows, totals


class TabulateAreaLegend(QgsProcessingAlgorithm):
    def name(self): return "tabulatearealegend"
    def displayName(self): return "Tabulate area (legend labels)"
    def group(self): return "Raster analysis"
    def groupId(self): return "rasteranalysis"
    def tags(self): return ["zonal", "histogram", "tabulate", "area", "categorical", "land use", "legend", "basemap"]
    def createInstance(self): return TabulateAreaLegend()

    def shortHelpString(self):
        return ("Counts the pixels of each class of a categorical raster (land use, for example) inside each polygon, "
                "with one column per legend label instead of per raster value, like ArcGIS's Tabulate Area.\n\n"
                "The labels come from the raster layer's style (Symbology › Paletted/Unique values), or from a .qml "
                "style file. Values that share a label are added into one column; values the legend does not name "
                "get a column 'value <n>' and are listed in the log. A pixel counts when its centre lies inside the "
                "polygon. Results can be pixel counts, area in map units² (m² in EPSG:25832) or the percentage of "
                "each polygon's classified pixels.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer("RASTER", "Categorical raster"))
        self.addParameter(QgsProcessingParameterBand("BAND", "Band", 1, "RASTER"))
        self.addParameter(QgsProcessingParameterFeatureSource("ZONES", "Polygons (zones)", [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterFile(
            "QML", "Style file with the legend (.qml); empty = the raster layer's own style", extension="qml", optional=True))
        self.addParameter(QgsProcessingParameterEnum("UNIT", "Report as", UNITS, defaultValue=1))
        self.addParameter(QgsProcessingParameterBoolean("DROP_EMPTY", "Leave out classes found in no polygon", defaultValue=True))
        self.addParameter(QgsProcessingParameterFeatureSink("OUTPUT", "Tabulated area"))

    def processAlgorithm(self, parameters, context, feedback):
        raster = self.parameterAsRasterLayer(parameters, "RASTER", context)
        qml = self.parameterAsFile(parameters, "QML", context)
        legend = legend_from_qml(qml) if qml else legend_from_layer(raster)
        if not legend:
            raise QgsProcessingException(
                "No legend found: style the raster as Paletted/Unique values, or give a .qml style file.")
        feedback.pushInfo(f"{len(legend)} legend entries, {len(set(legend.values()))} labels")
        unit = self.parameterAsEnum(parameters, "UNIT", context)
        pixel_area = abs(raster.rasterUnitsPerPixelX() * raster.rasterUnitsPerPixelY())
        zones = self.parameterAsSource(parameters, "ZONES", context)
        zone_fields = zones.fields().names()

        rows, totals = count_zones(raster.source(), self.parameterAsInt(parameters, "BAND", context), zones,
                                   raster.crs(), legend, context, feedback)
        order = {label: i for i, label in enumerate(dict.fromkeys(legend[v] for v in sorted(legend)))}
        if self.parameterAsBool(parameters, "DROP_EMPTY", context):
            labels = [label for label in totals if totals[label]]
        else:
            labels = list(dict.fromkeys(list(totals) + list(order)))
        labels.sort(key=lambda label: (order.get(label, len(order)), label))

        fields = QgsFields()
        for field in zones.fields():
            fields.append(field)
        taken = {name.lower() for name in zone_fields}
        for label in labels:
            name, i = label, 2
            while name.lower() in taken:   # a label that repeats a zone field name
                name, i = f"{label} ({i})", i + 1
            taken.add(name.lower())
            fields.append(QgsField(name, COUNT_TYPE if unit == 0 else AREA_TYPE))

        sink, dest = self.parameterAsSink(parameters, "OUTPUT", context, fields, zones.wkbType(), zones.sourceCrs())
        for feature, counts in rows:
            classified = sum(counts.values())
            out = QgsFeature(fields)
            out.setGeometry(feature.geometry())
            values = [feature[name] for name in zone_fields]
            for label in labels:
                c = counts.get(label, 0)
                values.append(c if unit == 0 else c * pixel_area if unit == 1 else (100 * c / classified if classified else None))
            out.setAttributes(values)
            sink.addFeature(out, QgsFeatureSink.FastInsert)
        unlabelled = [label for label in labels if label.startswith("value ")]
        if unlabelled:
            feedback.reportError(f"Values not in the legend: {', '.join(unlabelled)}", fatalError=False)
        return {"OUTPUT": dest}
