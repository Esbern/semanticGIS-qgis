"""QGIS layer metadata <-> STAC Collection, so extension datasets are described in QGIS's own
metadata editor (Layer Properties > Metadata).

  QGIS metadata                          STAC Collection
  title, abstract                        title, description
  keywords (vocabulary semanticgis:leaf) semanticgis:leaves (as page addresses on the site)
  other keywords                         keywords
  licences (first)                       license
  rights (first)                         semanticgis:attribution
  history                                semanticgis:history
  layer extent                           extent.spatial (WGS 84)
Everything else in the Collection (assets, providers, snapshot provenance) is kept as it is.
"""

import copy
import datetime
import hashlib
import re

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsLayerMetadata, QgsProject

from .extensions import SITE_LEAVES

LEAF_VOCABULARY = "semanticgis:leaf"
KEYWORD_VOCABULARY = "keywords"
STAC_EXTENSIONS = [
    "https://semanticgis.org/stac/v0.1/schema.json",
    "https://stac-extensions.github.io/file/v2.1.0/schema.json",
]
MEDIA_TYPES = {
    ".tif": "image/tiff; application=geotiff; profile=cloud-optimized",
    ".parquet": "application/vnd.apache.parquet",
}


def leaf_ids(metadata):
    return list(metadata.keywords().get(LEAF_VOCABULARY, []))


def set_leaf_ids(metadata, ids):
    keywords = dict(metadata.keywords())
    keywords.pop(LEAF_VOCABULARY, None)
    metadata.setKeywords(keywords)
    if ids:
        metadata.addKeywords(LEAF_VOCABULARY, list(ids))


def metadata_from_stac(dataset):
    """Layer metadata for an extension dataset, from its STAC Collection."""
    stac = dataset.stac or {}
    metadata = QgsLayerMetadata()
    metadata.setIdentifier(stac.get("id", ""))
    metadata.setTitle(stac.get("title", dataset.title))
    metadata.setAbstract(stac.get("description", ""))
    if stac.get("keywords"):
        metadata.addKeywords(KEYWORD_VOCABULARY, list(stac["keywords"]))
    set_leaf_ids(metadata, dataset.leaves)
    if stac.get("license"):
        metadata.setLicenses([stac["license"]])
    if stac.get("semanticgis:attribution"):
        metadata.setRights([stac["semanticgis:attribution"]])
    history = list(stac.get("semanticgis:history", []))
    snapshot = stac.get("semanticgis:snapshot_of")
    if snapshot and not any(line.startswith("Snapshot of") for line in history):
        history.insert(0, f"Snapshot of {snapshot.get('title', snapshot.get('service', ''))} taken {snapshot.get('taken', '')}")
    metadata.setHistory(history)
    return metadata


def wgs84_bbox(layer):
    if layer is None or layer.extent().isEmpty():
        return None
    transform = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
    box = transform.transformBoundingBox(layer.extent())
    return [round(box.xMinimum(), 5), round(box.yMinimum(), 5), round(box.xMaximum(), 5), round(box.yMaximum(), 5)]


def stac_from_metadata(metadata, layer, collection, leaves):
    """The Collection with the fields QGIS's metadata editor owns replaced by the layer's metadata."""
    stac = copy.deepcopy(collection)
    stac["title"] = metadata.title() or stac.get("title", "")
    stac["description"] = metadata.abstract() or stac.get("description", "")
    keywords = [k for vocabulary, words in metadata.keywords().items() if vocabulary != LEAF_VOCABULARY for k in words]
    stac["keywords"] = list(dict.fromkeys(keywords))
    ids = [i for i in leaf_ids(metadata) if i in leaves]
    stac["semanticgis:leaves"] = [SITE_LEAVES + leaves[i].path for i in ids]
    if metadata.licenses():
        stac["license"] = metadata.licenses()[0]
    if metadata.rights():
        stac["semanticgis:attribution"] = metadata.rights()[0]
    stac["semanticgis:history"] = [line for line in metadata.history() if line]
    bbox = wgs84_bbox(layer)
    if bbox:
        stac.setdefault("extent", {}).setdefault("spatial", {})["bbox"] = [bbox]
    links = [link for link in stac.get("links", []) if link.get("rel") != "about"]
    links += [{"rel": "about", "href": SITE_LEAVES + leaves[i].path, "type": "text/html",
               "title": f"SPHERE leaf: {leaves[i].title}"} for i in ids]
    stac["links"] = links
    return stac


def slug(text):
    text = text.lower().translate(str.maketrans({"æ": "ae", "ø": "oe", "å": "aa", "é": "e", "ü": "u", "ö": "oe", "ä": "ae"}))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:60] or "dataset"


def new_collection(collection_id, title, description, asset_url, file_path, extension_title, derived_from=None):
    """A STAC Collection for a newly published file (assets with size and checksum)."""
    with open(file_path, "rb") as f:
        data = f.read()
    suffix = "." + file_path.rsplit(".", 1)[-1].lower()
    today = datetime.date.today().isoformat()
    collection = {
        "type": "Collection",
        "stac_version": "1.1.0",
        "stac_extensions": STAC_EXTENSIONS,
        "id": collection_id,
        "title": title,
        "description": description or title,
        "license": "other",
        "keywords": [],
        "providers": [{"name": extension_title, "roles": ["producer", "host"]}],
        "extent": {"spatial": {"bbox": [[-180, -90, 180, 90]]},
                   "temporal": {"interval": [[f"{today}T00:00:00Z", f"{today}T00:00:00Z"]]}},
        "semanticgis:status": "ready",
        "links": [
            {"rel": "root", "href": "../../catalog.json", "type": "application/json"},
            {"rel": "parent", "href": "../../catalog.json", "type": "application/json"},
        ],
        "assets": {
            "data": {
                "href": asset_url,
                "type": MEDIA_TYPES.get(suffix, "application/octet-stream"),
                "title": title,
                "roles": ["data"],
                "file:size": len(data),
                "file:checksum": "1220" + hashlib.sha256(data).hexdigest(),   # multihash sha2-256
            }
        },
    }
    if derived_from is not None:
        collection["semanticgis:derived_from"] = [{"dataset": derived_from.id, "title": derived_from.title}]
        collection["links"].append({"rel": "derived_from", "href": derived_from.page, "title": derived_from.title})
    return collection
