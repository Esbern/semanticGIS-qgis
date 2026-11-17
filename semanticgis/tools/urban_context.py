"""Urban context for a study area (Denmark): the base data of an urban design project in one step.

For a study area (a polygon, or a rectangle drawn on the map) it fetches the chosen GeoDanmark layers
(buildings, kerbs, road centre lines, trees …) from Datafordeleren, gives every building a height, eave height
and roof type from Danmarks Højdemodel, and saves it all in one GeoPackage, with the terrain and the height above
ground as GeoTIFFs beside it (terrain for Blender). The result is added to the project, styled.

This replaces the download, clip and height steps of the Building heights from DHM recipe, which beginners found
the hardest part: finding the services, keys, clipping each layer and installing scripts.
"""
import os
from datetime import date

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsField, QgsFields, QgsGeometry,
    QgsGraduatedSymbolRenderer, QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingOutputString, QgsProcessingParameterBoolean, QgsProcessingParameterEnum,
    QgsProcessingParameterExtent, QgsProcessingParameterFeatureSource, QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
    QgsProject, QgsRasterLayer, QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes,
)

from . import elevation as el
from .aoi_wkt import DOUBLE_TYPE
from .building_heights import height_fields, with_heights
from .clip_to_geopackage import add_group, write_raster
from .compat import STRING, save_default_style
from .snapshot_series import fetch

GEODANMARK_WFS = "https://wfs.datafordeler.dk/GeoDanmarkVektor/GeoDanmark60_NOHIST_GML3/1.0.0/WFS"
GEODANMARK_ATTRIBUTION = "GeoDanmark, Klimadatastyrelsen og kommunerne"
MAX_SIDE = 3000   # m: Danmarks Højdemodel at 0.4 m for about 3 x 3 km

# The newest spring orthophoto for every part of the country (a mosaic of the latest flights), 12.5 cm
ORTHO_WMS = "https://wms.datafordeler.dk/GeoDanmarkOrto/orto_foraar/1.0.0/WMS"
ORTHO_LAYER = "orto_foraar_12_5"
ORTHO_ATTRIBUTION = "Ortofoto forår, Klimadatastyrelsen"
OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"
MAX_IMAGE_PIXELS = 100_000_000   # per image (10 000 x 10 000); a coarser resolution is used above it
MAX_OSM_TILES = 150              # OSM's tile policy forbids bulk downloads: a lower zoom is used above it

# (WFS layer, table, label, on by default, visible when loaded). Buildings are kept whole, the rest is clipped.
THEMES = [
    ("gdk60:Bygning", "buildings", "Buildings (Bygning)", True, True),
    ("gdk60:Vejkant", "road_edges", "Road edges and kerbs (Vejkant)", True, True),
    ("gdk60:Vejmidte", "road_centrelines", "Road centre lines (Vejmidte)", True, False),
    ("gdk60:Helle", "traffic_islands", "Traffic islands (Helle)", True, True),
    ("gdk60:Parkering", "parking", "Parking (Parkering)", True, True),
    ("gdk60:Plads", "squares", "Squares and plazas (Plads)", True, True),
    ("gdk60:Soe", "lakes", "Lakes and ponds (Soe)", True, True),
    ("gdk60:Vandloebskant", "stream_edges", "Stream edges (Vandloebskant)", False, True),
    ("gdk60:Trae", "trees", "Trees (Trae)", True, True),
    ("gdk60:Traegruppe", "tree_groups", "Groups of trees (Traegruppe)", True, True),
    ("gdk60:Hegn", "fences", "Fences and hedges (Hegn)", True, True),
    ("gdk60:Trafikhegn", "traffic_barriers", "Traffic barriers (Trafikhegn)", False, True),
    ("gdk60:Bygvaerk", "structures", "Structures: bridges, tunnels … (Bygvaerk)", False, True),
    ("gdk60:Sportsbane", "sports_grounds", "Sports grounds (Sportsbane)", False, True),
    ("gdk60:RekreativtOmraade", "recreational_areas", "Recreational areas (RekreativtOmraade)", False, True),
]
BUILDINGS = 0


def osm_uri(rect, crs, context):
    """OpenStreetMap's standard tiles, with the highest zoom limited so the area needs at most MAX_OSM_TILES."""
    import math
    from urllib.parse import quote

    wgs = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), context.transformContext()).transformBoundingBox(rect)
    zoom = 19
    while zoom > 12:
        n = 2 ** zoom
        x0, x1 = (wgs.xMinimum() + 180) / 360 * n, (wgs.xMaximum() + 180) / 360 * n
        y = lambda lat: (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
        tiles = (math.floor(x1) - math.floor(x0) + 1) * (math.floor(y(wgs.yMinimum())) - math.floor(y(wgs.yMaximum())) + 1)
        if tiles <= MAX_OSM_TILES:
            break
        zoom -= 1
    return f"type=xyz&url={quote(OSM_TILES, safe='')}&zmin=0&zmax={zoom}"


def flat(geometry):
    """2D: GeoDanmark has Z values (the height of the registered outline), which only confuse later steps."""
    g = QgsGeometry(geometry)
    if QgsWkbTypes.hasZ(g.wkbType()) or QgsWkbTypes.hasM(g.wkbType()):
        g.get().dropZValue()
        g.get().dropMValue()
    return g


def clean_fields(fields):
    """The fields without `fid` (GeoPackage's own key) and GML bookkeeping."""
    out = QgsFields()
    for f in fields:
        if f.name().lower() not in ("fid", "gml_id"):
            out.append(QgsField(f))
    return out


def save_table(gpkg, table, fields, wkb, crs, features, first, transform_context):
    """Write features (a list of QgsFeature with `fields`) as `table` in the GeoPackage."""
    layer = QgsVectorLayer(f"{QgsWkbTypes.displayString(wkb)}?crs={crs.authid()}", table, "memory")
    layer.dataProvider().addAttributes(fields.toList())
    layer.updateFields()
    layer.dataProvider().addFeatures(features)
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName, options.layerName, options.fileEncoding = "GPKG", table, "UTF-8"
    options.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteFile if first
                                    else QgsVectorFileWriter.CreateOrOverwriteLayer)
    result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, gpkg, transform_context, options)
    if result[0] != QgsVectorFileWriter.NoError:
        raise QgsProcessingException(f"Could not write {table}: {result[1]}")


def copy_to(features, fields):
    out = []
    for f in features:
        g = QgsFeature(fields)
        g.setGeometry(f.geometry())
        g.setAttributes([f[name] if f.fields().indexOf(name) >= 0 else None for name in fields.names()])
        out.append(g)
    return out


def refresh_crs_cache():
    """CRS objects made in the Processing thread can be left in QGIS's cache and break when the thread ends
    (see layers.rebind_crs); clear the caches on the main thread before adding layers."""
    from qgis.core import QgsCoordinateTransform as T

    QgsCoordinateReferenceSystem.invalidateCache()
    T.invalidateCache()


class UrbanContext(QgsProcessingAlgorithm):
    AREA, EXTENT, THEMES_, HEIGHTS, RASTERS, ORTHO, OSM, IMAGE_RES, OUTPUT, LOAD, ATTRIBUTION = (
        "AREA", "EXTENT", "THEMES", "HEIGHTS", "RASTERS", "ORTHO", "OSM", "IMAGE_RES", "OUTPUT", "LOAD", "ATTRIBUTION")

    def name(self): return "urbancontext"
    def displayName(self): return "Urban context for a study area (Denmark)"
    def group(self): return "Data access"
    def groupId(self): return "dataaccess"
    def tags(self): return ["urban design", "context", "geodanmark", "buildings", "bygning", "vejkant", "dhm",
                            "danmarks højdemodel", "heights", "terrain", "blender", "study area", "download", "3d"]
    def createInstance(self): return UrbanContext()

    def shortHelpString(self):
        return (
            "The base data of an urban design project in one step, for a study area anywhere in Denmark:<br>"
            "• <b>GeoDanmark</b> layers you choose: buildings, kerbs, road centre lines, trees …, cut to the study "
            "area (buildings are kept whole);<br>"
            "• every building's <b>height</b>, <b>eave</b> height and <b>roof type</b> from Danmarks Højdemodel "
            "(see <i>Building heights from elevation models</i> for the fields);<br>"
            "• the <b>terrain</b> (DTM, e.g. for Blender) and the <b>height above ground</b> (nDSM, to check the "
            "heights) as GeoTIFFs in a folder next to the GeoPackage;<br>"
            "• the newest <b>orthophoto</b> (spring photo, 12.5 cm) and the standard <b>OpenStreetMap</b> map of the "
            "rectangle around the study area, as GeoTIFFs at the chosen resolution (0.1 m). OpenStreetMap is drawn "
            "at most at zoom 19 (about 0.17 m in Denmark), and at a lower zoom for large areas, to respect its tile "
            "policy.<br><br>"
            "<b>Study area</b>: a polygon layer, or leave it empty and <b>draw a rectangle</b> on the map with the "
            "… button beside <i>or a rectangle</i>. Keep it a few hundred metres across (at most 3 x 3 km).<br><br>"
            "Everything is saved in <b>one GeoPackage</b> and added to the project in a group, with buildings "
            "coloured by height. Road centre lines are added switched off: they are not edges of surfaces, so "
            "<i>Surface partition 1</i> should not use them.<br><br>"
            "Needs your <b>Datafordeleren key</b> (free) in the SemanticGIS panel's Settings. Put the attribution "
            "from the log on your maps.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(self.AREA, "Study area (polygon layer)",
                                                              [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterExtent(self.EXTENT, "… or a rectangle (draw it on the map)", optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.THEMES_, "GeoDanmark layers", options=[t[2] for t in THEMES], allowMultiple=True,
            defaultValue=[i for i, t in enumerate(THEMES) if t[3]]))
        self.addParameter(QgsProcessingParameterBoolean(self.HEIGHTS, "Building heights from Danmarks Højdemodel", True))
        self.addParameter(QgsProcessingParameterBoolean(self.RASTERS, "Save terrain and height above ground as GeoTIFF", True))
        self.addParameter(QgsProcessingParameterBoolean(self.ORTHO, "Orthophoto, newest (GeoTIFF)", True))
        self.addParameter(QgsProcessingParameterBoolean(self.OSM, "OpenStreetMap standard map (GeoTIFF)", True))
        self.addParameter(QgsProcessingParameterNumber(self.IMAGE_RES, "Resolution of orthophoto and map (m)",
                                                       DOUBLE_TYPE, 0.1, False, 0.05, 10.0))
        self.addParameter(QgsProcessingParameterFileDestination(self.OUTPUT, "GeoPackage", "GeoPackage (*.gpkg)"))
        self.addParameter(QgsProcessingParameterBoolean(self.LOAD, "Add the result to the project", True))
        self.addOutput(QgsProcessingOutputString(self.ATTRIBUTION, "Attribution"))

    def study_area(self, parameters, context, crs):
        source = self.parameterAsSource(parameters, self.AREA, context)
        if source is not None:
            geoms = [f.geometry() for f in source.getFeatures() if f.hasGeometry()]
            if not geoms:
                raise QgsProcessingException("The study area layer has no polygons (or none selected).")
            area = QgsGeometry.unaryUnion(geoms)
            area.transform(QgsCoordinateTransform(source.sourceCrs(), crs, context.transformContext()))
            return flat(area).makeValid()
        if parameters.get(self.EXTENT):
            area = self.parameterAsExtentGeometry(parameters, self.EXTENT, context, crs)
            if area is not None and not area.isEmpty():
                return area
        raise QgsProcessingException("Choose a study area layer, or draw a rectangle on the map.")

    def processAlgorithm(self, parameters, context, feedback):
        crs = el.dk_crs()
        area = self.study_area(parameters, context, crs)
        rect = area.boundingBox()
        if rect.width() > MAX_SIDE or rect.height() > MAX_SIDE:
            raise QgsProcessingException(f"The study area is {rect.width():.0f} x {rect.height():.0f} m; keep it "
                                         f"within {MAX_SIDE} x {MAX_SIDE} m.")
        feedback.pushInfo(f"Study area {rect.width():.0f} x {rect.height():.0f} m ({area.area() / 10000:.1f} ha)")
        el.require_key("datafordeler")
        chosen = sorted(self.parameterAsEnums(parameters, self.THEMES_, context))
        heights = self.parameterAsBool(parameters, self.HEIGHTS, context)
        rasters = self.parameterAsBool(parameters, self.RASTERS, context)
        if heights and BUILDINGS not in chosen:
            chosen.insert(0, BUILDINGS)
        gpkg = self.parameterAsFileOutput(parameters, self.OUTPUT, context)
        if not gpkg.lower().endswith(".gpkg"):
            gpkg += ".gpkg"
        if os.path.exists(gpkg):
            os.remove(gpkg)
        engine = QgsGeometry.createGeometryEngine(area.constGet())
        engine.prepareGeometry()
        tc = context.transformContext()

        # the study area first, so it is the bottom layer
        fields = QgsFields()
        fields.append(QgsField("name", STRING))
        f = QgsFeature(fields)
        f.setGeometry(area)
        f.setAttributes(["study area"])
        save_table(gpkg, "study_area", fields, QgsWkbTypes.multiType(area.wkbType()), crs, [f], True, tc)
        self.loaded = [("study_area", "Study area", True)]

        steps = len(chosen) + (2 if heights else 0)
        for n, i in enumerate(chosen):
            if feedback.isCanceled():
                return {}
            typename, table, label, _, visible = THEMES[i]
            feedback.setProgress(100 * n / steps)
            feedback.pushInfo(f"{label} …")
            fields, features = fetch(GEODANMARK_WFS, typename, rect, crs, feedback)
            kept = []
            for feat in features:
                if not feat.hasGeometry() or not engine.intersects(feat.geometry().constGet()):
                    continue
                g = flat(feat.geometry())
                if i != BUILDINGS:
                    g = g.intersection(area)
                    if g.isEmpty():
                        continue
                feat.setGeometry(g)
                kept.append(feat)
            feedback.pushInfo(f"  {len(kept)} features")
            if not kept:
                continue
            out_fields = clean_fields(fields)
            kept = copy_to(kept, out_fields)
            wkb = QgsWkbTypes.multiType(QgsWkbTypes.flatType(kept[0].geometry().wkbType()))
            if i == BUILDINGS and heights:
                kept, out_fields = self.add_heights(kept, out_fields, crs, rect, gpkg if rasters else None, feedback)
                if kept is None:
                    return {}
            for feat in kept:
                g = feat.geometry()
                g.convertToMultiType()
                feat.setGeometry(g)
            save_table(gpkg, table, out_fields, wkb, crs, kept, False, tc)
            self.loaded.append((table, label, visible))

        sources = [GEODANMARK_ATTRIBUTION] + ([el.DHM_ATTRIBUTION] if heights else [])
        images = [(self.ORTHO, "orthophoto", "Orthophoto (newest)", True),
                  (self.OSM, "openstreetmap", "OpenStreetMap", False)]
        for name, file, label, visible in images:
            if self.parameterAsBool(parameters, name, context) and not feedback.isCanceled():
                if self.save_image(file, label, visible, rect, crs, gpkg, parameters, context, feedback):
                    sources.append(ORTHO_ATTRIBUTION if file == "orthophoto" else None)
        attribution = "Contains data from " + ", ".join(s for s in sources if s) + f", {date.today().isoformat()}"
        if any(file == "openstreetmap" for _, _, file in getattr(self, "images", [])):
            attribution += f". Map {OSM_ATTRIBUTION}"
        feedback.pushInfo(f"Put this on your maps: {attribution}")
        self.gpkg, self.load = gpkg, self.parameterAsBool(parameters, self.LOAD, context)
        self.results = {self.OUTPUT: gpkg, self.ATTRIBUTION: attribution}
        return self.results

    def add_heights(self, buildings, fields, crs, rect, gpkg, feedback):
        """Buildings with the height fields; the terrain and nDSM of the whole study area saved when gpkg is given."""
        feedback.pushInfo("Building heights from Danmarks Højdemodel …")
        dsm, dtm = el.dhm_layer(el.DHM_SURFACE), el.dhm_layer(el.DHM_TERRAIN)
        geoms = [f.geometry() for f in buildings]
        # the grid covers the whole study area, so the terrain reaches its edges
        values, grids = el.building_heights(geoms, dsm, dtm, crs, cover=rect, feedback=feedback)
        if values is None:
            return None, None
        out_fields = height_fields(fields)
        out = [with_heights(f, out_fields, v) for f, v in zip(buildings, values)]
        feedback.pushInfo("Heights: " + el.summary(values))
        if gpkg:
            folder = os.path.splitext(gpkg)[0] + "_rasters"
            os.makedirs(folder, exist_ok=True)
            extent, a_dsm, a_dtm = grids
            self.rasters = []
            for name, arr, label in (("terrain", a_dtm, "Terrain (DTM)"),
                                     ("height_above_ground", a_dsm - a_dtm, "Height above ground (nDSM)")):
                path = os.path.join(folder, name + ".tif")
                el.write_tif(path, arr, extent, el.DHM_RESOLUTION, crs)
                self.rasters.append((path, label))
                feedback.pushInfo(f"  → {path}")
        return out, out_fields

    def save_image(self, file, label, visible, rect, crs, gpkg, parameters, context, feedback):
        """The orthophoto or the OSM map of the study area's rectangle as an RGBA GeoTIFF. Returns success."""
        res = self.parameterAsDouble(parameters, self.IMAGE_RES, context)
        pixels = (rect.width() / res) * (rect.height() / res)
        if pixels > MAX_IMAGE_PIXELS:
            res = round((rect.width() * rect.height() / MAX_IMAGE_PIXELS) ** 0.5 + 0.005, 2)
            feedback.pushInfo(f"{label}: the area is large, so it is saved at {res} m instead")
        if file == "orthophoto":
            uri = (f"crs={el.DK_CRS}&dpiMode=7&format=image/jpeg&layers={ORTHO_LAYER}&styles=&url={ORTHO_WMS}")
            layer = QgsRasterLayer(uri, label, "wms")
        else:
            layer = QgsRasterLayer(osm_uri(rect, crs, context), label, "wms")
        if not layer.isValid():
            feedback.reportError(f"{label}: the service could not be opened, skipped")
            return False
        folder = os.path.splitext(gpkg)[0] + "_rasters"
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, file + ".tif")
        feedback.pushInfo(f"{label} at {res} m …")
        error = write_raster(layer, rect, crs, res, path, context.transformContext())
        if error:
            feedback.reportError(f"{label}: {error}, skipped")
            return False
        self.images = getattr(self, "images", []) + [(path, label, file)]
        if visible:
            self.visible_images = getattr(self, "visible_images", set()) | {label}
        feedback.pushInfo(f"  → {path}")
        return True

    def postProcessAlgorithm(self, context, feedback):
        gpkg = getattr(self, "gpkg", None)
        results = getattr(self, "results", {})   # what this returns replaces processAlgorithm's results
        if not gpkg or not getattr(self, "load", False):
            return results
        refresh_crs_cache()
        project = context.project() or QgsProject.instance()
        layers, hidden = [], set()
        for table, label, visible in reversed(self.loaded):   # top of the tree first
            layer = QgsVectorLayer(f"{gpkg}|layername={table}", label, "ogr")
            if table == "buildings" and layer.fields().indexOf("height") >= 0:
                style_by_height(layer)
                save_default_style(layer, "height", "Buildings coloured by height")
            elif table == "study_area":
                style_outline(layer)
                save_default_style(layer, "outline", "Study area outline")
            if not visible:
                hidden.add(layer.id())
            layers.append(layer)
        for path, label in getattr(self, "rasters", []):
            raster = QgsRasterLayer(path, label)
            hidden.add(raster.id())
            layers.append(raster)
        for path, label, _ in getattr(self, "images", []):
            raster = QgsRasterLayer(path, label)
            if label not in getattr(self, "visible_images", set()):
                hidden.add(raster.id())
            layers.append(raster)
        group = add_group(project, "Urban context – " + os.path.basename(gpkg), layers)
        for node in group.findLayers():
            if node.layerId() in hidden:
                node.setItemVisibilityChecked(False)
        return results


def style_by_height(layer):
    try:
        from qgis.core import QgsClassificationQuantile, QgsGradientColorRamp, QgsSymbol
        from qgis.PyQt.QtGui import QColor

        renderer = QgsGraduatedSymbolRenderer("height")
        renderer.setClassificationMethod(QgsClassificationQuantile())
        renderer.setSourceSymbol(QgsSymbol.defaultSymbol(layer.geometryType()))
        renderer.setSourceColorRamp(QgsGradientColorRamp(QColor("#f2efe9"), QColor("#7a4a2a")))
        renderer.updateClasses(layer, 6)
        renderer.updateColorRamp(QgsGradientColorRamp(QColor("#f2efe9"), QColor("#7a4a2a")))
        layer.setRenderer(renderer)
    except Exception:
        pass   # the default style is fine


def style_outline(layer):
    from qgis.core import QgsFillSymbol, QgsSingleSymbolRenderer

    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
        {"color": "#00000000", "outline_color": "#d7301f", "outline_width": "0.6", "outline_style": "dash"})))
