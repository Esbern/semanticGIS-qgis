"""Extensions: private datasets of research groups, kept in GitLab projects and linked to SPHERE leaves.

A GitLab project marked with the topic `semanticgis` holds:
  semanticgis.json                        manifest (id, title, the STAC catalog's path)
  catalog.json                            STAC Catalog whose `child` links are the datasets
  collections/<id>/collection.json        STAC Collection per dataset, with semanticgis:* fields
The data files can live anywhere the assets point to, e.g. the project's package registry.

The plugin finds the projects the user is a member of on each extension server, reads them
with the user's GitLab token (an access profile sent as the PRIVATE-TOKEN header) and adds their
datasets to the leaves they name. This module has no QGIS imports: `get` is a function
url -> bytes supplied by the caller.
"""

import base64
import json
import posixpath
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

from .catalogue import AccessProfile, Attribution, Dataset, Service

TOPIC = "semanticgis"
SITE_LEAVES = "https://semanticgis.org/Data"   # leaf URIs are page addresses on the public site
COG_TYPES = ("image/tiff",)
VECTOR_TYPES = ("application/vnd.apache.parquet", "application/x-parquet", "application/flatgeobuf",
                "application/geopackage+sqlite3", "application/geo+json")


@dataclass
class Extension:
    server: str
    project_id: int
    path: str            # group/project on the server
    title: str
    web_url: str
    branch: str
    datasets: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    catalog_path: str = "catalog.json"
    catalog: dict | None = None
    catalog_commit: str | None = None   # version of catalog.json when read (conflict check on save)


def server_host(server):
    return urlsplit(server if "://" in server else "https://" + server).hostname or server


def server_profile(server):
    """The access profile of an extension server: the user's GitLab token, sent as a header."""
    host = server_host(server)
    return AccessProfile(
        id=f"gitlab-{host}", title=f"GitLab token ({host})", param="", hosts=[host], header="PRIVATE-TOKEN",
        signup_url=f"https://{host}/-/user_settings/personal_access_tokens",
    )


def _api(server):
    return f"https://{server_host(server)}/api/v4"


def discover(server, get):
    """Projects on a GitLab server that the user is a member of and that carry the topic."""
    url = f"{_api(server)}/projects?membership=true&topic={TOPIC}&simple=true&per_page=100"
    return [
        Extension(server=server_host(server), project_id=p["id"], path=p["path_with_namespace"], title=p.get("name") or p["path"],
                  web_url=p["web_url"], branch=p.get("default_branch") or "main")
        for p in json.loads(get(url))
    ]


def project_api(extension):
    return f"{_api(extension.server)}/projects/{extension.project_id}"


def read_file(extension, path, get):
    """(text, last_commit_id) of a file in the project: the commit id is the version a later save
    must start from, so GitLab can refuse a save over someone else's newer change."""
    url = f"{project_api(extension)}/repository/files/{quote(path, safe='')}?ref={quote(extension.branch, safe='')}"
    data = json.loads(get(url))
    return base64.b64decode(data["content"]).decode("utf-8"), data["last_commit_id"]


def leaf_id(reference, leaves):
    """The leaf a STAC reference names: a page address on the site, a /Leaves/… path or a bare id."""
    reference = str(reference).strip().rstrip("/")
    if reference in leaves:
        return reference
    path = reference[len(SITE_LEAVES):] if reference.startswith(SITE_LEAVES) else reference
    return next((leaf.id for leaf in leaves.values() if leaf.path.rstrip("/") == path), None)


def _service(extension, asset_key, asset, base):
    media = (asset.get("type") or "").split(";")[0].strip().lower()
    href = asset["href"] if "://" in asset["href"] else posixpath.normpath(posixpath.join(base, asset["href"]))
    kind = "cog" if media in COG_TYPES else "ogr" if media in VECTOR_TYPES else "download"
    return Service(
        id=f"{kind}|{href}|", type=kind, url=href, layer=None, title=asset.get("title") or asset_key,
        auth="none", check={"status": "ok", "note": "extension"}, priority=0,
        # files on the extension's own server need the user's GitLab token
        access=server_profile(extension.server).id if urlsplit(href).hostname == extension.server else None,
    )


def dataset_from_collection(extension, path, collection, leaves):
    """A Dataset for a STAC Collection, with its leaves resolved; problems are returned as text."""
    problems = []
    leaf_ids = []
    for reference in collection.get("semanticgis:leaves", []):
        found = leaf_id(reference, leaves)
        if found:
            leaf_ids.append(found)
        else:
            problems.append(f"{collection.get('id')}: unknown leaf {reference}")
    base = posixpath.dirname(path)
    services = [_service(extension, k, a, base) for k, a in collection.get("assets", {}).items() if "data" in a.get("roles", ["data"])]
    attribution = collection.get("semanticgis:attribution")
    dataset = Dataset(
        id=f"ext:{extension.server}:{extension.path}:{collection['id']}",
        title=collection.get("title") or collection["id"],
        page=f"{extension.web_url}/-/blob/{extension.branch}/{path}",
        leaves=leaf_ids,
        services=services,
        owner=extension.title,
        attribution=Attribution(text=attribution, status="project") if attribution else None,
        extension=extension,
        stac=collection,
        stac_path=path,
    )
    return dataset, problems


def load_extension(extension, get, leaves):
    """Read the manifest, the catalog and its collections into extension.datasets."""
    manifest = json.loads(read_file(extension, "semanticgis.json", get)[0])
    extension.title = manifest.get("title") or extension.title
    catalog_path = manifest.get("catalog", "catalog.json")
    text, extension.catalog_commit = read_file(extension, catalog_path, get)
    catalog = json.loads(text)
    extension.catalog_path, extension.catalog = catalog_path, catalog
    for link in catalog.get("links", []):
        if link.get("rel") != "child":
            continue
        path = posixpath.normpath(posixpath.join(posixpath.dirname(catalog_path), link["href"]))
        try:
            text, commit = read_file(extension, path, get)
            collection = json.loads(text)
        except (OSError, ValueError, KeyError) as error:
            extension.problems.append(f"{path}: {error}")
            continue
        dataset, problems = dataset_from_collection(extension, path, collection, leaves)
        dataset.stac_commit = commit
        extension.datasets.append(dataset)
        extension.problems.extend(problems)
    return extension


def load_extensions(servers, get, leaves):
    """All extensions on the servers; a server that fails is reported, not fatal."""
    extensions, problems = [], []
    for server in servers:
        try:
            found = discover(server, get)
        except (OSError, ValueError) as error:
            problems.append(f"{server}: {error}")
            continue
        for extension in found:
            try:
                extensions.append(load_extension(extension, get, leaves))
            except (OSError, ValueError) as error:
                problems.append(f"{extension.path}: {error}")
    return extensions, problems


def merge(catalogue, extensions):
    """Add the extensions' datasets to the catalogue and to the leaves they realise."""
    catalogue.extensions = extensions
    for extension in extensions:
        for dataset in extension.datasets:
            catalogue.datasets[dataset.id] = dataset
            for leaf_ref in dataset.leaves:
                leaf = catalogue.leaves.get(leaf_ref)
                if leaf is not None and dataset not in leaf.datasets:
                    leaf.datasets.append(dataset)
