"""Snapshot series: datasets published as one layer per year (Marker_2008 … Marker_2026).

Many registers are not kept as one layer with dates but as a new snapshot each year, as a separate WFS layer
or even a separate dataset ("… 2024", "… 2025"). This module finds such series in the catalogue: verified WFS
layers whose names differ only by a year, and datasets whose titles differ only by a year. No QGIS imports, so
it can be tested without QGIS.
"""
import re
from dataclasses import dataclass, field

YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20\d\d)(?!\d)")


@dataclass
class Snapshot:
    year: int
    service: object      # catalogue.Service (a verified WFS layer)
    dataset: object      # catalogue.Dataset it belongs to


@dataclass
class Series:
    title: str
    snapshots: dict = field(default_factory=dict)   # year -> Snapshot

    @property
    def years(self):
        return sorted(self.snapshots)

    @property
    def label(self):
        years = self.years
        return f"{self.title} ({years[0]}–{years[-1]}, {len(years)} years)"

    def between(self, first, last):
        return [self.snapshots[y] for y in self.years if first <= y <= last]


def _without_year(text):
    return re.sub(r"\s+", " ", YEAR.sub("", text or "")).strip(" -_:,.")


def _plain(text):
    """Lower case ASCII letters and digits only, for comparing a title with a layer name."""
    text = (text or "").lower()
    for a, b in (("æ", "ae"), ("ø", "oe"), ("å", "aa"), ("é", "e"), ("ü", "u"), ("ö", "oe"), ("ä", "ae")):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]", "", text)


def _layer_title(pattern):
    """Marker:Marker_{year} -> 'Marker'; Vandprojekter:Minivaadomraadekort_foer_hoering_{year} -> 'Minivaadomraadekort foer hoering'."""
    name = pattern.split(":")[-1].replace("{year}", "")
    return re.sub(r"[_\s]+", " ", name).strip(" -_") or pattern


def find_series(datasets, min_years=2):
    """Series from catalogue datasets (an iterable of catalogue.Dataset), sorted by title."""
    by_pattern = {}   # (endpoint, pattern) -> {"snapshots": {year: Snapshot}, "datasets": [...]}
    by_title = {}     # plain title -> {year: Snapshot}, for one dataset per year
    titles = {}

    for dataset in datasets:
        layers = [s for s in dataset.services if s.type == "wfs" and s.verified and s.layer_name]
        title_year = YEAR.search(dataset.title or "")
        for service in layers:
            name = service.layer_name
            match = YEAR.search(name.split(":")[-1])
            if match:
                key = (service.endpoint.split("?")[0].lower(), YEAR.sub("{year}", name))
                entry = by_pattern.setdefault(key, {"snapshots": {}, "datasets": []})
                # one layer per year: keep the first (the export sorts services by priority)
                entry["snapshots"].setdefault(int(match.group(0)), Snapshot(int(match.group(0)), service, dataset))
                if dataset not in entry["datasets"]:
                    entry["datasets"].append(dataset)
            elif title_year and len(layers) == 1:
                base = _without_year(dataset.title)
                titles.setdefault(_plain(base), base)
                year = int(title_year.group(0))
                by_title.setdefault(_plain(base), {}).setdefault(year, Snapshot(year, service, dataset))

    series = {}
    for (_, pattern), entry in sorted(by_pattern.items()):
        # name it after the dataset whose title matches the layer name; several datasets may list the layers
        wanted = _plain(_layer_title(pattern))
        named = [d for d in entry["datasets"] if _plain(_without_year(d.title)) == wanted] or \
                [d for d in entry["datasets"] if wanted and wanted in _plain(_without_year(d.title))] or \
                [d for d in entry["datasets"] if _plain(_without_year(d.title)) and _plain(_without_year(d.title)) in wanted]
        bases = {_plain(_without_year(d.title)) for d in entry["datasets"]}
        if not named and len(bases) == 1 and all(YEAR.search(d.title or "") for d in entry["datasets"]):
            named = entry["datasets"][:1]   # one dataset per year, all with the same title: use that title
        title = _without_year(named[0].title) if named else _layer_title(pattern)
        if named:
            entry["snapshots"] = {y: Snapshot(y, s.service, named[0]) for y, s in entry["snapshots"].items()}
        key = _plain(title)
        if key in series:   # the same series on another service: add the years it lacks
            for year, snapshot in entry["snapshots"].items():
                series[key].snapshots.setdefault(year, snapshot)
        else:
            series[key] = Series(title, dict(entry["snapshots"]))
    for key, snapshots in by_title.items():
        target = series.setdefault(key, Series(titles[key]))
        for year, snapshot in snapshots.items():
            target.snapshots.setdefault(year, snapshot)
    result = [s for s in series.values() if len(s.snapshots) >= min_years]
    return sorted(result, key=lambda s: s.title.lower())
