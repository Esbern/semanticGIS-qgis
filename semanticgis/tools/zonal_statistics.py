"""Zonal statistics by class with legend labels: statistics of a value raster (NDVI, elevation …) within each
class of a categorical raster (land use), one row per legend label. QGIS's Raster layer zonal statistics
does the same with the class values only, and leaves out the median and quartiles.

Labels come from the categorical raster's paletted style or from a .qml file, as in Tabulate area (legend
labels); values sharing a label are pooled into one row unless asked otherwise. The two rasters need not
share a grid or CRS: the other raster is resampled (nearest neighbour) onto the grid chosen, so every
pixel of that grid counts once. Field names follow Statistics by categories (count, mean, median, q1 …).
"""
import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsFeature, QgsFeatureSink, QgsField, QgsFields, QgsProcessingAlgorithm,
    QgsProcessingException, QgsProcessingParameterBand, QgsProcessingParameterBoolean, QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink, QgsProcessingParameterFile, QgsProcessingParameterRasterLayer, QgsWkbTypes,
)

from .compat import DOUBLE, INTEGER64, STRING
from .tabulate_area import legend_from_layer, legend_from_qml, value_label

GRIDS = ["The value raster's grid (each value counted once)", "The categorical raster's grid"]
STATS = ["sum", "mean", "median", "stddev", "min", "max", "range", "q1", "q3", "iqr"]
STRIP = 1024   # rows read at a time


def single_band(path, band_no):
    """The band as a one-band VRT, with an alpha band added when warped, so pixels outside it can be told apart."""
    ds = gdal.Open(path)
    if ds is None:
        raise QgsProcessingException(f"GDAL cannot open {path}")
    if ds.GetGeoTransform()[2] or ds.GetGeoTransform()[4]:
        raise QgsProcessingException(f"{path} is rotated; warp it to a north-up grid first.")
    return gdal.Translate("", ds, format="VRT", bandList=[band_no])


def onto_grid(ds, ref):
    """ds resampled by nearest neighbour onto ref's grid, as a VRT read on demand; band 2 is 0 where ds has no data."""
    gt = ref.GetGeoTransform()
    bounds = (gt[0], gt[3] + ref.RasterYSize * gt[5], gt[0] + ref.RasterXSize * gt[1], gt[3])
    return gdal.Warp("", ds, format="VRT", outputBounds=bounds, width=ref.RasterXSize, height=ref.RasterYSize,
                     dstSRS=ref.GetProjection(), resampleAlg="near", dstAlpha=True)


def valid(values, nodata):
    keep = np.ones(values.shape, bool)
    if nodata is not None:
        keep &= values != nodata
    if values.dtype.kind == "f":
        keep &= ~np.isnan(values)
    return keep


def collect(classes, values, on_value_grid, feedback):
    """{class value: [value arrays]} over the whole grid, read in strips."""
    ref, other = (values, classes) if on_value_grid else (classes, values)
    warped = onto_grid(other, ref)
    ref_band, other_band, alpha = ref.GetRasterBand(1), warped.GetRasterBand(1), warped.GetRasterBand(2)
    ref_nodata, other_nodata = ref_band.GetNoDataValue(), other.GetRasterBand(1).GetNoDataValue()
    chunks = {}
    for y in range(0, ref.RasterYSize, STRIP):
        if feedback.isCanceled():
            break
        h = min(STRIP, ref.RasterYSize - y)
        a = ref_band.ReadAsArray(0, y, ref.RasterXSize, h)
        b = other_band.ReadAsArray(0, y, ref.RasterXSize, h)
        keep = valid(a, ref_nodata) & valid(b, other_nodata) & (alpha.ReadAsArray(0, y, ref.RasterXSize, h) > 0)
        c, v = (b, a) if on_value_grid else (a, b)
        c, v = c[keep], v[keep].astype(np.float32)
        order = np.argsort(c, kind="stable")
        c, v = c[order], v[order]
        uniques, starts = np.unique(c, return_index=True)
        for value, part in zip(uniques, np.split(v, starts[1:])):
            chunks.setdefault(float(value), []).append(part)
        feedback.setProgress(100 * (y + h) / ref.RasterYSize)
    return chunks, abs(ref.GetGeoTransform()[1] * ref.GetGeoTransform()[5])


def statistics(v):
    v = v.astype(np.float64)
    q1, median, q3 = np.percentile(v, [25, 50, 75])
    low, high = float(v.min()), float(v.max())
    return {"sum": float(v.sum()), "mean": float(v.mean()), "median": float(median), "stddev": float(v.std()),
            "min": low, "max": high, "range": high - low, "q1": float(q1), "q3": float(q3), "iqr": float(q3 - q1)}


def code(value):
    return str(int(value)) if value.is_integer() else str(value)


class ZonalStatisticsLegend(QgsProcessingAlgorithm):
    def name(self): return "zonalstatisticslegend"
    def displayName(self): return "Zonal statistics by class (legend labels)"
    def group(self): return "Raster analysis"
    def groupId(self): return "rasteranalysis"
    def tags(self): return ["zonal", "statistics", "categorical", "class", "land use", "legend", "basemap", "ndvi",
                            "median", "quartile", "raster"]
    def createInstance(self): return ZonalStatisticsLegend()

    def shortHelpString(self):
        return ("Statistics of a value raster (NDVI, elevation, temperature …) within each class of a categorical "
                "raster (land use, for example): one row per legend label, with the class name in the table "
                "instead of only its raster value.\n\n"
                "The labels come from the categorical raster's style (Symbology › Paletted/Unique values), or from a "
                ".qml style file. Values that share a label are pooled into one row, with their raster values listed "
                "in 'class_values'; untick the option to get one row per raster value. Values the legend does not name "
                "get the label 'value <n>' and are listed in the log.\n\n"
                "The rasters need not have the same pixel size or CRS. The other raster is resampled by nearest "
                "neighbour onto the grid you choose. On the value raster's grid (the default) each measured value is "
                "counted once, and each pixel takes the class at its centre. NoData in either raster is left out.\n\n"
                "Fields: count (pixels), area (map units², m² in a UTM CRS), sum, mean, median, stddev (population), "
                "min, max, range, q1, q3 and iqr, named as in Statistics by categories. The tool holds the values of "
                "the whole raster in memory; clip large rasters to the study area first.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer("CLASSES", "Categorical raster (zones)"))
        self.addParameter(QgsProcessingParameterBand("CLASSES_BAND", "Band", 1, "CLASSES"))
        self.addParameter(QgsProcessingParameterFile(
            "QML", "Style file with the legend (.qml); empty = the raster layer's own style", extension="qml", optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer("VALUES", "Value raster"))
        self.addParameter(QgsProcessingParameterBand("VALUES_BAND", "Band", 1, "VALUES"))
        self.addParameter(QgsProcessingParameterBoolean("POOL", "One row per legend label (pool values that share a label)",
                                                        defaultValue=True))
        self.addParameter(QgsProcessingParameterEnum("GRID", "Count on", GRIDS, defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSink("OUTPUT", "Zonal statistics by class"))

    def processAlgorithm(self, parameters, context, feedback):
        classes = self.parameterAsRasterLayer(parameters, "CLASSES", context)
        values = self.parameterAsRasterLayer(parameters, "VALUES", context)
        qml = self.parameterAsFile(parameters, "QML", context)
        legend = legend_from_qml(qml) if qml else legend_from_layer(classes)
        if not legend:
            raise QgsProcessingException(
                "No legend found: style the categorical raster as Paletted/Unique values, or give a .qml style file.")
        feedback.pushInfo(f"{len(legend)} legend entries, {len(set(legend.values()))} labels")
        pool = self.parameterAsBool(parameters, "POOL", context)
        on_value_grid = self.parameterAsEnum(parameters, "GRID", context) == 0

        chunks, pixel_area = collect(
            single_band(classes.source(), self.parameterAsInt(parameters, "CLASSES_BAND", context)),
            single_band(values.source(), self.parameterAsInt(parameters, "VALUES_BAND", context)),
            on_value_grid, feedback)
        if not chunks:
            raise QgsProcessingException("The rasters have no valid pixels in common: check that they overlap.")

        groups = {}   # key: label when pooling, else the raster value
        for value in sorted(chunks):
            label = value_label(value, legend)
            entry = groups.setdefault(label if pool else value, {"label": label, "values": [], "chunks": []})
            entry["values"].append(value)
            entry["chunks"].extend(chunks[value])
        order = {label: i for i, label in enumerate(dict.fromkeys(legend[v] for v in sorted(legend)))}
        rows = sorted(groups.values(), key=lambda g: (order.get(g["label"], len(order)), g["values"][0]))

        fields = QgsFields()
        fields.append(QgsField("class_values" if pool else "class_value", STRING if pool else DOUBLE))
        fields.append(QgsField("label", STRING))
        fields.append(QgsField("count", INTEGER64))
        fields.append(QgsField("area", DOUBLE))
        for name in STATS:
            fields.append(QgsField(name, DOUBLE))
        sink, dest = self.parameterAsSink(parameters, "OUTPUT", context, fields, QgsWkbTypes.NoGeometry,
                                          QgsCoordinateReferenceSystem())
        for row in rows:
            v = np.concatenate(row["chunks"])
            stats = statistics(v)
            out = QgsFeature(fields)
            out.setAttributes([", ".join(code(x) for x in row["values"]) if pool else row["values"][0], row["label"],
                               int(v.size), v.size * pixel_area] + [stats[name] for name in STATS])
            sink.addFeature(out, QgsFeatureSink.FastInsert)
        unlabelled = [row["label"] for row in rows if row["label"].startswith("value ")]
        if unlabelled:
            feedback.reportError(f"Values not in the legend: {', '.join(unlabelled)}", fatalError=False)
        return {"OUTPUT": dest}
