"""The SPHERE network as the plugin sees it.

Two published JSON files make up the catalogue:
  sphere-index.v1.json  spheres, twigs, leaves (with twig memberships) and threads
  services.v1.json      datasets per leaf and their checked OGC/download services

Both are fetched from a base URL (default: semanticgis.dk) or read from a local folder,
and cached so the plugin also works offline. This module has no QGIS imports so it can
be tested without QGIS.
"""

import json
import os
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://semanticgis.dk/assets/"
INDEX_FILE = "sphere-index.v1.json"
SERVICES_FILE = "services.v1.json"
MIN_INDEX_VERSION = (0, 4)
LOADABLE_TYPES = ("wfs", "wms", "wmts")
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

    @property
    def status(self):
        return self.check.get("status", "unchecked")

    @property
    def verified(self):
        return self.status == "ok"

    @property
    def loadable(self):
        return self.type in LOADABLE_TYPES and bool(self.layer_name)

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


@dataclass
class Dataset:
    id: str
    title: str
    page: str
    leaves: list
    services: list


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

    @property
    def title(self):
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

        self.datasets = {}
        for dataset_id, d in services.get("datasets", {}).items():
            self.datasets[dataset_id] = Dataset(
                id=dataset_id,
                title=d["title"],
                page=d.get("page", ""),
                leaves=d.get("leaves", []),
                services=[
                    Service(
                        id=s["id"],
                        type=s["type"],
                        url=s["url"],
                        layer=s.get("layer"),
                        title=s.get("title"),
                        auth=s.get("auth", "none"),
                        check=s.get("check", {}),
                    )
                    for s in d.get("services", [])
                ],
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
                twig = Twig(id=twig_id, sphere=sphere.id)
                twig.leaves = sorted(
                    (leaf for leaf in self.leaves.values() if twig_id in leaf.twigs), key=lambda l: l.title
                )
                sphere.twigs.append(twig)
                self.twigs[twig_id] = twig
            self.spheres.append(sphere)

    def search(self, text):
        """Leaves whose title or question contains every word of `text`."""
        words = text.lower().split()
        return [
            leaf
            for leaf in sorted(self.leaves.values(), key=lambda l: l.title)
            if all(w in f"{leaf.title} {leaf.question}".lower() for w in words)
        ]


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

    Returns (catalogue, from_cache).
    """
    texts = {}
    from_cache = False
    try:
        for name in (INDEX_FILE, SERVICES_FILE):
            texts[name] = _read(source, name, timeout)
    except (OSError, ValueError) as error:
        if not cache_dir or not all(os.path.exists(os.path.join(cache_dir, n)) for n in (INDEX_FILE, SERVICES_FILE)):
            raise CatalogueError(f"Could not read the catalogue from {source}: {error}") from error
        texts = {name: _read(cache_dir, name, timeout) for name in (INDEX_FILE, SERVICES_FILE)}
        from_cache = True

    catalogue = Catalogue(json.loads(texts[INDEX_FILE]), json.loads(texts[SERVICES_FILE]))

    if cache_dir and not from_cache:
        os.makedirs(cache_dir, exist_ok=True)
        for name, text in texts.items():
            with open(os.path.join(cache_dir, name), "w", encoding="utf-8") as f:
                f.write(text)
    return catalogue, from_cache
