"""Standard symbology: a legend belongs to a code scheme, and a layer gets it because one of its columns uses that scheme.

A legend has QGIS styles (files on the data share, the publisher's own where there are any) and a list of codes with colours
(in the catalogue), used when a style cannot be fetched. Only the renderer of a style is read: no forms, actions or scripts.
"""

import re
from dataclasses import dataclass

LEGENDS = {}                    # scheme id -> legend, of the loaded catalogue (set by the panel)
NONE, COLOURS = "none", "colours"
MAX_BYTES = 5_000_000
_styles = {}                    # href -> text, fetched in this session
ORIGIN = {"official": "the publisher's own", "converted": "converted from the publisher's", "ours": "made for SemanticGIS"}
# a symbol may carry its picture (base64) but must not point to a file or address
_OUTSIDE = re.compile(r'value="(?:https?:|ftp:|file:|/|\\\\|[A-Za-z]:[\\/])', re.I)


@dataclass
class Choice:
    id: str
    title: str
    lang: str = ""
    href: str = ""


def column_for(dataset, service):
    """(legend, column of this file or service that uses its scheme), or None. The column the legend was made for is preferred."""
    for version in getattr(dataset, "versions", []):
        for m in version.meanings:
            if not (m["protocol"] == "file" and service.type == "file" and f"/{m['snapshot']}/" in service.url and m["file"] in ("*", service.layer)):
                continue
            found = [(LEGENDS[q["scheme"]], c) for c, q in (m.get("qualifiers") or {}).items() if q.get("scheme") in LEGENDS]
            return next(((l, c) for l, c in found if c.lower() == l["column"].lower()), found[0] if found else None)
    return None


def choices(legend, geometry, locale="en"):
    """The legends on offer for a layer, the default first: the user's language, then English, then the rest."""
    want = (locale or "en")[:2].lower()
    styles = [Choice(s["id"], s["title"], s.get("lang", ""), s["href"]) for s in legend["styles"] if geometry is None or s["geometry"] == geometry]
    styles.sort(key=lambda s: (s.lang != want, s.lang != "en"))
    return styles + [Choice(COLOURS, "Colours only"), Choice(NONE, "No legend")]


def fetch(href):
    """The text of a style on the share, or None (no login, offline, too large). May be slow: not on the main thread."""
    if href in _styles:
        return _styles[href]
    from osgeo import gdal

    from .access import KEYS
    from .share import LINK, SHARE, source

    if not KEYS.has_key(SHARE):
        return None
    path = source(KEYS.key(LINK), href)
    try:
        info = gdal.VSIStatL(path)
        handle = gdal.VSIFOpenL(path, "rb") if info is not None and 0 < info.size <= MAX_BYTES else None
        if handle is None:
            return None
        try:
            text = bytes(gdal.VSIFReadL(1, info.size, handle)).decode("utf-8")
        finally:
            gdal.VSIFCloseL(handle)
    except (RuntimeError, UnicodeDecodeError):
        return None
    _styles[href] = text
    return text


def renderer_from_style(text, made_for, column):
    """The renderer of a QGIS style, its rules pointed at `column`; None when it is not usable."""
    from qgis.core import QgsFeatureRenderer, QgsReadWriteContext
    from qgis.PyQt.QtXml import QDomDocument

    if not text or "<!ENTITY" in text or _OUTSIDE.search(text):
        return None
    if column != made_for:
        text = text.replace(f"&quot;{made_for}&quot;", f"&quot;{column}&quot;")
    doc = QDomDocument()
    doc.setContent(text)
    element = doc.elementsByTagName("renderer-v2").at(0).toElement()
    return None if element.isNull() else QgsFeatureRenderer.load(element, QgsReadWriteContext())


def renderer_from_colours(legend, column, layer, locale="en"):
    from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsSymbol
    from qgis.PyQt.QtGui import QColor

    field = layer.fields().field(column)
    categories = []
    for c in legend["colours"]:
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(QColor(c["colour"]))
        label = (c["da"] if (locale or "").startswith("da") else c["en"]) or c["da"] or c["en"]
        value = c["code"]
        if field.isNumeric() and re.fullmatch(r"-?\d+", value):
            value = int(value)
        categories.append(QgsRendererCategory(value, symbol, f"{c['code']} – {label}" if label != c["code"] else label))
    return QgsCategorizedSymbolRenderer(column, categories)


def geometry_name(layer):
    from qgis.core import Qgis

    return {Qgis.GeometryType.Polygon: "polygon", Qgis.GeometryType.Line: "line", Qgis.GeometryType.Point: "point"}.get(layer.geometryType())


def prepare(layer, dataset, service, wanted=None, locale="en"):
    """Fetch the style the layer will get (background thread)."""
    found = column_for(dataset, service)
    if found and geometry_name(layer):
        choice = _pick(found[0], layer, wanted, locale)
        if choice.href:
            fetch(choice.href)


def _pick(legend, layer, wanted, locale):
    offered = choices(legend, geometry_name(layer), locale)
    return next((c for c in offered if c.id == wanted or (c.href and wanted == "lang:" + c.lang)), offered[0])


def apply(layer, dataset, service, wanted=None, locale="en"):
    """Put the legend on the layer (main thread). Returns a line about what was done, or ''."""
    from .layers import PROPERTY_PREFIX

    found = column_for(dataset, service)
    if not found or geometry_name(layer) is None or layer.fields().indexOf(found[1]) < 0:
        return ""
    legend, column = found
    choice = _pick(legend, layer, wanted, locale)
    if choice.id == NONE:
        return ""
    said = f"{choice.title}: {legend['source']}"
    renderer = renderer_from_style(_styles.get(choice.href), legend["column"], column) if choice.href else None
    if renderer is None:
        renderer = renderer_from_colours(legend, column, layer, locale)
        said = f"colours of {legend['title']}" + (" (the style file could not be read from the data share)" if choice.href else "")
    layer.setRenderer(renderer)
    layer.setCustomProperty(PROPERTY_PREFIX + "legend", f"{choice.id if 'colours of' not in said else COLOURS}; {legend['page'].rsplit('/', 1)[-1]}; {legend['origin']}; {legend['licence']}")
    return said
