"""Clip project layers to one GeoPackage: every (visible) vector layer clipped to a study area and saved, with its
style, as a table in one GeoPackage; raster layers optionally as GeoTIFFs beside it.

A study area for a project is usually a few hundred metres across, while the layers come from national
services. Clipping each one by hand (and restyling the result) is the slow, error-prone part for beginners;
this does all of them at once and adds them to the project in a group, so the originals can be switched off.
"""
import os
import re

from qgis.core import (
    QgsCoordinateTransform, QgsMapLayer, QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterBoolean, QgsProcessingParameterFileDestination, QgsProcessingParameterVectorLayer,
    QgsProcessingUtils, QgsProject, QgsRasterFileWriter, QgsRasterLayer, QgsRasterPipe, QgsReadWriteContext,
    QgsVectorFileWriter, QgsVectorLayer,
)
from qgis.PyQt.QtXml import QDomDocument

from .compat import save_default_style, set_creation_options

PICTURES = {"wms", "xyz", "arcgismapserver", "vectortile", "mbtilesvectortiles"}   # map images, not data


def clean_name(name, used):
    """A valid, unique GeoPackage table (or file) name: ASCII, lower case, no leading digit."""
    for a, b in (("æ", "ae"), ("ø", "oe"), ("å", "aa"), ("Æ", "Ae"), ("Ø", "Oe"), ("Å", "Aa")):
        name = name.replace(a, b)
    base = re.sub(r"[^0-9A-Za-z_]+", "_", name).strip("_").lower() or "layer"
    if base[0].isdigit():
        base = "l_" + base
    candidate, i = base, 2
    while candidate in used:
        candidate, i = f"{base}_{i}", i + 1
    used.add(candidate)
    return candidate


def project_layers(project, only_visible):
    """Layers in layer-tree order (top to bottom)."""
    for node in project.layerTreeRoot().findLayers():
        layer = node.layer()
        if layer is not None and layer.isValid() and (node.isVisible() or not only_visible):
            yield layer


def write_vector(layer, gpkg, table, first, transform_context):
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName, options.layerName, options.fileEncoding = "GPKG", table, "UTF-8"
    options.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteFile if first
                                    else QgsVectorFileWriter.CreateOrOverwriteLayer)
    result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, gpkg, transform_context, options)
    return result[0] == QgsVectorFileWriter.NoError, result[1]


def copy_style(source, gpkg, table):
    out = QgsVectorLayer(f"{gpkg}|layername={table}", table, "ogr")
    doc = QDomDocument()
    source.exportNamedStyle(doc, QgsReadWriteContext())
    out.importNamedStyle(doc)
    save_default_style(out, table, "Copied from the source layer")


def clip_raster(layer, rect, path, transform_context):
    """The layer's data within `rect` (in the layer's CRS) at its own resolution, read through its provider, so
    WCS layers work as well as files. Returns an error message or None."""
    provider = layer.dataProvider()
    rect = rect.intersect(layer.extent())
    if rect.isEmpty():
        return "no data inside the area"
    if layer.width() > 0 and layer.height() > 0:
        res_x = layer.extent().width() / layer.width()
        res_y = layer.extent().height() / layer.height()
    else:
        return "unknown resolution"
    cols, rows = max(1, round(rect.width() / res_x)), max(1, round(rect.height() / res_y))
    if cols * rows > 100_000_000:
        return f"too large ({cols} x {rows} pixels)"
    pipe = QgsRasterPipe()
    if not pipe.set(provider.clone()):
        return "cannot read the layer"
    writer = QgsRasterFileWriter(path)
    set_creation_options(writer, ["COMPRESS=DEFLATE", "TILED=YES"])
    error = writer.writeRaster(pipe, cols, rows, rect, layer.crs(), transform_context)
    return None if error == QgsRasterFileWriter.NoError else f"write error {error}"


def write_raster(layer, rect, crs, res, path, transform_context, options=("COMPRESS=DEFLATE", "TILED=YES")):
    """Any QGIS raster layer (WMS, XYZ, WCS, file) as a GeoTIFF of `rect` in `crs` at `res` metres, reprojected when
    the layer has another CRS. Web map layers give an RGBA image. Returns an error message or None."""
    from qgis.core import QgsRasterProjector

    cols, rows = max(1, round(rect.width() / res)), max(1, round(rect.height() / res))
    pipe = QgsRasterPipe()
    if not pipe.set(layer.dataProvider().clone()):
        return "cannot read the layer"
    if layer.crs() != crs:
        projector = QgsRasterProjector()
        projector.setCrs(layer.crs(), crs, transform_context)
        pipe.insert(2, projector)
    writer = QgsRasterFileWriter(path)
    set_creation_options(writer, list(options))
    error = writer.writeRaster(pipe, cols, rows, rect, crs, transform_context)
    return None if error == QgsRasterFileWriter.NoError else f"write error {error}"


class ClipToGeoPackage(QgsProcessingAlgorithm):
    AREA, ONLY_VISIBLE, SKIP_EMPTY, SAVE_STYLES, INCLUDE_RASTERS, LOAD_RESULT, OUTPUT = (
        "AREA", "ONLY_VISIBLE", "SKIP_EMPTY", "SAVE_STYLES", "INCLUDE_RASTERS", "LOAD_RESULT", "OUTPUT")

    def name(self): return "cliptogeopackage"
    def displayName(self): return "Clip project layers to one GeoPackage"
    def group(self): return "Vector general"
    def groupId(self): return "vectorgeneral"
    def tags(self): return ["clip", "study area", "project area", "geopackage", "gpkg", "export", "package", "styles",
                            "visible layers", "urban design"]
    def createInstance(self): return ClipToGeoPackage()

    def shortHelpString(self):
        return ("Clips every visible vector layer in the project to a study area and saves them, with their "
                "styles, as layers in <b>one GeoPackage</b>. The study area itself is saved too.<br><br>"
                "Turn on the layers you need first, then run the tool. The clipped layers are added to the project "
                "in a new group: switch the originals off and work with the clipped copies.<br><br>"
                "<b>Rasters</b> (optional): height models and other data rasters, also WCS layers, are cut to the "
                "study area's rectangle and saved as GeoTIFFs in a folder next to the GeoPackage "
                "(&lt;name&gt;_rasters). Map pictures (WMS, WMTS, XYZ basemaps) are not data and are skipped.<br><br>"
                "Layers keep their own CRS; layer names become valid table names (æ → ae, spaces → _).")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(self.AREA, "Study area (polygon layer)",
                                                            [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterBoolean(self.ONLY_VISIBLE, "Only visible layers", True))
        self.addParameter(QgsProcessingParameterBoolean(self.SKIP_EMPTY, "Skip layers with nothing inside the area", True))
        self.addParameter(QgsProcessingParameterBoolean(self.SAVE_STYLES, "Keep the layer styles", True))
        self.addParameter(QgsProcessingParameterBoolean(self.INCLUDE_RASTERS, "Also clip raster layers (to GeoTIFF)", False))
        self.addParameter(QgsProcessingParameterBoolean(self.LOAD_RESULT, "Add the clipped layers to the project", True))
        self.addParameter(QgsProcessingParameterFileDestination(self.OUTPUT, "GeoPackage", "GeoPackage (*.gpkg)"))

    def processAlgorithm(self, parameters, context, feedback):
        import processing

        area = self.parameterAsVectorLayer(parameters, self.AREA, context)
        if area is None or area.featureCount() == 0:
            raise QgsProcessingException("The study area layer is empty: draw a polygon in it first.")
        only_visible = self.parameterAsBool(parameters, self.ONLY_VISIBLE, context)
        skip_empty = self.parameterAsBool(parameters, self.SKIP_EMPTY, context)
        save_styles = self.parameterAsBool(parameters, self.SAVE_STYLES, context)
        include_rasters = self.parameterAsBool(parameters, self.INCLUDE_RASTERS, context)
        self.load_result = self.parameterAsBool(parameters, self.LOAD_RESULT, context)
        gpkg = self.parameterAsFileOutput(parameters, self.OUTPUT, context)
        if not gpkg.lower().endswith(".gpkg"):
            gpkg += ".gpkg"

        project = context.project() or QgsProject.instance()
        layers = list(project_layers(project, only_visible))
        vectors = [l for l in layers if isinstance(l, QgsVectorLayer) and l.isSpatial() and l.id() != area.id()]
        vectors.append(area)   # the study area itself, at the bottom, even if hidden
        rasters = [l for l in layers if isinstance(l, QgsRasterLayer)] if include_rasters else []

        if os.path.exists(gpkg):
            os.remove(gpkg)
        raster_dir = os.path.splitext(gpkg)[0] + "_rasters"
        used, self.written, self.written_rasters = set(), [], []
        total, first = len(vectors) + len(rasters), True

        for i, layer in enumerate(vectors):
            if feedback.isCanceled():
                break
            feedback.setProgress(100 * i / max(total, 1))
            feedback.pushInfo(f"Clipping {layer.name()} …")
            if layer.id() == area.id():
                clipped = area
            else:
                try:
                    result = processing.run("native:clip", {"INPUT": layer, "OVERLAY": area,
                                                            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT},
                                            context=context, feedback=None, is_child_algorithm=True)
                    clipped = QgsProcessingUtils.mapLayerFromString(result["OUTPUT"], context)
                except Exception as e:
                    feedback.reportError(f"  skipped {layer.name()}: {e}")
                    continue
            if clipped is None:
                feedback.reportError(f"  skipped {layer.name()}: could not be clipped")
                continue
            if skip_empty and clipped.featureCount() == 0 and layer.id() != area.id():
                feedback.pushInfo("  nothing inside the area, skipped")
                continue
            table = clean_name(layer.name(), used)
            ok, message = write_vector(clipped, gpkg, table, first, context.transformContext())
            if not ok:
                feedback.reportError(f"  could not write {table}: {message}")
                continue
            first = False
            if save_styles:
                copy_style(layer, gpkg, table)
            self.written.append((table, layer.name()))
            feedback.pushInfo(f"  → {table} ({clipped.featureCount()} features)")

        area_rect = area.extent()
        for j, layer in enumerate(rasters):
            if feedback.isCanceled():
                break
            feedback.setProgress(100 * (len(vectors) + j) / max(total, 1))
            if layer.providerType().lower() in PICTURES:
                feedback.pushInfo(f"{layer.name()}: a map picture (web service), not data; skipped")
                continue
            os.makedirs(raster_dir, exist_ok=True)
            path = os.path.join(raster_dir, clean_name(layer.name(), used) + ".tif")
            feedback.pushInfo(f"Clipping raster {layer.name()} …")
            rect = QgsCoordinateTransform(area.crs(), layer.crs(), context.transformContext()).transformBoundingBox(area_rect)
            error = clip_raster(layer, rect, path, context.transformContext())
            if error:
                feedback.reportError(f"  skipped {layer.name()}: {error}")
            else:
                self.written_rasters.append((path, layer.name()))
                feedback.pushInfo(f"  → {os.path.basename(path)}")

        if not self.written:
            raise QgsProcessingException("No layers were written.")
        self.gpkg = gpkg
        feedback.pushInfo(f"Done: {len(self.written)} layer(s) in {gpkg}"
                          + (f", {len(self.written_rasters)} raster(s) in {raster_dir}" if self.written_rasters else ""))
        return {self.OUTPUT: gpkg}

    def postProcessAlgorithm(self, context, feedback):
        """Adding layers to the project happens on the main thread."""
        gpkg = getattr(self, "gpkg", None)
        if gpkg and getattr(self, "load_result", False):
            add_group(context.project() or QgsProject.instance(), "Clipped – " + os.path.basename(gpkg),
                      [QgsVectorLayer(f"{gpkg}|layername={t}", name, "ogr") for t, name in self.written]
                      + [QgsRasterLayer(p, name) for p, name in self.written_rasters])
        return {self.OUTPUT: gpkg}


def add_group(project, title, layers):
    """Add the valid layers to a new group at the top of the layer tree, in the given order."""
    group = project.layerTreeRoot().insertGroup(0, title)
    for layer in layers:
        if layer.isValid():
            project.addMapLayer(layer, False)
            group.addLayer(layer)
    return group
