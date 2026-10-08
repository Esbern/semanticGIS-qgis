"""Turning catalogue services into QGIS layers.

The URI builders are plain functions so they can be tested without a running QGIS.
`add_service_layer` creates the layer, puts it in a group per leaf and tags it with its
SPHERE provenance (leaf, dataset, service) as layer custom properties, so a saved project
still knows where each layer came from.
"""

from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

PREFERRED_CRS = "EPSG:25832"
ROOT_GROUP = "SemanticGIS"
PROPERTY_PREFIX = "semanticgis/"


def with_params(url, **params):
    """Set query parameters on an endpoint URL (replacing any existing ones of the same name)."""
    params = {k: v for k, v in params.items() if v}
    if not params:
        return url
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in params] + list(params.items())
    return urlunsplit(parts._replace(query=urlencode(query)))


def pick_crs(available, preferred=PREFERRED_CRS):
    """Choose a CRS the service offers, preferring the project's and then EPSG:25832.
    Only when the service declares no CRS at all is the project CRS used unverified."""
    def norm(code):
        code = code.upper()
        if "EPSG" in code:
            return "EPSG:" + code.replace("::", ":").rstrip(":").split(":")[-1]
        return code

    offered = [norm(c) for c in available or []]
    for wanted in (preferred, PREFERRED_CRS):
        if wanted and norm(wanted) in offered:
            return norm(wanted)
    return offered[0] if offered else norm(preferred or PREFERRED_CRS)


def tile_matrix_set_crs(name):
    """Best guess of a WMTS tile matrix set's CRS from its name."""
    n = name.upper()
    if "25832" in n or "ETRS89" in n or "UTM32" in n:
        return "EPSG:25832"
    if "3857" in n or "GOOGLE" in n or "WEBMERCATOR" in n or "900913" in n:
        return "EPSG:3857"
    if "4326" in n or "WGS84" in n or "CRS84" in n:
        return "EPSG:4326"
    return PREFERRED_CRS


def wfs_uri(url, layer, only_in_view=True):
    params = {
        "url": url,
        "typename": layer,
        "version": "auto",
        "pagingEnabled": "true",
        "restrictToRequestBBOX": "1" if only_in_view else "0",
    }
    return " ".join(f"{k}='{v}'" for k, v in params.items())


def wms_uri(url, layer, crs):
    return urlencode(
        [("crs", crs), ("format", "image/png"), ("layers", layer), ("styles", ""), ("url", url)]
    )


def wmts_uri(url, layer, tile_matrix_sets, formats, preferred_crs=PREFERRED_CRS):
    sets = tile_matrix_sets or ["EPSG:25832"]
    tms = next((s for s in sets if tile_matrix_set_crs(s) == preferred_crs), None) or next(
        (s for s in sets if tile_matrix_set_crs(s) == PREFERRED_CRS), sets[0]
    )
    fmt = next((f for f in formats or [] if f == "image/png"), (formats or ["image/png"])[0])
    capabilities = url + ("&" if "?" in url else "?") + "SERVICE=WMTS&REQUEST=GetCapabilities"
    return urlencode(
        [
            ("contextualWMSLegend", "0"),
            ("crs", tile_matrix_set_crs(tms)),
            ("dpiMode", "7"),
            ("format", fmt),
            ("layers", layer),
            ("styles", "default"),
            ("tileMatrixSet", tms),
            ("url", capabilities),
        ]
    )


def xyz_uri(url, zmin=0, zmax=19):
    return f"type=xyz&url={quote(url, safe='')}&zmin={zmin or 0}&zmax={zmax or 19}"


def layer_source(service, only_in_view=True, preferred_crs=PREFERRED_CRS):
    """(uri, provider) for a loadable service. The URI never contains a key: keys are added to
    each request as it is sent (access.py), so saved projects carry none."""
    url = service.endpoint
    check = service.check
    if service.type == "xyz":
        return xyz_uri(url, service.zmin, service.zmax), "wms"
    if service.type == "wcs":
        # Real values (e.g. heights), not a picture. Request a CRS the coverage supports (the
        # project CRS if it is one of them); QGIS reprojects on the fly. Asking for an unsupported
        # CRS returns empty cells, so with no known list let QGIS use the coverage's own CRS.
        params = [("cache", "PreferNetwork")]
        if check.get("crs"):
            params.append(("crs", pick_crs(check["crs"], preferred_crs)))
        params += [("format", "GTiff"), ("identifier", service.layer_name), ("url", url)]
        return urlencode(params), "wcs"
    if service.type == "wfs":
        return wfs_uri(url, service.layer_name, only_in_view), "WFS"
    if service.type == "wms":
        return wms_uri(url, service.layer_name, pick_crs(check.get("crs"), preferred_crs)), "wms"
    if service.type == "wmts":
        return wmts_uri(url, service.layer_name, check.get("tile_matrix_sets"), check.get("formats"), preferred_crs), "wms"
    raise ValueError(f"Service type {service.type} cannot be loaded as a layer")


def build_layer(service, dataset, only_in_view=True, preferred_crs=PREFERRED_CRS):
    """Create (but do not add) the layer. Talks to the server, so it may be slow; safe to run in a
    QgsTask as long as the caller moves the layer to the main thread before adding it.

    Raises RuntimeError with the provider's error when the layer is invalid.
    """
    from qgis.core import QgsRasterLayer, QgsVectorLayer

    uri, provider = layer_source(service, only_in_view, preferred_crs)
    name = f"{dataset.title} — {service.label}" if service.label != dataset.title else dataset.title
    layer = QgsVectorLayer(uri, name, provider) if provider == "WFS" else QgsRasterLayer(uri, name, provider)
    if not layer.isValid():
        raise RuntimeError(layer.error().summary() or f"The {service.type.upper()} layer could not be loaded")
    return layer


def _add_to_group(layer, group_name):
    from qgis.core import QgsProject

    project = QgsProject.instance()
    root = project.layerTreeRoot()
    group = root.findGroup(ROOT_GROUP) or root.insertGroup(0, ROOT_GROUP)
    sub = group.findGroup(group_name) or group.addGroup(group_name)
    project.addMapLayer(layer, False)
    sub.insertLayer(0, layer)
    return layer


def place_layer(layer, service, leaf, dataset, group_name=None):
    """Tag the layer with its SPHERE provenance and add it under SemanticGIS > <leaf or owner>.

    `leaf` may be None for a dataset browsed by owner. Main thread only.
    """
    from .attribution import stamp

    stamp(layer, dataset.attribution, service.type)
    for key, value in {
        "leaf": leaf.id if leaf else "",
        "leaf_title": leaf.title if leaf else "",
        "dataset": dataset.id,
        "dataset_page": dataset.page,
        "service": service.id,
        "access": service.access or "",
    }.items():
        layer.setCustomProperty(PROPERTY_PREFIX + key, value)
    return _add_to_group(layer, leaf.title if leaf else (group_name or "Datasets"))


BASEMAP_GROUP = "Basemaps"


def build_basemap_layer(basemap, service, preferred_crs=PREFERRED_CRS):
    """Create (not add) a basemap layer; safe in a QgsTask like build_layer."""
    from qgis.core import QgsRasterLayer, QgsVectorLayer

    uri, provider = layer_source(service, True, preferred_crs)
    name = basemap.title + (f" ({basemap.period})" if basemap.period and basemap.period not in basemap.title else "")
    layer = QgsVectorLayer(uri, name, provider) if provider == "WFS" else QgsRasterLayer(uri, name, provider)
    if not layer.isValid():
        raise RuntimeError(layer.error().summary() or f"The basemap {basemap.title} could not be loaded")
    return layer


def place_basemap_layer(layer, basemap, service):
    """Add a basemap at the bottom of the layer tree (under the user's data), with attribution."""
    from qgis.core import QgsProject

    from .attribution import stamp

    stamp(layer, basemap.attribution, service.type)
    for key, value in {"basemap": basemap.id, "basemap_page": basemap.page, "service": service.id,
                       "access": service.access or "", "licence": basemap.licence or ""}.items():
        layer.setCustomProperty(PROPERTY_PREFIX + key, value)
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    group = root.findGroup(BASEMAP_GROUP) or root.addGroup(BASEMAP_GROUP)   # addGroup appends at the bottom
    project.addMapLayer(layer, False)
    group.insertLayer(0, layer)   # the basemap just added is the visible one
    return layer


def build_reference_layer(source, geometry):
    """A layer for one geometry of a reference unit, read remotely (range requests) by OGR.

    Safe to run in a QgsTask; the caller moves it to the main thread.
    """
    from qgis.core import QgsVectorLayer

    url = geometry.url
    path = "/vsicurl/" + url if url.startswith(("http://", "https://")) else url
    layer = QgsVectorLayer(path, f"{source.unit} {geometry.scale}", "ogr")
    if not layer.isValid():
        raise RuntimeError(layer.error().summary() or f"Could not open {url}")
    return layer


def place_reference_layer(layer, source, geometry):
    for key, value in {
        "reference": source.realisation,
        "reference_unit": source.unit,
        "id_scheme": source.id_scheme,
        "scale": geometry.scale,
        "leaf": source.leaf or "",
    }.items():
        layer.setCustomProperty(PROPERTY_PREFIX + key, value)
    return _add_to_group(layer, "Reference Framework")


def add_service_layer(service, leaf, dataset, only_in_view=True):
    """Build and place a layer in one blocking call (used by scripts and tests)."""
    from qgis.core import QgsProject

    preferred = QgsProject.instance().crs().authid() or PREFERRED_CRS
    layer = build_layer(service, dataset, only_in_view, preferred)
    return place_layer(layer, service, leaf, dataset)
