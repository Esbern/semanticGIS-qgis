"""The SPHERE network as the plugin sees it.

Two published JSON files make up the catalogue:
  sphere-index.v1.json  spheres, twigs, leaves (with twig memberships) and threads
  services.v1.json      datasets per leaf and their checked OGC/download services

Both are fetched from a base URL (default: semanticgis.org) or read from a local folder,
and cached so the plugin also works offline. This module has no QGIS imports so it can
be tested without QGIS.
"""

import json
import os
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://semanticgis.org/Data/assets/"
INDEX_FILE = "sphere-index.v1.json"
SERVICES_FILE = "services.v1.json"
MIN_INDEX_VERSION = (0, 4)
LOADABLE_TYPES = ("wfs", "wms", "wmts", "xyz", "wcs")
ACRONYMS = {"ict", "crs"}


class CatalogueError(Exception):
    pass


@dataclass
class Service:
    id: str
    type: str
    url: str
    layer: str | None
    title: str | None
    auth: str
    check: dict
    priority: int = 9            # lower is preferred (portal ranking, see the Data Portals notes)
    portal: dict | None = None   # {"title", "page"} of the data portal serving it
    zmin: int | None = None      # XYZ tile services only
    zmax: int | None = None

    @property
    def status(self):
        return self.check.get("status", "unchecked")

    @property
    def verified(self):
        return self.status == "ok"

    @property
    def loadable(self):
        return self.type in LOADABLE_TYPES and (self.type == "xyz" or bool(self.layer_name))

    @property
    def layer_name(self):
        """Layer name as the server knows it; the harvested name is a fallback."""
        return self.check.get("resolved_layer") or self.layer

    @property
    def endpoint(self):
        return self.check.get("effective_url") or self.url

    @property
    def label(self):
        return self.check.get("layer_title") or self.title or self.layer_name or self.url


# How a service is named in a Danish attribution line ("…, WMS-tjeneste"); files say when they were fetched.
SERVICE_LABEL = {
    "wms": "WMS-tjeneste",
    "wmts": "WMTS-tjeneste",
    "wfs": "WFS-tjeneste",
    "wcs": "WCS-tjeneste",
    "xyz": "tile-tjeneste",
    "download": "hentet ‹dato›",
}


@dataclass
class Attribution:
    """The credit line to put on a map. `status` is verified (checked against the publisher's
    terms), default (standard wording, terms not checked) or unknown (publisher not recorded)."""

    text: str = ""
    status: str = "unknown"
    licence: str | None = None
    terms_url: str | None = None

    def line(self, service_type=None):
        """The line for one service type; {service} is the service, or 'hentet <date>' for files."""
        return self.text.replace("{service}", SERVICE_LABEL.get(service_type or "download", SERVICE_LABEL["download"]))


def make_attribution(a):
    if not a:
        return None
    if isinstance(a, str):   # services.v1.json before attribution records
        return Attribution(text=a, status="verified")
    return Attribution(text=a.get("text", ""), status=a.get("status", "unknown"),
                       licence=a.get("licence"), terms_url=a.get("terms_url"))


def make_service(s):
    return Service(
        id=s["id"],
        type=s["type"],
        url=s["url"],
        layer=s.get("layer"),
        title=s.get("title"),
        auth=s.get("auth", "none"),
        check=s.get("check", {}),
        priority=int(s.get("priority", 9)),
        portal=s.get("portal"),
        zmin=s.get("zmin"),
        zmax=s.get("zmax"),
    )


@dataclass
class Basemap:
    id: str
    title: str
    kind: str
    page: str
    services: list
    period: str | None = None
    provider: str | None = None
    attribution: Attribution | None = None
    licence: str | None = None
    terms_url: str | None = None
    collection_method: str | None = None
    leaf: str | None = None

    @property
    def preferred_service(self):
        ranked = sorted(self.services, key=lambda s: s.priority)
        return next((s for s in ranked if s.verified and s.loadable), None)


@dataclass
class Dataset:
    id: str
    title: str
    page: str
    leaves: list
    services: list
    owner: str | None = None
    attribution: Attribution | None = None

    @property
    def preferred_service(self):
        """The first verified, loadable service in priority order (the export sorts by priority)."""
        ranked = sorted(self.services, key=lambda s: s.priority)
        return next((s for s in ranked if s.verified and s.loadable), None)


@dataclass
class Leaf:
    id: str
    title: str
    question: str
    path: str
    twigs: list
    primary_lens: str | None
    datasets: list = field(default_factory=list)


@dataclass
class Twig:
    id: str
    sphere: str
    leaves: list = field(default_factory=list)
    path: str | None = None
    page_title: str | None = None
    draft: bool = False   # draft pages are not published on the site

    @property
    def title(self):
        if self.page_title:
            return self.page_title
        # "anthroposphere_resource_utilisation" -> "Resource Utilisation", "..._ict_flows" -> "ICT Flows"
        words = self.id.split("_", 1)[-1].split("_")
        return " ".join(w.upper() if w in ACRONYMS else w.capitalize() for w in words)


@dataclass
class Sphere:
    id: str
    title: str
    description: str
    path: str
    kind: str
    twigs: list = field(default_factory=list)


@dataclass
class ReferenceGeometry:
    scale: str
    url: str

    @property
    def denominator(self):
        """60000000 for "1:60 000 000"; 0 when the scale is not a ratio."""
        digits = "".join(ch for ch in self.scale.split(":")[-1] if ch.isdigit())
        return int(digits) if digits else 0


@dataclass
class ReferenceSource:
    """One ID scheme of a reference realisation, with its geometries per scale."""

    realisation: str
    title: str
    leaf: str | None
    unit: str
    id_scheme: str
    version: str | None
    crs: str | None
    geometries: list

    @property
    def label(self):
        version = f", {self.version}" if self.version else ""
        return f"{self.unit} ({self.id_scheme}{version})"

    @property
    def index_geometry(self):
        """The smallest-scale geometry: every scale shares the IDs, so it is the cheapest to read them from."""
        return max(self.geometries, key=lambda g: g.denominator)

    def geometries_by_detail(self):
        return sorted(self.geometries, key=lambda g: g.denominator)


def sort_realisations(rels):
    """Priority first (1 = preferred, unset last), then dataset name."""
    return sorted(rels, key=lambda r: (r.get("priority") or 10**6, str(r.get("dataset", r["id"])).lower()))


class Catalogue:
    def __init__(self, index, services):
        version = tuple(int(p) for p in str(index.get("version", "0.0")).split(".")[:2])
        if version < MIN_INDEX_VERSION:
            raise CatalogueError(
                f"The SPHERE index is version {index.get('version')}; this plugin needs "
                f"{'.'.join(map(str, MIN_INDEX_VERSION))} or later (twig memberships)."
            )
        self.version = index.get("version")
        self.services_checked = services.get("checked")
        self.has_services = bool(services)

        self.datasets = {}
        for dataset_id, d in services.get("datasets", {}).items():
            self.datasets[dataset_id] = Dataset(
                id=dataset_id,
                title=d["title"],
                page=d.get("page", ""),
                leaves=d.get("leaves", []),
                owner=d.get("owner"),
                services=[make_service(s) for s in d.get("services", [])],
                attribution=make_attribution(d.get("attribution")),
            )

        self.leaves = {}
        for entry in index["leaves"]:
            leaf = Leaf(
                id=entry["id"],
                title=entry["title"],
                question=entry.get("question", ""),
                path=entry.get("path", ""),
                twigs=entry.get("twig_membership", []),
                primary_lens=entry.get("primary_lens"),
            )
            leaf.datasets = [
                self.datasets[d] for d in services.get("leaves", {}).get(leaf.id, []) if d in self.datasets
            ]
            self.leaves[leaf.id] = leaf

        # Reference units that offer geometries: what tables can be joined to.
        self.references = []
        for rel in index.get("realisations", []):
            leaf = next((t[len("leaf/"):] for t in rel.get("tags", []) if t.startswith("leaf/")), None)
            for unit in rel.get("reference_units", []) or []:
                geometries = [
                    ReferenceGeometry(scale=str(g["scale"]), url=g["url"])
                    for g in unit.get("geometries", []) or []
                    if g.get("url")
                ]
                if geometries:
                    self.references.append(
                        ReferenceSource(
                            realisation=rel["id"],
                            title=rel.get("title", rel["id"]),
                            leaf=leaf,
                            unit=unit["unit"],
                            id_scheme=unit["id_scheme"],
                            version=str(unit["version"]) if unit.get("version") else None,
                            crs=unit.get("crs"),
                            geometries=geometries,
                        )
                    )

        self.basemaps = [
            Basemap(
                id=b["id"], title=b["title"], kind=b.get("kind", ""), page=b.get("page", ""),
                services=[make_service(s) for s in b.get("services", [])],
                period=b.get("period"), provider=b.get("provider"), attribution=make_attribution(b.get("attribution")),
                licence=b.get("licence"), terms_url=b.get("terms_url"),
                collection_method=b.get("collection_method"), leaf=b.get("leaf"),
            )
            for b in services.get("basemaps", [])
        ]

        # Datafordeleren's registers (its own data overview), as ordinary datasets so that ordering,
        # the preferred service and search work the same way. Their pages are vault notes.
        self.datafordeler = []
        for r_index, register in enumerate(services.get("datafordeler", [])):
            items = []
            for d_index, d in enumerate(register.get("datasets", [])):
                dataset = Dataset(
                    id=f"datafordeler:{r_index}:{d_index}", title=d["title"], page=d.get("page", ""), leaves=[],
                    services=[make_service(s) for s in d.get("services", [])], owner=register["title"],
                    attribution=make_attribution(d.get("attribution")),
                )
                self.datasets[dataset.id] = dataset
                items.append(dataset)
            self.datafordeler.append({"title": register["title"], "page": register.get("page"), "datasets": items})
        self.leaf_registers = services.get("leaf_registers", {})

        # Browsing structures for the other entry points.
        self.owners = services.get("owners", {})
        self.collections = services.get("collections", [])
        self.classical_themes = index.get("classical_themes", [])
        self.collection_methods = index.get("collection_methods", [])
        self.realisations = {r["id"]: r for r in index.get("realisations", [])}

        self.spheres = []
        self.twigs = {}
        for entry in index["spheres"]:
            sphere = Sphere(
                id=entry["id"],
                title=entry["title"],
                description=entry.get("description", ""),
                path=entry.get("path", ""),
                kind=entry.get("kind", "thematic"),
            )
            for twig_id in entry["subspheres"]:
                info = index.get("twigs", {}).get(twig_id, {})
                twig = Twig(
                    id=twig_id, sphere=sphere.id, path=info.get("path"), page_title=info.get("title"),
                    draft=bool(info.get("draft")),
                )
                twig.leaves = sorted(
                    (leaf for leaf in self.leaves.values() if twig_id in leaf.twigs), key=lambda l: l.title
                )
                sphere.twigs.append(twig)
                self.twigs[twig_id] = twig
            self.spheres.append(sphere)

    @property
    def thematic_spheres(self):
        return [s for s in self.spheres if s.kind != "reference"]

    @property
    def reference_framework(self):
        return next((s for s in self.spheres if s.kind == "reference"), None)

    def datasets_by_owner(self):
        """Datasets per owner folder (the Datasets by Owner structure of the site)."""
        groups = {}
        for dataset in self.datasets.values():
            parts = dataset.page.strip("/").split("/")
            if len(parts) > 2 and parts[0] == "Datasets by Owner":
                groups.setdefault(parts[1], []).append(dataset)
        return {owner: sorted(items, key=lambda d: d.title.lower()) for owner, items in groups.items()}

    def realisations_by_method(self):
        groups = {}
        for rel in self.realisations.values():
            groups.setdefault(rel.get("collection_method"), []).append(rel)
        return {method: sort_realisations(rels) for method, rels in groups.items()}

    def registers_for(self, leaf_id):
        """Datafordeleren registers that realise a leaf (realisations with datafordeler_register)."""
        titles = self.leaf_registers.get(leaf_id, [])
        return [r for r in self.datafordeler if r["title"] in titles]

    def references_for(self, leaf_id=None, realisation_id=None):
        return [
            r for r in self.references
            if (leaf_id is None or r.leaf == leaf_id) and (realisation_id is None or r.realisation == realisation_id)
        ]

    def search(self, text):
        """Leaves whose title or question contains every word of `text`."""
        words = text.lower().split()
        return [
            leaf
            for leaf in sorted(self.leaves.values(), key=lambda l: l.title)
            if all(w in f"{leaf.title} {leaf.question}".lower() for w in words)
        ]

    def search_datasets(self, text, limit=200):
        """Datasets whose title contains every word of `text`."""
        words = text.lower().split()
        hits = [d for d in self.datasets.values() if all(w in d.title.lower() for w in words)]
        return sorted(hits, key=lambda d: d.title.lower())[:limit]


def _read(source, name, timeout):
    """Read one catalogue file from a URL base or a local folder."""
    if source.startswith(("http://", "https://")):
        url = source.rstrip("/") + "/" + name
        request = urllib.request.Request(url, headers={"User-Agent": "semanticGIS-qgis"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    with open(os.path.join(source, name), encoding="utf-8") as f:
        return f.read()


def load_catalogue(source=DEFAULT_BASE_URL, cache_dir=None, timeout=30):
    """Load the catalogue from `source`, falling back to the cache when it is unreachable.

    The index is required; the services file is optional, so the network can still be
    browsed when services have not been published. Returns (catalogue, from_cache).
    """
    def read_all(where):
        texts = {INDEX_FILE: _read(where, INDEX_FILE, timeout)}
        try:
            texts[SERVICES_FILE] = _read(where, SERVICES_FILE, timeout)
        except (OSError, ValueError):
            texts[SERVICES_FILE] = None
        return texts

    from_cache = False
    try:
        texts = read_all(source)
    except (OSError, ValueError) as error:
        if not cache_dir or not os.path.exists(os.path.join(cache_dir, INDEX_FILE)):
            raise CatalogueError(f"Could not read the catalogue from {source}: {error}") from error
        texts = read_all(cache_dir)
        from_cache = True

    services_text = texts[SERVICES_FILE]
    catalogue = Catalogue(json.loads(texts[INDEX_FILE]), json.loads(services_text) if services_text else {})
    catalogue.has_services = services_text is not None

    if cache_dir and not from_cache:
        os.makedirs(cache_dir, exist_ok=True)
        for name, text in texts.items():
            if text is not None:
                with open(os.path.join(cache_dir, name), "w", encoding="utf-8") as f:
                    f.write(text)
    return catalogue, from_cache
