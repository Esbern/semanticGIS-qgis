"""Reading reference units from their (remote) geometry files with GDAL.

GeoPackages on HTTP servers that accept range requests are read through /vsicurl/, so only
the needed pages travel: the 78 MB European LAU file yields Denmark's 99 units in a few
seconds when filtered by country and bounding box. Every extract is cached in the QGIS
profile, so a repeated join is instant. Safe to call from a QgsTask (no QGIS objects).
"""

import hashlib
import json
import os

from osgeo import gdal, ogr

from .join import RefUnit

NAME_FIELDS = ("NAME_LATN", "LAU_NAME", "NUTS_NAME", "NAME", "name", "navn", "Navn")
COUNTRY_FIELD = "CNTR_CODE"
LEVEL_FIELD = "LEVL_CODE"
COUNTRY_FILTER_ABOVE = 5000   # files with more features than this are read per country

gdal.UseExceptions()


def _gdal_path(url):
    return "/vsicurl/" + url if url.startswith(("http://", "https://")) else url


def _configure_thread():
    # Avoid directory listings and needless requests on remote files (thread-local, so QGIS is unaffected).
    gdal.SetThreadLocalConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    gdal.SetThreadLocalConfigOption("GDAL_HTTP_TIMEOUT", "60")
    gdal.SetThreadLocalConfigOption("CPL_VSIL_CURL_CHUNK_SIZE", "65536")


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _extract(url, cache_dir, where=None, bbox=None):
    """Copy the (filtered) features of `url` into a cached local GeoPackage and return its path."""
    key = hashlib.sha1(json.dumps([url, where, bbox]).encode()).hexdigest()[:16]
    path = os.path.join(cache_dir, f"ref-{key}.gpkg")
    if os.path.exists(path):
        return path
    os.makedirs(cache_dir, exist_ok=True)
    _configure_thread()
    tmp = path[: -len(".gpkg")] + ".part.gpkg"  # GDAL picks the driver from the extension
    if os.path.exists(tmp):
        os.remove(tmp)
    options = gdal.VectorTranslateOptions(
        format="GPKG",
        where=where,
        spatFilter=list(bbox) if bbox else None,
        geometryType="PROMOTE_TO_MULTI",
        layerName="units",
    )
    gdal.VectorTranslate(tmp, _gdal_path(url), options=options)
    os.replace(tmp, path)
    return path


def describe(url):
    """(field names, feature count) of a geometry file, read from its header only."""
    _configure_thread()
    ds = gdal.OpenEx(_gdal_path(url), gdal.OF_VECTOR)
    layer = ds.GetLayer(0)
    defn = layer.GetLayerDefn()
    fields = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
    return fields, layer.GetFeatureCount(force=0)


def read_units(source, cache_dir, country=None, country_bbox=None):
    """The reference units of `source` (attributes and bounding boxes), from its cheapest geometry.

    Large files (e.g. LAU, ~98 000 units) are read for one country only.
    """
    url = source.index_geometry.url
    fields, count = describe(url)
    if source.id_scheme not in fields:
        raise ValueError(f"{source.id_scheme} is not a field of {url}")
    where = None
    bbox = None
    if count > COUNTRY_FILTER_ABOVE or count < 0:
        if COUNTRY_FIELD not in fields or not country:
            raise ValueError(f"{source.label} is too large to read without a country filter")
        where = f"{COUNTRY_FIELD} = {_quote(country.upper())}"
        bbox = country_bbox
    path = _extract(url, cache_dir, where, bbox)

    name_field = next((f for f in NAME_FIELDS if f in fields), None)
    ds = ogr.Open(path)
    layer = ds.GetLayer(0)
    units = []
    for feature in layer:
        geometry = feature.GetGeometryRef()
        env = geometry.GetEnvelope() if geometry else None   # (minx, maxx, miny, maxy)
        units.append(
            RefUnit(
                id=str(feature.GetField(source.id_scheme)),
                name=feature.GetField(name_field) if name_field else None,
                country=feature.GetField(COUNTRY_FIELD) if COUNTRY_FIELD in fields else None,
                level=feature.GetField(LEVEL_FIELD) if LEVEL_FIELD in fields else None,
                bbox=(env[0], env[2], env[1], env[3]) if env else None,
            )
        )
    return units


def country_bbox(units, country):
    """Bounding box of a country from units that include a country-level unit (NUTS level 0)."""
    country = (country or "").upper()
    for unit in units:
        if unit.id.upper() == country and unit.bbox:
            return unit.bbox
    return None


def union_bbox(units):
    boxes = [u.bbox for u in units if u.bbox]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def fetch_geometries(source, geometry, ids, cache_dir, bbox=None):
    """Local GeoPackage with the units `ids` of `source` at the scale of `geometry`.

    The bounding box of the wanted units (from the index) lets GDAL use the remote file's
    spatial index instead of scanning it.
    """
    ids = sorted(set(ids))
    if not ids:
        raise ValueError("No units to fetch")
    where = f"{source.id_scheme} IN ({', '.join(_quote(i) for i in ids)})"
    return _extract(geometry.url, cache_dir, where, bbox)
