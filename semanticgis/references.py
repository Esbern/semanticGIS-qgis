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


WFS_FILTER_MAX = 30   # up to this many units: an attribute filter in the request; more: the whole layer, once (the URL would be too long)


def _wfs_key(source):
    from .access import KEYS
    key = KEYS.key(source.wfs.get("access", "datafordeler"))
    if not key:
        raise RuntimeError("The geometry comes from Datafordeleren: set your Datafordeleren API key first (SemanticGIS settings).")
    return key


def fetch_wfs(source, ids, cache_dir):
    """Local GeoPackage with the units `ids` from the source's WFS (DAGI on Datafordeleren). The key is added to
    the request only; the cached file and the joined layer carry none."""
    w = source.wfs
    gdal.SetConfigOption("OGR_WFS_PAGING_ALLOWED", "ON")
    url = f"WFS:{w['url']}?apikey={_wfs_key(source)}"
    where = f"{w['id_field']} IN ({', '.join(_quote(i) for i in ids)})"
    if len(ids) <= WFS_FILTER_MAX:
        return _extract_wfs(url, w["layer"], cache_dir, ("ids", w["url"], w["layer"], sorted(ids)), where)
    whole = _extract_wfs(url, w["layer"], cache_dir, ("all", w["url"], w["layer"]), None)
    return _extract(whole, cache_dir, where)


def _extract_wfs(url, layer, cache_dir, cache_key, where):
    key = hashlib.sha1(json.dumps(cache_key).encode()).hexdigest()[:16]
    path = os.path.join(cache_dir, f"wfs-{key}.gpkg")
    if os.path.exists(path):
        return path
    os.makedirs(cache_dir, exist_ok=True)
    tmp = path[: -len(".gpkg")] + ".part.gpkg"
    if os.path.exists(tmp):
        os.remove(tmp)
    options = gdal.VectorTranslateOptions(format="GPKG", layers=[layer], where=where, geometryType="MULTIPOLYGON", layerName="units")
    gdal.VectorTranslate(tmp, url, options=options)
    os.replace(tmp, path)
    return path


def fetch_geometries(source, geometry, ids, cache_dir, bbox=None):
    """Local GeoPackage with the units `ids` of `source` at the scale of `geometry`.

    The bounding box of the wanted units (from the index) lets GDAL use the remote file's
    spatial index instead of scanning it.
    """
    ids = sorted(set(ids))
    if not ids:
        raise ValueError("No units to fetch")
    if getattr(source, "wfs", None):
        return fetch_wfs(source, ids, cache_dir)
    where = f"{source.id_scheme} IN ({', '.join(_quote(i) for i in ids)})"
    return _extract(geometry.url, cache_dir, where, bbox)


# -- units as they were on a date: DAGI's bitemporal GraphQL on Datafordeleren --------------------------------

GRAPHQL_PAGE = 1000
GRAPHQL_CODES = 400        # codes per request in a filter
VERSIONS_LAYER = "versions"


def _graphql(cfg, key, query, timeout=300):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(f"{cfg['url']}?apiKey={key}", data=json.dumps({"query": query}).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": "SemanticGIS QGIS plugin"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:       # the message, never the URL (it holds the key)
        raise RuntimeError(f"Datafordeleren answered {e.code} {e.reason}") from None
    if d.get("errors"):
        raise RuntimeError("Datafordeleren: " + d["errors"][0].get("message", "error")[:300])
    return d["data"]


def _graphql_all(cfg, key, args, fields, codes, id_field):
    """Every node for `args` (time arguments), the codes in chunks, the pages followed."""
    entity, out = cfg["entity"], []
    chunks = [codes[i:i + GRAPHQL_CODES] for i in range(0, len(codes), GRAPHQL_CODES)] or [[]]
    for chunk in chunks:
        where = f', where:{{{id_field}:{{in:{json.dumps(chunk)}}}}}' if chunk else ""
        after = ""
        while True:
            d = _graphql(cfg, key, f'{{{entity}(first:{GRAPHQL_PAGE}{after}, {args}{where}){{pageInfo{{hasNextPage endCursor}} nodes{{{fields}}}}}}}')[entity]
            out += d["nodes"]
            if not d["pageInfo"]["hasNextPage"]:
                break
            after = f', after:"{d["pageInfo"]["endCursor"]}"'
    return out


def _now():
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def unit_versions(source, codes):
    """Every version of the units `codes` in valid time, as the register knows them now (no geometry: quick)."""
    from .timeslice import versions_from
    cfg, w = source.wfs["graphql"], source.wfs
    nodes = _graphql_all(cfg, _wfs_key(source), f'registreringstid:"{_now()}"', f"{w['id_field']} {w.get('name_field', 'navn')} virkningFra virkningTil",
                         sorted(set(codes)), w["id_field"])
    return versions_from(nodes, w["id_field"], w.get("name_field", "navn"))


def versions_path(source, cache_dir):
    return os.path.join(cache_dir, f"dagi-{source.wfs['graphql']['entity'].lower()}.gpkg")


def _cached_keys(path):
    if not os.path.exists(path):
        return set()
    ds = ogr.Open(path)
    layer = ds.GetLayerByName(VERSIONS_LAYER)
    return {(f.GetField("code"), f.GetField("stamp")) for f in layer} if layer else set()


def fetch_unit_versions(source, needed, cache_dir, progress=None):
    """Make sure the cache holds the geometry of every version in `needed` ({(code, stamp): UnitVersion}); returns
    the cache's path. A version is fetched at the instant it begins, so the register returns exactly it."""
    w, cfg = source.wfs, source.wfs["graphql"]
    path = versions_path(source, cache_dir)
    os.makedirs(cache_dir, exist_ok=True)
    missing = [k for k in needed if k not in _cached_keys(path)]
    if not missing:
        return path
    by_stamp = {}
    for code, stamp in missing:
        by_stamp.setdefault(stamp, []).append(code)
    key, now = _wfs_key(source), _now()
    if os.path.exists(path):
        ds = ogr.Open(path, 1)
        layer = ds.GetLayerByName(VERSIONS_LAYER)
    else:
        from osgeo import osr
        ds = ogr.GetDriverByName("GPKG").CreateDataSource(path)
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(int(str(w.get("crs") or "EPSG:25832").split(":")[-1]))
        layer = ds.CreateLayer(VERSIONS_LAYER, srs, ogr.wkbMultiPolygon)
        for name in ("code", "name", "stamp", "valid_from", "valid_to"):
            layer.CreateField(ogr.FieldDefn(name, ogr.OFTString))
    defn = layer.GetLayerDefn()
    for i, (stamp, codes) in enumerate(sorted(by_stamp.items())):
        if progress and progress(i, len(by_stamp)):
            break
        nodes = _graphql_all(cfg, key, f'registreringstid:"{now}", virkningstid:"{stamp}"', f"{w['id_field']} {w.get('name_field', 'navn')} virkningFra virkningTil geometri{{wkt}}",
                             sorted(codes), w["id_field"])
        layer.StartTransaction()
        for n in nodes:
            v = needed.get((str(n[w["id_field"]]), n["virkningFra"]))
            if v is None or not (n.get("geometri") or {}).get("wkt"):
                continue
            f = ogr.Feature(defn)
            f.SetField("code", v.code); f.SetField("name", v.name or ""); f.SetField("stamp", v.stamp)
            f.SetField("valid_from", v.valid_from.isoformat()); f.SetField("valid_to", v.valid_to.isoformat() if v.valid_to else None)
            g = ogr.ForceToMultiPolygon(ogr.CreateGeometryFromWkt(n["geometri"]["wkt"]))
            f.SetGeometry(g)
            layer.CreateFeature(f)
        layer.CommitTransaction()
    layer = defn = None
    ds = None                               # closes the file before QGIS opens it
    return path


def units_on(source, codes, days, cache_dir, progress=None):
    """The versions of the units `codes` valid on each of `days`, with their geometry in the cache.
    Returns (cache path, {day: {code: UnitVersion}}, the date DAGI's history begins)."""
    import datetime as dt

    from .timeslice import pick
    versions = unit_versions(source, codes)
    begins = source.wfs["graphql"].get("from")
    begins = dt.date.fromisoformat(begins[:10]) if begins else min((v.valid_from for v in versions), default=None)
    chosen = {d: pick(versions, d, begins) for d in days}
    needed = {(v.code, v.stamp): v for c in chosen.values() for v in c.values()}
    return fetch_unit_versions(source, needed, cache_dir, progress), chosen, begins
