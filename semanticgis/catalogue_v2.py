"""The v2 catalogue: leaves with several twigs, variables, versions, reviewed and unreviewed leaf links, share files.

  index.v2.json      leaves, variables, schemes
  catalogue.v2.json  datasets with leaf links, versions (variables, layers, column meanings) and files on the share

`as_v1` turns the two files into the structures Catalogue already reads (so browsing, search and joins keep working),
`extend` then hangs the rest on it. No QGIS imports.
"""

from dataclasses import dataclass, field

INDEX_FILE_V2 = "index.v2.json"
CATALOGUE_FILE_V2 = "catalogue.v2.json"
SHARE = "ruc-data-share"

# How sure it is that a dataset belongs to a leaf: mark and explanation in the panel.
LINK_NOTE = {
    "unreviewed": (" ⚑", "Nobody has checked yet that this dataset belongs to this leaf: the link was made automatically."),
    "from-guess": (" ?", "Rests on columns whose meaning is a guess, to be confirmed."),
}
LINK_ORDER = {"reviewed": 0, "from-variables": 1, "from-guess": 2, "unreviewed": 3}


@dataclass
class Variable:
    id: str
    title: str
    about: str = "world"          # world | record
    scale: str = ""
    value_type: str = ""
    reference: dict = field(default_factory=dict)
    leaf: str | None = None
    relations: list = field(default_factory=list)

    @property
    def page(self):
        return f"/Variables/{self.id}"

    @property
    def tooltip(self):
        ref = f"{self.reference.get('kind', '')}: {self.reference.get('value', '')}".strip(": ")
        lines = [f"{'A property of the record' if self.about == 'record' else 'A property of the world'} · scale: {self.scale or '—'}", ref]
        for r in self.relations:
            to = r.get("to") or r.get("of") or ""
            if to and r.get("type") in ("convertible", "related", "part-of", "aggregates", "derived-from", "facet-of"):
                lines.append(f"{r['type']}: {', '.join(to) if isinstance(to, list) else to}" + (f" ({r['rule']})" if r.get("rule") else ""))
        return "\n".join(x for x in lines if x)


@dataclass
class Version:
    id: str
    label: str
    page: str
    variables: list
    meanings: list


def as_v1(index, services, index2, catalogue2):
    """(index, services) in the v1 structures, with the v2 leaves and datasets."""
    leaves = []
    for leaf_id, l in index2["leaves"].items():
        leaves.append({"id": leaf_id, "title": l["title"], "question": (l.get("question") or "").strip(), "path": f"/Leaves/{leaf_id}",
                       "twig_membership": l.get("twig_membership") or [], "primary_lens": l.get("primary_lens")})
    datasets, by_leaf = {}, {}
    for dataset_id, d in catalogue2["datasets"].items():
        if d.get("part_of_record") and not d["services"]:
            continue
        entry = {k: d.get(k) for k in ("title", "page", "owner", "attribution", "realisation", "subsets", "derivation")}
        if not entry["attribution"] and d.get("share"):
            entry["attribution"] = {"text": d["share"][-1].get("attribution") or "", "status": "default"}
        entry.update(leaves=[l["leaf"] for l in d["leaf_links"]], services=list(d["services"]))
        have = {(s["url"], s.get("layer")) for s in entry["services"]}
        for v in d["versions"]:                    # services the version knows and the live catalogue does not
            for m in v["meanings"]:
                if m["protocol"] == "wfs" and m["state"] == "active" and "<" not in m["url"] and (m["url"], m["layer"]) not in have:
                    entry["services"].append({"id": f"wfs|{m['url']}|{m['layer']}", "type": "wfs", "url": m["url"], "layer": m["layer"], "title": m["layer"],
                                              "auth": "none", "access": m.get("access"), "priority": 3, "check": {"status": "ok", "checked": m.get("checked")}})
                    have.add((m["url"], m["layer"]))
        datasets[dataset_id] = entry
        for l in d["leaf_links"]:
            by_leaf.setdefault(l["leaf"], []).append((LINK_ORDER.get(l["status"], 9), d["title"].lower(), dataset_id))
    sphere = {k: index2[k] for k in ("spheres", "twigs", "classical_themes") if k in index2}   # the v2 twigs
    owners = {**services.get("owners", {}), **{k: o.get("name") or o["title"] for k, o in index2.get("organisations", {}).items()}}
    return ({**index, **sphere, "leaves": leaves},
            {**services, "owners": owners, "datasets": datasets, "leaves": {k: [x[2] for x in sorted(v)] for k, v in by_leaf.items()}})


def extend(catalogue, index2, catalogue2, make_service):
    """Variables on the catalogue; versions, share files and link status on its datasets and leaves."""
    catalogue.variables = {
        k: Variable(k, v["title"], v.get("about", "world"), v.get("scale") or "", v.get("value_type") or "", v.get("reference") or {},
                    v.get("leaf"), v.get("relations") or [])
        for k, v in index2["variables"].items()}
    catalogue.organisations = index2.get("organisations", {})
    catalogue.registers = index2.get("registers", {})      # initiative: its schema and its datasets, one per class
    catalogue.flat = bool(catalogue.registers)
    catalogue.redirects = index2.get("redirects", {})
    catalogue.areas = index2.get("areas", {})              # published on the site; the list in the panel's heading
    catalogue.legends = index2.get("legends", {})
    catalogue.access_paths = index2.get("access_paths", {})
    catalogue.leaf_variables = {}
    for leaf_id, l in index2["leaves"].items():
        catalogue.leaves[leaf_id].headings = l.get("headings") or {}
    for k, v in index2["variables"].items():
        for leaf in [v.get("leaf")] + list(v.get("also_leaves") or []):
            if leaf in catalogue.leaves:
                catalogue.leaf_variables.setdefault(leaf, []).append(catalogue.variables[k])
    for items in catalogue.leaf_variables.values():
        items.sort(key=lambda v: v.title.lower())
    for dataset_id, d in catalogue2["datasets"].items():
        dataset = catalogue.datasets.get(dataset_id)
        if dataset is None:
            continue
        dataset.link_status = {l["leaf"]: l["status"] for l in d["leaf_links"]}
        dataset.versions = [Version(v["id"], v.get("label") or v["id"], v["page"], v["variables"], v["meanings"]) for v in d["versions"]]
        # a series too long for the tree (a file per day for years) is fetched by one of this plugin's own tools
        dataset.scope = (d.get("scope") or {}).get("area")
        useful = (d.get("quality") or {}).get("useful_from")
        dataset.useful_from = max(useful) if isinstance(useful, list) and useful else useful   # 1:n; where it varies (towns, countryside), the coarsest
        dataset.tools = [t for t in d.get("tools") or [] if str(t.get("algorithm", "")).startswith("semanticgis:")]
        dataset.share = []
        for s in d.get("share") or []:
            files = []
            for f in s["files"]:
                service = make_service({"id": f"file|{f['href']}", "type": "file", "url": f["href"], "layer": f["name"], "title": f["name"],
                                        "access": SHARE, "check": {"status": "ok"}, "priority": 8})
                service.kind, service.description = f["kind"], f.get("description") or ""
                service.unit, service.rows = f.get("unit"), f.get("rows")
                files.append(service)
            dataset.share.append({"snapshot": s["snapshot"], "licence": s.get("licence") or "", "files": files})


def around(areas, area):
    """The areas that hold this one, from the nearest to the largest."""
    out = []
    while area in areas and areas[area].get("parent"):
        area = areas[area]["parent"]
        out.append(area)
    return out


KINDS = (("global", "Global"), ("regional", "Regional"), ("national", "National"), ("local", "Local"), ("project", "Project"))


def _size(b):
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def canvas_home(areas, box):
    """The smallest area one of whose search boxes holds the whole box: where a map of that box is. World when none does."""
    best, size = None, None
    for key, a in areas.items():
        for b in a.get("boxes") or []:
            if b[0] <= box[0] and b[1] <= box[1] and box[2] <= b[2] and box[3] <= b[3] and (size is None or _size(b) < size):
                best, size = key, _size(b)
    return best or next((k for k, a in areas.items() if not a.get("parent")), None)


SCALES = (1000, 10000, 25000, 50000, 100000, 250000, 1000000, 10000000)   # the choices in the panel: 1:n


def concerns(areas, scope, chosen, useful=None, scale=None):
    """Is a dataset about `scope`, useful from 1:`useful`, wanted by someone who maps the area `chosen` at about 1:`scale`?
    Only data that cover the whole chosen area: data about it, or about a larger area that holds it (OpenStreetMap is about the world) –
    not data about a place inside it. Hidden too: data whose errors show at that scale. Data whose area or useful scale is not known are shown."""
    if useful and scale and useful > scale:
        return False
    if not chosen or chosen not in areas or not scope or scope not in areas:
        return True
    return scope == chosen or scope in around(areas, chosen)


def columns_of(dataset, service):
    """{column: variable id} for a service or file of a dataset, from its versions; {} when none is recorded."""
    for version in getattr(dataset, "versions", []):
        for m in version.meanings:
            if service.type == "wfs" and m["protocol"] == "wfs" and m["url"] == service.url and m["layer"] in (service.layer, service.layer_name):
                return m["columns"]
            if service.type == "file" and m["protocol"] == "file" and f"/{m['snapshot']}/" in service.url and m["file"] in ("*", service.layer):
                return m["columns"]
    return {}
