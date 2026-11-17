"""Elevation models read on a common grid, and building heights from a surface and a terrain model.

Used by Building heights from elevation models and by Urban context for a study area. Rasters are read
through their QGIS data provider, so a local GeoTIFF and a WCS service (Danmarks Højdemodel) work the same way,
and only the area needed is fetched, in tiles.
"""
import math
from urllib.parse import urlencode

import numpy as np
from osgeo import gdal, ogr, osr
from qgis.core import Qgis, QgsCoordinateReferenceSystem, QgsProcessingException, QgsRasterLayer, QgsRectangle

# Danmarks Højdemodel on Datafordeleren (WCS 1.0). The user's Datafordeleren key is added to every request by
# the plugin (access.py), so it is never part of the layer source.
DHM_WCS = "https://wcs.datafordeler.dk/DHMNedboer/dhm_wcs/1.0.0/WCS"
DHM_SURFACE, DHM_TERRAIN = "dhm_overflade", "dhm_terraen"
DHM_RESOLUTION = 0.4
DHM_ATTRIBUTION = "Danmarks Højdemodel, Klimadatastyrelsen"
DK_CRS = "EPSG:25832"
MAX_PIXELS = 60_000_000   # per raster: about 3 x 3 km at 0.4 m
TILE = 1000               # pixels per side of each request

_NP_TYPES = {
    Qgis.DataType.Byte: np.uint8, Qgis.DataType.UInt16: np.uint16, Qgis.DataType.Int16: np.int16,
    Qgis.DataType.UInt32: np.uint32, Qgis.DataType.Int32: np.int32,
    Qgis.DataType.Float32: np.float32, Qgis.DataType.Float64: np.float64,
}


def require_key(profile_id="datafordeler"):
    """Stop with a message a beginner can act on when the plugin has no key for the service."""
    from ..access import KEYS

    if KEYS.has_key(profile_id):
        return
    profile = KEYS.profiles.get(profile_id)
    title = profile.title if profile else profile_id
    if not KEYS.loaded and KEYS.encrypted():
        raise QgsProcessingException(f"Your keys are locked. Open the SemanticGIS panel, unlock them with the QGIS "
                                     f"master password and run the tool again.")
    signup = f" Get one (free) at {profile.signup_url} and" if profile and profile.signup_url else ""
    raise QgsProcessingException(f"Danmarks Højdemodel needs your own {title}.{signup} enter it in the SemanticGIS "
                                 f"panel under Settings, then run the tool again.")


def dhm_layer(coverage):
    """Danmarks Højdemodel (surface or terrain) as a WCS layer, not added to the project."""
    require_key("datafordeler")
    uri = urlencode([("cache", "PreferNetwork"), ("crs", DK_CRS), ("format", "GTiff"),
                     ("identifier", coverage), ("url", DHM_WCS)])
    layer = QgsRasterLayer(uri, "DHM " + ("surface" if coverage == DHM_SURFACE else "terrain"), "wcs")
    if not layer.isValid():
        raise QgsProcessingException(f"Danmarks Højdemodel ({coverage}) could not be opened: "
                                     f"{layer.error().summary()[:300]}. Check your Datafordeleren key and connection.")
    return layer


def grid_extent(rect, res, margin_px=2):
    """`rect` grown by a margin and snapped to multiples of `res`, so pixels line up with DHM's 0.4 m grid."""
    m = margin_px * res
    return QgsRectangle(math.floor((rect.xMinimum() - m) / res) * res, math.floor((rect.yMinimum() - m) / res) * res,
                        math.ceil((rect.xMaximum() + m) / res) * res, math.ceil((rect.yMaximum() + m) / res) * res)


def grid_shape(extent, res):
    return int(round(extent.height() / res)), int(round(extent.width() / res))


def check_size(extent, res):
    rows, cols = grid_shape(extent, res)
    if rows * cols > MAX_PIXELS:
        raise QgsProcessingException(
            f"The area is too large ({extent.width():.0f} x {extent.height():.0f} m at {res} m): select fewer "
            f"buildings, draw a smaller study area or use a coarser resolution.")
    return rows, cols


def read_raster(layer, extent, res, feedback=None):
    """Band 1 of any QGIS raster layer (WCS, file …) for `extent` at resolution `res`, read in tiles.
    Returns float32 with NaN for nodata, or None when cancelled."""
    rows, cols = grid_shape(extent, res)
    out = np.full((rows, cols), np.nan, dtype=np.float32)
    provider = layer.dataProvider()
    tiles = [(r, c) for r in range(0, rows, TILE) for c in range(0, cols, TILE)]
    for i, (r0, c0) in enumerate(tiles):
        if feedback is not None and feedback.isCanceled():
            return None
        h, w = min(TILE, rows - r0), min(TILE, cols - c0)
        ext = QgsRectangle(extent.xMinimum() + c0 * res, extent.yMaximum() - (r0 + h) * res,
                           extent.xMinimum() + (c0 + w) * res, extent.yMaximum() - r0 * res)
        block = provider.block(1, ext, w, h)
        if block is None or not block.isValid():
            raise QgsProcessingException(f"Could not read {layer.name()} (service down, or the area too large?)")
        dtype = _NP_TYPES.get(block.dataType())
        if dtype is None:
            raise QgsProcessingException(f"{layer.name()} has a data type this tool cannot read")
        arr = np.frombuffer(bytes(block.data()), dtype=dtype).reshape(h, w).astype(np.float32)
        if block.hasNoDataValue():
            arr[arr == block.noDataValue()] = np.nan
        if provider.sourceHasNoDataValue(1):
            arr[arr == provider.sourceNoDataValue(1)] = np.nan
        arr[arr < -1000] = np.nan   # nodata such as -9999 that is not declared
        out[r0:r0 + h, c0:c0 + w] = arr
        if feedback is not None and len(tiles) > 1:
            feedback.pushDebugInfo(f"{layer.name()}: tile {i + 1} of {len(tiles)}")
    return out


def write_tif(path, arr, extent, res, crs):
    """A float32 GeoTIFF (nodata -9999), tiled and compressed."""
    ds = gdal.GetDriverByName("GTiff").Create(path, arr.shape[1], arr.shape[0], 1, gdal.GDT_Float32,
                                              ["COMPRESS=DEFLATE", "PREDICTOR=3", "TILED=YES"])
    ds.SetGeoTransform((extent.xMinimum(), res, 0, extent.yMaximum(), 0, -res))
    ds.SetProjection(crs.toWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(-9999)
    band.WriteArray(np.where(np.isnan(arr), -9999, arr))
    ds = None


# -- building heights ---------------------------------------------------------------------------------------

HEIGHT_FIELDS = ["height", "eave", "roof_rise", "roof_type", "ground", "roof_abs", "h_median", "h_max_raw", "n_px", "h_check"]
DEFAULTS = dict(res=DHM_RESOLUTION, buffer=0.5, pct=95.0, k=3.0, eave_pct=10.0, flat_rise=1.0, min_px=10)


def label_grid(geoms, extent, res, crs, buffer):
    """Pixel value i + 1 where the pixel centre lies inside building i (shrunk by `buffer`), 0 elsewhere."""
    rows, cols = grid_shape(extent, res)
    mem = gdal.GetDriverByName("MEM").Create("", cols, rows, 1, gdal.GDT_Int32)
    mem.SetGeoTransform((extent.xMinimum(), res, 0, extent.yMaximum(), 0, -res))
    srs = osr.SpatialReference()
    srs.ImportFromWkt(crs.toWkt())
    mem.SetProjection(srs.ExportToWkt())
    driver = ogr.GetDriverByName("Memory") or ogr.GetDriverByName("MEM")
    ods = driver.CreateDataSource("b")
    olyr = ods.CreateLayer("b", srs, ogr.wkbMultiPolygon)
    olyr.CreateField(ogr.FieldDefn("bid", ogr.OFTInteger))
    for i, g in enumerate(geoms):
        if g is None or g.isEmpty():
            continue
        gg = g.buffer(-buffer, 4) if buffer > 0 else g
        if gg.isEmpty():   # a very small building: use it unbuffered
            gg = g
        f = ogr.Feature(olyr.GetLayerDefn())
        f.SetField("bid", i + 1)
        f.SetGeometry(ogr.CreateGeometryFromWkb(bytes(gg.asWkb())))
        olyr.CreateFeature(f)
    gdal.RasterizeLayer(mem, [1], olyr, options=["ATTRIBUTE=bid"])
    labels = mem.GetRasterBand(1).ReadAsArray()
    return labels


def roof_statistics(h, k, pct, eave_pct, flat_rise):
    """(height, eave, median, raw max) of the height-above-ground values `h` inside one building.

    Spikes above the roof (chimneys, antennas, trees) are removed with median + k·MAD; the height is the `pct`
    percentile of the rest. The eave assumes roof heights spread evenly from eave to ridge: the line through the
    low and high percentile is extended down to 0 %, after holes below the roof (courtyards) are removed with
    median − k·MAD."""
    med, hmax = float(np.median(h)), float(h.max())
    h_low = h
    if k > 0 and h.size >= 5:
        mad = max(float(np.median(np.abs(h - med))) * 1.4826, 0.5)   # a floor, so flat roofs are not cut up
        h = h[h <= med + k * mad]
        h_low = h[h >= med - k * mad]
        if h_low.size == 0:
            h_low = h
    height = float(np.percentile(h, pct))
    p_lo, p_hi = float(np.percentile(h_low, eave_pct)), float(np.percentile(h_low, pct))
    f_lo, f_hi = eave_pct / 100.0, pct / 100.0
    eave = p_lo - (p_hi - p_lo) * f_lo / max(f_hi - f_lo, 1e-6)
    eave = min(max(eave, 0.0), height)
    if height - eave < flat_rise:
        eave = height
    return height, eave, med, hmax


def building_heights(geoms, dsm, dtm, crs, res=DHM_RESOLUTION, buffer=0.5, pct=95.0, k=3.0, eave_pct=10.0,
                     flat_rise=1.0, min_px=10, cover=None, feedback=None):
    """Heights for buildings `geoms` (QgsGeometry in `crs`, the rasters' CRS) from raster layers `dsm` and `dtm`.

    Returns (values, grids): values is one dict per building with the HEIGHT_FIELDS; grids is
    (extent, dsm array, dtm array), or (None, None) when cancelled. The grid also covers the rectangle `cover`
    (e.g. a whole study area) when given."""
    bbox = QgsRectangle(cover) if cover is not None else QgsRectangle()
    for g in geoms:
        if g is not None and not g.isEmpty():
            bbox.combineExtentWith(g.boundingBox())
    if bbox.isEmpty():
        raise QgsProcessingException("The buildings have no geometry.")
    extent = grid_extent(bbox, res)
    rows, cols = check_size(extent, res)
    if feedback is not None:
        feedback.pushInfo(f"Area {extent.width():.0f} x {extent.height():.0f} m = {cols} x {rows} pixels")
        feedback.pushInfo(f"Reading {dsm.name()} …")
    a_dsm = read_raster(dsm, extent, res, feedback)
    if feedback is not None:
        feedback.setProgress(35)
        feedback.pushInfo(f"Reading {dtm.name()} …")
    a_dtm = read_raster(dtm, extent, res, feedback) if a_dsm is not None else None
    if a_dsm is None or a_dtm is None:
        return None, None
    if feedback is not None:
        feedback.setProgress(70)
    ndsm = a_dsm - a_dtm

    labels = label_grid(geoms, extent, res, crs, buffer).ravel()
    hv, gv = ndsm.ravel(), a_dtm.ravel()
    inside = labels > 0
    lab, hv, gv = labels[inside], hv[inside], gv[inside]
    order = np.argsort(lab, kind="stable")
    lab, hv, gv = lab[order], hv[order], gv[order]
    ids, starts = np.unique(lab, return_index=True)
    ends = np.append(starts[1:], len(lab))

    values = [{"h_check": "no data"} for _ in geoms]
    for bid, s, e in zip(ids, starts, ends):
        h, g = hv[s:e], gv[s:e]
        h = h[~np.isnan(h)]
        g = g[~np.isnan(g)]
        if h.size == 0:
            continue
        height, eave, med, hmax = roof_statistics(h, k, pct, eave_pct, flat_rise)
        check = "ok" if h.size >= min_px else "few pixels"
        if height < 0:
            check, height, eave = "negative", 0.0, 0.0
        ground = float(np.median(g)) if g.size else None
        values[bid - 1] = {
            "height": round(height, 2), "eave": round(eave, 2), "roof_rise": round(height - eave, 2),
            "roof_type": "flat" if eave >= height else "pitched",
            "ground": None if ground is None else round(ground, 2),
            "roof_abs": None if ground is None else round(ground + height, 2),
            "h_median": round(med, 2), "h_max_raw": round(hmax, 2), "n_px": int(h.size), "h_check": check}
    return values, (extent, a_dsm, a_dtm)


def summary(values):
    counts = {}
    for v in values:
        counts[v["h_check"]] = counts.get(v["h_check"], 0) + 1
    return ", ".join(f"{n} {name}" for name, n in sorted(counts.items(), key=lambda kv: -kv[1]))


def dk_crs():
    return QgsCoordinateReferenceSystem(DK_CRS)
