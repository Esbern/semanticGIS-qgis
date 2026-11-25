"""Line density: how many lines, or how many different things, cross each cell of a grid.

Made for AIS vessel lines, so that a class can compare its own map with HELCOM's shipping density; works on any lines
(GPS tracks, animal paths, routes). The grid is aligned to the coordinate system's origin, so maps made on different
days, and HELCOM's 1 km grid in EPSG:3035, have the same cells.
"""
import math

import numpy as np
from osgeo import gdal, osr
from qgis.core import (
    QgsCoordinateTransform, QgsFeatureRequest, QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterCrs, QgsProcessingParameterEnum, QgsProcessingParameterExtent, QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField, QgsProcessingParameterNumber, QgsProcessingParameterRasterDestination,
)

from .flatten_snapshots import FIELD_ANY

NODATA = -9999


def cells_of(points, x0, y1, size, columns, rows):
    """The cells (row * columns + column) that a line passes through, each once. points: [(x, y)] in the grid's system."""
    found = set()

    def add(c, r):
        if 0 <= c < columns and 0 <= r < rows:
            found.add(r * columns + c)
    if len(points) == 1:
        add(math.floor((points[0][0] - x0) / size), math.floor((y1 - points[0][1]) / size))
    for (xa, ya), (xb, yb) in zip(points, points[1:]):
        ua, va, ub, vb = (xa - x0) / size, (y1 - ya) / size, (xb - x0) / size, (y1 - yb) / size
        c, r, ce, re = math.floor(ua), math.floor(va), math.floor(ub), math.floor(vb)
        add(c, r)
        if (c, r) == (ce, re):
            continue
        du, dv = ub - ua, vb - va
        sc, sr = (1 if du > 0 else -1), (1 if dv > 0 else -1)
        tu = ((c + (sc > 0)) - ua) / du if du else math.inf      # where along the step the next column, the next row begins
        tv = ((r + (sr > 0)) - va) / dv if dv else math.inf
        su, sv = (abs(1 / du) if du else math.inf), (abs(1 / dv) if dv else math.inf)
        for _ in range(abs(ce - c) + abs(re - r)):
            if tu < tv:
                tu += su
                c += sc
            else:
                tv += sv
                r += sr
            add(c, r)
    return found


class LineDensity(QgsProcessingAlgorithm):
    INPUT, COUNT, ID, TIME, CRS, SIZE, EXTENT, ZERO, OUTPUT = "INPUT", "COUNT", "ID_FIELD", "TIME_FIELD", "CRS", "CELL_SIZE", "EXTENT", "EMPTY_CELLS", "OUTPUT"
    COUNTS = ["Different vessels (or other id)", "Different vessels per day, added up", "Lines"]
    EMPTY = ["0", "No data"]

    def name(self): return "linedensity"
    def displayName(self): return "Line density (how many cross each cell)"
    def group(self): return "Raster analysis"
    def groupId(self): return "rasteranalysis"
    def createInstance(self): return LineDensity()

    def shortHelpString(self):
        return (
            "Counts for each cell of a grid how many vessels, or lines, cross it. Each is counted <b>once per cell</b>, however long it stays.<br><br>"
            "<b>Different vessels</b>: the same vessel counts once, however many lines it has. <b>Per day, added up</b>: once for each day (needs a time field). "
            "<b>Lines</b>: every line counts; a vessel that fell silent and came back has several.<br><br>"
            "The grid is aligned to the coordinate system, so maps of different days have the same cells. With EPSG:3035 and 1000 m the cells are those of "
            "<b>HELCOM's shipping density</b> (except HELCOM's 2021 and 2022).<br><br>"
            "<b>Before you compare with HELCOM</b>: HELCOM counts <i>trips in a year</i> of <i>IMO-registered ships</i> only. Filter on imo (not empty) first, and remember that "
            "a day is not a year and a line is not a trip.<br><br>"
            "<b>Moored vessels</b> are counted too: give a filter such as <i>length_m &gt; 2000</i> in the input's options if you want only movement. "
            "The number depends on the cell size: counts in cells of different sizes cannot be compared.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(self.INPUT, "Lines", [QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterEnum(self.COUNT, "Count", self.COUNTS, False, 0))
        self.addParameter(QgsProcessingParameterField(self.ID, "Field with the vessel (or other id)", "mmsi", self.INPUT, FIELD_ANY, optional=True))
        self.addParameter(QgsProcessingParameterField(self.TIME, "Field with the time (for 'per day')", "start_time", self.INPUT, FIELD_ANY, optional=True))
        self.addParameter(QgsProcessingParameterCrs(self.CRS, "Coordinate system of the grid (in metres)", "EPSG:3035"))
        self.addParameter(QgsProcessingParameterNumber(self.SIZE, "Cell size (metres)", QgsProcessingParameterNumber.Type.Double, 1000, False, 1))
        self.addParameter(QgsProcessingParameterExtent(self.EXTENT, "Area (empty = all lines)", None, True))
        self.addParameter(QgsProcessingParameterEnum(self.ZERO, "Cells nothing crosses", self.EMPTY, False, 1))
        self.addParameter(QgsProcessingParameterRasterDestination(self.OUTPUT, "Line density"))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))
        crs = self.parameterAsCrs(parameters, self.CRS, context)
        size = self.parameterAsDouble(parameters, self.SIZE, context)
        count = self.parameterAsEnum(parameters, self.COUNT, context)
        id_field, time_field = self.parameterAsString(parameters, self.ID, context), self.parameterAsString(parameters, self.TIME, context)
        id_at, time_at = source.fields().indexOf(id_field) if id_field else -1, source.fields().indexOf(time_field) if time_field else -1
        if crs.isGeographic():
            raise QgsProcessingException("Choose a coordinate system in metres, such as EPSG:3035 or EPSG:25832.")
        if count < 2 and id_at < 0:
            raise QgsProcessingException("Choose the field that says which vessel it is, or count lines.")
        if count == 1 and time_at < 0:
            raise QgsProcessingException("Choose the time field, or count different vessels.")
        transform = QgsCoordinateTransform(source.sourceCrs(), crs, context.transformContext())
        area = self.parameterAsExtent(parameters, self.EXTENT, context, crs) if parameters.get(self.EXTENT) else None
        if area is None or area.isNull() or area.isEmpty():
            if source.featureCount() == 0:
                raise QgsProcessingException("There are no lines.")
            area = transform.transformBoundingBox(source.sourceExtent())
        x0, y0 = math.floor(area.xMinimum() / size) * size, math.floor(area.yMinimum() / size) * size
        x1, y1 = math.ceil(area.xMaximum() / size) * size, math.ceil(area.yMaximum() / size) * size
        columns, rows = max(1, round((x1 - x0) / size)), max(1, round((y1 - y0) / size))
        if columns * rows > 100_000_000:
            raise QgsProcessingException(f"The grid would have {columns} x {rows} cells: choose larger cells or a smaller area.")
        feedback.pushInfo(f"Grid: {columns} x {rows} cells of {size:g} m in {crs.authid()}")
        counts = np.zeros(columns * rows, dtype=np.int32)
        groups, lines, total = {}, 0, source.featureCount() or 1
        request = QgsFeatureRequest().setSubsetOfAttributes([i for i in (id_at, time_at) if i >= 0])
        for n, f in enumerate(source.getFeatures(request)):
            if feedback.isCanceled():
                return {}
            g = f.geometry()
            if g.isNull() or g.isEmpty():
                continue
            # corners first, then project: an AIS step is short, so the line between two projected positions is close enough
            g.transform(transform)
            found = set()
            for part in (g.asMultiPolyline() if g.isMultipart() else [g.asPolyline()]):
                found |= cells_of([(p.x(), p.y()) for p in part], x0, y1, size, columns, rows)
            lines += 1
            if found:
                if count == 2:
                    counts[np.fromiter(found, dtype=np.int64)] += 1
                else:
                    key = f.attribute(id_at)
                    if count == 1:
                        t = f.attribute(time_at)
                        key = (key, t.date().toString("yyyy-MM-dd") if hasattr(t, "date") else str(t)[:10])
                    groups.setdefault(key, set()).update(found)
            feedback.setProgress(95 * n / total)
        for found in groups.values():
            counts[np.fromiter(found, dtype=np.int64)] += 1
        feedback.pushInfo(f"{lines:,} lines" + (f", {len(groups):,} {'vessels' if count == 0 else 'vessel days'}" if count < 2 else "") +
                          f"; {int((counts > 0).sum()):,} cells crossed, most in one cell: {int(counts.max())}")
        out = counts.reshape(rows, columns)
        if self.parameterAsEnum(parameters, self.ZERO, context) == 1:
            out = np.where(out == 0, NODATA, out)
        path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        driver = gdal.GetDriverByName("GTiff")
        ds = driver.Create(path, columns, rows, 1, gdal.GDT_Int32, ["COMPRESS=DEFLATE", "TILED=YES"])
        if ds is None:
            raise QgsProcessingException(f"Could not write {path}; give a file name ending in .tif.")
        ds.SetGeoTransform((x0, size, 0, y1, 0, -size))
        srs = osr.SpatialReference()
        srs.ImportFromWkt(crs.toWkt())
        ds.SetProjection(srs.ExportToWkt())
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(NODATA)
        band.SetDescription(("vessels", "vessel days", "lines")[count])
        band.WriteArray(out.astype(np.int32))
        ds = None
        return {self.OUTPUT: path}
