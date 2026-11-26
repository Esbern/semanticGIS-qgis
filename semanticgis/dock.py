"""Dock panel: browse the SPHERE network and add layers from its services."""

import os

from qgis.core import Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsMessageLog, QgsProject, QgsSettings, QgsTask
from qgis.PyQt.QtCore import QCoreApplication, QUrl, Qt
from qgis.PyQt.QtGui import QDesktopServices, QStandardItem, QStandardItemModel
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .access import KEYS
from .attribution import LAYOUT_EXPRESSION, STATUS_NOTE, copy_layer_attributions
from . import legends
from .catalogue_v2 import KINDS, LINK_NOTE, SCALES, SHARE, canvas_home, concerns
from .catalogue import DEFAULT_BASE_URL, CatalogueError, load_catalogue
from .join_dialog import JoinDialog
from .layers import (
    PREFERRED_CRS,
    VARIABLES,
    crs_definition,
    rebind_crs,
    build_basemap_layer,
    place_basemap_layer,
    build_layer,
    build_reference_layer,
    place_layer,
    place_reference_layer,
)
from .settings import DEFAULT_SITE_URL, SETTINGS_PREFIX, SettingsDialog

ROLE = Qt.ItemDataRole.UserRole + 1


def derivation_text(derivation):
    """One line on how a derived dataset was made, for tooltips."""
    source = derivation.get("from_title") or derivation.get("from", "?")
    parts = [f"Derived from {source}: {derivation.get('criteria', '')}"]
    if derivation.get("legal_basis"):
        parts.append(f"legal basis: {derivation['legal_basis']}")
    if derivation.get("as_of"):
        parts.append(f"as of {derivation['as_of']}")
    return "; ".join(parts)


def plugin_version():
    """The plugin's own version, from metadata.txt."""
    try:
        with open(os.path.join(os.path.dirname(__file__), "metadata.txt"), encoding="utf8") as metadata:
            return next((line.split("=", 1)[1].strip() for line in metadata if line.startswith("version=")), "")
    except OSError:
        return ""
STATUS_TEXT = {
    "ok": "",
    "needs-auth": "not checked: needs a key",
    "placeholder": "needs an API key: serves a placeholder image",
    "no-geometry": "no geometry in QGIS: opens as a table",
    "layer-not-found": "layer not found",
    "no-layer": "no layer name",
    "unreachable": "unreachable",
    "skipped": "not checked",
    "unchecked": "not checked",
}


def settings_value(key, default=""):
    return QgsSettings().value(SETTINGS_PREFIX + key, default)


class SemanticGisDock(QDockWidget):
    def __init__(self, iface, parent=None):
        super().__init__("SemanticGIS v2 (test)", parent)
        self.iface = iface
        self.catalogue = None
        self._layer_tasks = set()  # keep Python references until tasks finish
        self.setObjectName("SemanticGisDock")

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(4, 4, 4, 4)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search leaves by title or question…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.rebuild)
        top.addWidget(self.search)
        refresh = QToolButton()
        refresh.setIcon(QgsApplication.getThemeIcon("mActionRefresh.svg"))
        refresh.setToolTip("Reload the catalogue")
        refresh.clicked.connect(self.reload)
        top.addWidget(refresh)
        options = QToolButton()
        options.setIcon(QgsApplication.getThemeIcon("mActionOptions.svg"))
        options.setToolTip("Settings")
        options.clicked.connect(self.open_settings)
        top.addWidget(options)
        layout.addLayout(top)

        areas = QHBoxLayout()
        self.area, self.scale = QComboBox(), QComboBox()
        self.area.setToolTip("Show the data that cover this whole area: data about it and about the larger areas that hold it. Map canvas: the smallest area that holds the map")
        self.scale.setToolTip("The scale you map at: datasets whose errors show at this scale are hidden. Where the useful scale is not known, the dataset is shown")
        self.scale.addItem("any scale", 0)
        self.scale.addItem("as the map", self.MAP_SCALE)
        for n in SCALES:
            self.scale.addItem(f"1:{n:,}", n)
        self.scale.setCurrentIndex(max(0, self.scale.findData(int(settings_value("v2_scale", 0) or 0))))
        areas.addWidget(QLabel("Area"))
        areas.addWidget(self.area, 1)
        areas.addWidget(QLabel("at about"))
        areas.addWidget(self.scale, 1)
        self.read_map = QToolButton()
        self.read_map.setIcon(QgsApplication.getThemeIcon("mActionRefresh.svg"))
        self.read_map.setToolTip("Read the map again (its area and scale); the list does not follow the map by itself")
        self.read_map.clicked.connect(self.rebuild)
        areas.addWidget(self.read_map)
        self.area_row = QWidget()
        areas.setContentsMargins(0, 0, 0, 0)
        self.area_row.setLayout(areas)
        self.area_row.setVisible(False)                     # until a catalogue with areas is read
        layout.addWidget(self.area_row)
        self.area.currentIndexChanged.connect(self._area_chosen)
        self.scale.currentIndexChanged.connect(self._scale_chosen)
        self._area_now = None
        self._show_read_map()

        self.verified_only = QCheckBox("Verified services only")
        self.verified_only.setChecked(True)
        self.verified_only.toggled.connect(self.rebuild)
        self.only_in_view = QCheckBox("WFS: fetch only features in the map view")
        self.only_in_view.setChecked(True)
        layout.addWidget(self.verified_only)
        layout.addWidget(self.only_in_view)
        self.join_button = QPushButton("Join a table to reference units…")
        self.join_button.setToolTip("Give a statistics table geometry by matching its unit IDs to NUTS, LAU and other reference units")
        self.join_button.setEnabled(False)
        self.join_button.clicked.connect(self.open_join)
        layout.addWidget(self.join_button)
        self.credits_button = QPushButton("Copy attributions for the map")
        self.credits_button.setToolTip(
            "Copy the attribution lines of the project's layers, to paste into a label in the print layout.\n"
            f"A label with the expression {LAYOUT_EXPRESSION} keeps itself up to date with the layers of 'Map 1'."
        )
        self.credits_button.clicked.connect(self.copy_map_attributions)
        layout.addWidget(self.credits_button)

        self.model = QStandardItemModel()
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setModel(self.model)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        self.tree.doubleClicked.connect(self.activate)
        layout.addWidget(self.tree)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.setWidget(body)

    # -- catalogue -----------------------------------------------------------------

    def reload(self):
        source = settings_value("v2_catalogue_source", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
        cache_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "semanticgis", "cache-v2")
        self.status.setText(f"Loading catalogue from {source}…")

        def task_function(task):
            return load_catalogue(source, cache_dir)

        def on_finished(exception, result=None):
            if exception is not None:
                message = str(exception) if isinstance(exception, CatalogueError) else repr(exception)
                self.status.setText(message)
                self.iface.messageBar().pushMessage("SemanticGIS", message, Qgis.MessageLevel.Critical)
                return
            self.catalogue, from_cache = result
            legends.LEGENDS.clear()
            legends.LEGENDS.update(getattr(self.catalogue, "legends", {}))
            KEYS.set_profiles(self.catalogue.access_profiles)
            VARIABLES.clear()
            VARIABLES.update(self.catalogue.variables)
            self.join_button.setEnabled(bool(self.catalogue.references))
            checked = self.catalogue.services_checked or "never"
            services = f"services checked {checked}" if self.catalogue.has_services else "no services published yet"
            self.status.setText(
                f"SemanticGIS {plugin_version()} · {len(self.catalogue.leaves)} leaves · "
                f"{str(len(self.catalogue.variables)) + ' variables · ' if self.catalogue.is_v2 else 'v1 catalogue · '}"
                f"{services}" + (" · offline copy" if from_cache else "")
            )
            self.status.setToolTip(f"SemanticGIS plugin {plugin_version()} · SPHERE index v{self.catalogue.version} "
                                   f"· catalogue from {source}")
            self.rebuild()

        self._task = QgsTask.fromFunction("Load SemanticGIS catalogue", task_function, on_finished=on_finished)
        QgsApplication.taskManager().addTask(self._task)

    def open_join(self):
        if self.catalogue is not None:
            JoinDialog(self.iface, self.catalogue, self).show()

    def open_settings(self):
        profiles = self.catalogue.access_profiles if self.catalogue else KEYS.profiles
        if SettingsDialog(profiles, self).exec():
            self.reload()

    # -- tree ----------------------------------------------------------------------

    def _item(self, text, data, tooltip=None, bold=False):
        item = QStandardItem(text)
        item.setData(data, ROLE)
        if tooltip:
            item.setToolTip(tooltip)
        if bold:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        return item

    def _visible_services(self, dataset):
        return [
            s
            for s in dataset.services
            if self._usable(s) or s.type == "download" or not self.verified_only.isChecked()
        ]

    # Status of a service the nightly check could not verify because it had no key for it.
    UNCHECKED_FOR_KEY = ("needs-auth", "placeholder")

    @classmethod
    def _usable(cls, service):
        """Verified and loadable, or loadable and only unverified for lack of a key the user has."""
        if not service.loadable:
            return False
        return service.verified or (service.status in cls.UNCHECKED_FOR_KEY and bool(service.access)
                                    and KEYS.has_key(service.access))

    def _preferred(self, item):
        """The first usable service of a dataset or basemap in priority order."""
        return next((s for s in sorted(item.services, key=lambda s: s.priority) if self._usable(s)), None)

    def _key_mark(self, service):
        """'🔒 ' for a service that needs a key the user has not set, '🔑 ' when it is set, and a tooltip line."""
        profile = KEYS.profile_of(service)
        if profile is None:
            return "", ""
        if KEYS.has_key(profile.id):
            return "🔑 ", f"Uses your {profile.title}."
        how = self._how_to_get(profile.id)
        return "🔒 ", f"Needs your own {profile.title}: right-click to set it" + (f" (get one at {profile.signup_url})" if profile.signup_url else "") + "." + (f"\n{how}" if how else "")

    def _how_to_get(self, path_id):
        """Who can use a published access path, what it costs and how to get in; '' when the catalogue does not say."""
        path = getattr(self.catalogue, "access_paths", {}).get(path_id)
        if not path:
            return ""
        return f"For: {path['who']} · Cost: {path['cost']}\nHow to get access: {path['how_to_get']}."

    def _service_items(self, parent, dataset, leaf_id=None):
        services = sorted(self._visible_services(dataset), key=lambda s: s.priority)
        preferred = self._preferred(dataset)
        for service in services:
            note = STATUS_TEXT.get(service.status, service.status)
            portal = f"{service.portal['title']} · " if service.portal else ""
            star = " ★" if service is preferred else ""
            mark, key_tip = self._key_mark(service)
            text = f"{mark}{service.type.upper()} · {portal}{service.label}{star}" + (f"  ({note})" if note else "")
            alternative = ("Same data as a layer QGIS opens without geometry; use this one instead."
                           if service.alternative_for else "")
            tip = "\n".join(filter(None, [service.endpoint, f"layer: {service.layer_name or '—'}", key_tip, alternative]))
            parent.appendRow(self._item(text, ("service", dataset.id, service.id, leaf_id), tip))
        return bool(services)

    def _dataset_item(self, dataset, leaf_id=None):
        if not self._in_area(dataset):
            return None
        preferred = self._preferred(dataset)
        tip = dataset.page + (f"\nDouble-click adds the preferred service ({preferred.type.upper()}"
                              f"{' · ' + preferred.portal['title'] if preferred.portal else ''})" if preferred else "")
        if dataset.derivation:
            tip += "\n" + derivation_text(dataset.derivation)
        item = self._item(dataset.title, ("dataset", dataset.id, leaf_id), tip)
        services = self._service_items(item, dataset, leaf_id)
        self._share_items(item, dataset, leaf_id)
        for number, tool in enumerate(getattr(dataset, "tools", [])):
            item.appendRow(self._item(f"⚙ {tool['title']}", ("tool", dataset.id, number), tool.get("description", "") + "\nDouble-click opens the tool."))
        if not services and not item.hasChildren():
            return None
        self._subset_items(item, dataset, leaf_id)
        self._version_items(item, dataset)
        return item

    def _share_items(self, parent, dataset, leaf_id=None):
        """Files on the data share, one group per snapshot (a dataset can have 80 of them)."""
        for s_index, snapshot in enumerate(dataset.share):
            mark = "🔑 " if KEYS.has_key(SHARE) else "🔒 "
            tip = f"Licence: {snapshot['licence']}" + ("\nNot an open licence: read the terms in the snapshot's README before use, and pass them on."
                                                      if "leveringsbetingelser" in snapshot["licence"].lower() else "")
            how = "" if KEYS.has_key(SHARE) else self._how_to_get(SHARE)
            tip += f"\n{how}" if how else ""
            group = self._item(f"{mark}RUC data share, {snapshot['snapshot']} ({len(snapshot['files'])})", ("share", dataset.id, s_index, leaf_id), tip)
            for f_index, f in enumerate(snapshot["files"]):
                rows = f" · {f.rows:,} rows" if f.rows else ""
                group.appendRow(self._item(f"{f.layer}{rows}", ("sharefile", dataset.id, s_index, f_index, leaf_id),
                                           "\n".join(filter(None, [f.description, f"unit: {f.unit}" if f.unit else "", tip]))))
            parent.appendRow(group)

    def _version_items(self, parent, dataset):
        for version in dataset.versions:
            item = self._item(f"Version {version.label}", ("version", dataset.id, version.id), "What this version holds: its variables")
            for vid in version.variables:
                variable = self.catalogue.variables.get(vid)
                if variable is not None:
                    item.appendRow(self._item(variable.title + ("  (record)" if variable.about == "record" else ""), ("variable", vid), variable.tooltip))
            parent.appendRow(item)

    def _share_lookup(self, data):
        dataset = self.catalogue.datasets[data[1]]
        return self.catalogue.leaves.get(data[4]) if data[4] else None, dataset, dataset.share[data[2]]["files"][data[3]]

    def _subset_service(self, dataset):
        """The service a subset is loaded from: a usable WFS, where the filter runs on the server."""
        return next((s for s in sorted(dataset.services, key=lambda s: s.priority) if s.type == "wfs" and self._usable(s)), None)

    def _subset_items(self, parent, dataset, leaf_id=None):
        """The dataset's subsets: the same data filtered on one categorical attribute."""
        if not dataset.subsets:
            return
        attribute = dataset.subsets["attribute"]
        service = self._subset_service(dataset)
        group = self._item(f"Subsets by {attribute}", ("subsets", dataset.id, leaf_id),
                           f"The same dataset, filtered on {attribute}: not separate datasets.")
        for index, subset in enumerate(dataset.subsets["values"]):
            tip = f"{dataset.title} where {attribute} = '{subset['value']}'"
            tip += "\nDouble-click adds it (WFS, filtered on the server)." if service else "\nNeeds a working WFS service."
            child = self._item(subset["label"], ("subset", dataset.id, index, leaf_id), tip)
            child.setEnabled(service is not None)
            group.appendRow(child)
        parent.appendRow(group)

    def add_subset(self, leaf, dataset, index):
        subset = dataset.subsets["values"][index]
        service = self._subset_service(dataset)
        if service is None or self._key_missing(service):
            return
        attribute = dataset.subsets["attribute"]
        expression = f"\"{attribute}\" = '{subset['value'].replace(chr(39), chr(39) * 2)}'"
        preferred = QgsProject.instance().crs().authid() or PREFERRED_CRS
        only_in_view = self.only_in_view.isChecked()
        name = f"{dataset.title} — {subset['label']}"

        def place(layer):
            layer.setSubsetString(expression)
            layer.setName(name)
            layer.setCustomProperty("semanticgis/subset", expression)
            place_layer(layer, service, leaf, dataset, dataset.owner)

        # filtered=True: the filter goes to the server in a POST body (see layers.wfs_uri)
        self._run_layer_task(name, lambda: build_layer(service, dataset, only_in_view, preferred, filtered=True), place)

    def _reference_items(self, parent, references):
        """Loadable geometries of reference units, one per scale."""
        for source in references:
            ref_index = self.catalogue.references.index(source)
            ref_item = self._item(f"Geometry: {source.label}", ("reference", ref_index), source.title)
            for geometry in source.geometries_by_detail():
                geom_index = source.geometries.index(geometry)
                ref_item.appendRow(self._item(geometry.scale, ("refgeom", ref_index, geom_index), geometry.url))
            parent.appendRow(ref_item)

    def _leaf_item(self, leaf, twig=None):
        marker = " ★" if twig and leaf.primary_lens == twig.id else ""
        item = self._item(leaf.title + marker, ("leaf", leaf.id), leaf.question)
        self._reference_items(item, self.catalogue.references_for(leaf_id=leaf.id))
        variables = self.catalogue.leaf_variables.get(leaf.id, [])
        for dataset in leaf.datasets:
            ds_item = self._dataset_item(dataset, leaf.id)
            if ds_item is not None:
                mark, why = LINK_NOTE.get(dataset.link_status.get(leaf.id), ("", ""))
                if mark:
                    ds_item.setText(ds_item.text() + mark)
                    ds_item.setToolTip(f"{why}\n{ds_item.toolTip()}")
                item.appendRow(ds_item)
        if variables:
            group = self._item(f"Variables ({len(variables)})", ("variables", leaf.id),
                               "What exactly is measured: datasets can be compared when they share a variable")
            for variable in variables:
                group.appendRow(self._item(variable.title, ("variable", variable.id), variable.tooltip))
            item.appendRow(group)
        for register in self.catalogue.registers_for(leaf.id):
            reg_item = self._item(f"Datafordeleren: {register['title']}", ("register", register["title"]),
                                  "Services of the Datafordeleren register that realises this leaf")
            for dataset in register["datasets"]:
                ds_item = self._dataset_item(dataset, leaf.id)
                if ds_item is not None:
                    reg_item.appendRow(ds_item)
            if reg_item.hasChildren():
                item.appendRow(reg_item)
        if not item.hasChildren():
            item.setToolTip(f"{leaf.question}\n\nNo loadable services yet.")
        return item

    def _sphere_item(self, sphere):
        sphere_item = self._item(sphere.title, ("sphere", sphere.id), sphere.description, bold=True)
        for twig in sphere.twigs:
            twig_item = self._item(f"{twig.title} ({len(twig.leaves)})", ("twig", twig.id))
            self._twig_leaves(twig_item, twig)
            sphere_item.appendRow(twig_item)
        return sphere_item

    def _twig_leaves(self, parent, twig):
        """A twig's leaves, under their headings where the twig has any; leaves without one come last."""
        groups = {}
        for leaf in twig.leaves:
            groups.setdefault(leaf.headings.get(twig.id), []).append(leaf)
        for heading in sorted(h for h in groups if h):
            item = self._item(heading, ("heading", twig.id), "A heading, not a level of its own: the leaves belong to the twig")
            for leaf in groups[heading]:
                item.appendRow(self._leaf_item(leaf, twig))
            parent.appendRow(item)
        for leaf in groups.get(None, []):
            parent.appendRow(self._leaf_item(leaf, twig))

    def _realisation_item(self, rel):
        leaf_id = next((t[len("leaf/"):] for t in rel.get("tags", []) if t.startswith("leaf/")), None)
        leaf = self.catalogue.leaves.get(leaf_id)
        text = f"{rel.get('dataset', rel['id'])} — {leaf.title if leaf else rel.get('leaf', '')}"
        detail = ", ".join(filter(None, [rel.get("collection_subtype"), ("from " + ", ".join(rel["derived_from"])) if rel.get("derived_from") else None]))
        item = self._item(text + (f"  ({detail})" if detail else ""), ("realisation", rel["id"]), rel.get("title"))
        self._reference_items(item, self.catalogue.references_for(realisation_id=rel["id"]))
        return item

    def _top(self, title, tooltip, page=None):
        return self._item(title, ("top", page), tooltip, bold=True)

    def _classical_branch(self):
        top = self._top("Classical Classifications", "Themes of INSPIRE, ISO 19115 and UN-GGIM and where they land in SPHERE", "/Classical Classifications/")
        by_standard = {}
        for theme in self.catalogue.classical_themes:
            by_standard.setdefault(theme["classification"], []).append(theme)
        for standard in sorted(by_standard):
            std_item = self._item(standard, ("standard", standard))
            for theme in sorted(by_standard[standard], key=lambda t: t["title"].lower()):
                theme_item = self._item(theme["title"], ("theme", theme["id"]), f"{standard}: {theme['code']}")
                for twig_id in theme["twigs"]:
                    twig = self.catalogue.twigs.get(twig_id)
                    if twig is None:
                        continue
                    sphere = next((s.title for s in self.catalogue.spheres if s.id == twig.sphere), "")
                    twig_item = self._item(f"{sphere} › {twig.title}", ("twig", twig.id))
                    for leaf in twig.leaves:
                        twig_item.appendRow(self._leaf_item(leaf, twig))
                    theme_item.appendRow(twig_item)
                if theme.get("collection_method"):
                    method = next((m for m in self.catalogue.collection_methods if m["id"] == theme["collection_method"]), None)
                    method_item = self._item(f"Collection method › {method['title'] if method else theme['collection_method']}", ("method", theme["collection_method"]))
                    for rel in self.catalogue.realisations_by_method().get(theme["collection_method"], []):
                        method_item.appendRow(self._realisation_item(rel))
                    theme_item.appendRow(method_item)
                std_item.appendRow(theme_item)
            top.appendRow(std_item)
        return top

    def _methods_branch(self):
        top = self._top("Methods", "How the data was produced: register, field measurement, remote sensing, modelled, volunteered, cartographic", "/Collection Methods/")
        by_method = self.catalogue.realisations_by_method()
        for method in self.catalogue.collection_methods:
            rels = sorted(by_method.get(method["id"], []), key=lambda r: r.get("dataset", "").lower())
            method_item = self._item(f"{method['title']} ({len(rels)})", ("method", method["id"]), method["description"])
            for rel in rels:
                method_item.appendRow(self._realisation_item(rel))
            derived = [r for r in self.catalogue.realisations.values() if method["id"] in (r.get("derived_from") or [])]
            if derived:
                derived_item = self._item(f"Derived from this method ({len(derived)})", ("derived", method["id"]))
                for rel in derived:
                    derived_item.appendRow(self._realisation_item(rel))
                method_item.appendRow(derived_item)
            top.appendRow(method_item)
        return top

    def _registers_branch(self):
        top = self._top("Registers", "Data that exists because of a public task: each register's datasets, one per class", "/Initiatives/index")
        for register_id, r in sorted(self.catalogue.registers.items(), key=lambda x: x[1]["title"].lower()):
            reg_item = self._item(r["title"], ("page", r["page"]), "Purpose and datasets")
            for dataset_id in r["datasets"]:
                dataset = self.catalogue.datasets.get(dataset_id)
                ds_item = self._dataset_item(dataset) if dataset else None
                if ds_item is not None:
                    reg_item.appendRow(ds_item)
            if reg_item.hasChildren():
                reg_item.setText(f"{r['title']} ({reg_item.rowCount()})")
            top.appendRow(reg_item)
        return top

    def _collections_branch(self):
        if getattr(self.catalogue, "flat", False):
            return self._registers_branch()
        top = self._top("Datasets by Collection", "Register documentation (Grunddatamodellen); accessed through GraphQL and file downloads", "/Datasets by Collection/")
        if self.catalogue.datafordeler:
            df_top = self._item("Datafordeleren", ("page", "https://datafordeler.dk/dataoversigt/"),
                                "Datafordeleren's registers and their WMS, WMTS, WFS and WCS services (API key)")
            for register in self.catalogue.datafordeler:
                reg_item = self._item(register["title"], ("register", register["title"]))
                for dataset in register["datasets"]:
                    ds_item = self._dataset_item(dataset)
                    if ds_item is not None:
                        reg_item.appendRow(ds_item)
                if reg_item.hasChildren():
                    reg_item.setText(f"{register['title']} ({reg_item.rowCount()})")
                    df_top.appendRow(reg_item)
            if df_top.hasChildren():
                top.appendRow(df_top)
        items = {}
        for collection in self.catalogue.collections:
            item = self._item(collection["title"], ("page", collection["path"]), collection["path"])
            items[collection["path"]] = item
            parent = items.get(collection["parent"])
            (parent or top).appendRow(item)
        return top

    def _owners_branch(self):
        flat = getattr(self.catalogue, "flat", False)
        top = (self._top("Organisations", "Datasets by the organisation that publishes them now", "/Organisations/index") if flat else
               self._top("Datasets by Owner", "Every harvested dataset, by the organisation that publishes it", "/Datasets by Owner/"))
        groups = self.catalogue.datasets_by_owner()
        for owner in sorted(groups, key=lambda o: self.catalogue.owners.get(o, o).lower()):
            title = self.catalogue.owners.get(owner, owner)
            owner_item = self._item(title, ("owner", owner))
            for dataset in groups[owner]:
                ds_item = self._dataset_item(dataset)
                if ds_item is not None:
                    owner_item.appendRow(ds_item)
            if owner_item.hasChildren():
                owner_item.setText(f"{title} ({owner_item.rowCount()})")
                top.appendRow(owner_item)
        return top

    def _sphere_branch(self):
        top = self._top("SPHERE", "The thematic spheres and their twigs", "/SPHERE/")
        for sphere in self.catalogue.thematic_spheres:
            top.appendRow(self._sphere_item(sphere))
        return top

    def _reference_branch(self):
        rf = self.catalogue.reference_framework
        top = self._top("Reference Framework", rf.description if rf else "", rf.path if rf else None)
        if rf:
            for twig in rf.twigs:
                twig_item = self._item(f"{twig.title} ({len(twig.leaves)})", ("twig", twig.id))
                for leaf in twig.leaves:
                    twig_item.appendRow(self._leaf_item(leaf, twig))
                top.appendRow(twig_item)
        return top

    BASEMAP_KINDS = (("topographic", "Topographic"), ("imagery", "Imagery"), ("historical", "Historical"), ("terrain", "Terrain"))

    def _basemaps_branch(self):
        top = self._top("Basemaps", "Backdrops under your data: topographic maps, imagery, historical maps and terrain", "/Basemaps/")
        for kind, title in self.BASEMAP_KINDS:
            items = sorted((b for b in self.catalogue.basemaps if b.kind == kind), key=lambda b: b.title.lower())
            kind_item = self._item(title, ("page", f"/Basemaps/{title}/"))
            for basemap in items:
                services = [s for s in sorted(basemap.services, key=lambda s: s.priority)
                            if self._usable(s) or not self.verified_only.isChecked()]
                if not services:
                    continue
                basemaps_type = services[0].type
                label = basemap.title + (f" ({basemap.period})" if basemap.period and basemap.period not in basemap.title else "")
                tip = "\n".join(filter(None, [basemap.provider, basemap.licence, f"Attribution: {basemap.attribution.line(basemaps_type)}" if basemap.attribution else None,
                                               "Double-click adds it at the bottom of the layer tree."]))
                bm_item = self._item(label, ("basemap", basemap.id), tip)
                preferred = self._preferred(basemap)
                for service in services:
                    note = STATUS_TEXT.get(service.status, service.status)
                    portal = f"{service.portal['title']} · " if service.portal else ""
                    star = " ★" if service is preferred else ""
                    what = service.layer_name if service.type != "xyz" else "tiles"
                    mark, key_tip = self._key_mark(service)
                    text = f"{mark}{service.type.upper()} · {portal}{what}{star}" + (f"  ({note})" if note else "")
                    bm_item.appendRow(self._item(text, ("bmservice", basemap.id, service.id),
                                                 "\n".join(filter(None, [service.endpoint, key_tip]))))
                kind_item.appendRow(bm_item)
            if kind_item.hasChildren():
                top.appendRow(kind_item)
        return top

    def _basemap(self, basemap_id):
        return next(b for b in self.catalogue.basemaps if b.id == basemap_id)

    def add_basemap(self, basemap, service=None):
        service = service or self._preferred(basemap)
        if service is None:   # e.g. only services that need a key the user has not set
            locked = next((s for s in basemap.services if KEYS.missing(s)), None)
            if locked is not None:
                self._key_missing(locked)
            return
        if self._key_missing(service):
            return
        preferred = QgsProject.instance().crs().authid() or PREFERRED_CRS
        self._run_layer_task(
            basemap.title,
            lambda: build_basemap_layer(basemap, service, preferred),
            lambda layer: place_basemap_layer(layer, basemap, service),
        )

    # -- areas: what the datasets are about ------------------------------------------

    CANVAS, MAP_SCALE = "@canvas", -1

    def _fill_areas(self):
        """The site's list of areas, grouped by kind (global, regional, national, local, project); the user's choice is kept (Denmark to begin with)."""
        known = getattr(self.catalogue, "areas", None) or {}
        self.area_row.setVisible(bool(known))
        if getattr(self, "_areas_shown", None) is known:
            return
        self._areas_shown = known
        wanted = settings_value("v2_area", "denmark")
        self.area.blockSignals(True)
        self.area.clear()
        self.area.addItem("All areas", "")
        self.area.addItem("Map canvas", self.CANVAS)
        if all(a.get("kind") for a in known.values()):
            for kind, title in KINDS:
                keys = [k for k, a in known.items() if a["kind"] == kind]
                if keys:
                    self.area.addItem(f"— {title} —", None)
                    self.area.model().item(self.area.count() - 1).setEnabled(False)
                    for key in keys:
                        self.area.addItem("    " + known[key]["title"], key)
        else:
            def add(parent, depth):
                for key, a in known.items():
                    if a.get("parent") == parent:
                        self.area.addItem("    " * depth + a["title"], key)
                        add(key, depth + 1)
            add(None, 0)
        self.area.setCurrentIndex(max(0, self.area.findData(wanted)))
        self.area.blockSignals(False)

    def _canvas_box(self):
        """The map canvas as (west, south, east, north) in degrees; None when there is no canvas."""
        try:
            canvas = self.iface.mapCanvas()
            to_degrees = QgsCoordinateTransform(canvas.mapSettings().destinationCrs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
            r = to_degrees.transformBoundingBox(canvas.extent())
            box = [max(-180.0, r.xMinimum()), max(-90.0, r.yMinimum()), min(180.0, r.xMaximum()), min(90.0, r.yMaximum())]
            return box if box[0] < box[2] and box[1] < box[3] else None
        except Exception:
            return None

    def _chosen(self):
        """(area, box): the chosen area, or for the map canvas the area it lies in and the canvas box."""
        known, chosen = getattr(self.catalogue, "areas", None) or {}, self.area.currentData()
        if chosen != self.CANVAS:
            return chosen, None
        box = self._canvas_box()
        if box is None:
            return "", None
        home = canvas_home(known, box)
        at = self.area.findData(self.CANVAS)
        self.area.setItemText(at, f"Map canvas ({known[home]['title']})" if home in known else "Map canvas")
        return home, box

    def _scale_now(self):
        """The chosen scale (1:n), the map's when 'as the map'; None for any scale."""
        n = self.scale.currentData()
        if n == self.MAP_SCALE:
            try:
                n = round(self.iface.mapCanvas().scale())
            except Exception:
                n = None
            self.scale.setItemText(self.scale.findData(self.MAP_SCALE), f"as the map (1:{n:,})" if n else "as the map")
        return n or None

    def _show_read_map(self):
        self.read_map.setVisible(self.area.currentData() == self.CANVAS or self.scale.currentData() == self.MAP_SCALE)

    def _area_chosen(self):
        QgsSettings().setValue(SETTINGS_PREFIX + "v2_area", self.area.currentData())
        self._show_read_map()
        self.rebuild()

    def _scale_chosen(self):
        QgsSettings().setValue(SETTINGS_PREFIX + "v2_scale", self.scale.currentData())
        self._show_read_map()
        self.rebuild()

    def _in_area(self, dataset):
        known = getattr(self.catalogue, "areas", None)
        if not known:
            return True
        if self._area_now is None:
            self._area_now, self._scale_value = self._chosen(), self._scale_now()
        return concerns(known, getattr(dataset, "scope", None), self._area_now[0], getattr(dataset, "useful_from", None), self._scale_value)

    def rebuild(self):
        self.model.clear()
        if not self.catalogue:
            return
        self._fill_areas()
        self._area_now = None                               # worked out once per rebuild (the canvas is read once)
        root = self.model.invisibleRootItem()
        text = self.search.text().strip()
        if text:
            leaves = self.catalogue.search(text)
            datasets = [d for d in self.catalogue.search_datasets(text) if (self._visible_services(d) or d.share) and self._in_area(d)]
            if leaves:
                group = self._top(f"Leaves ({len(leaves)})", "Leaves whose title or question matches", "/Leaves/")
                for leaf in leaves:
                    group.appendRow(self._leaf_item(leaf))
                root.appendRow(group)
            if datasets:
                group = self._top(f"Datasets ({len(datasets)})", "Datasets whose title matches", "/Datasets/index")
                for dataset in datasets:
                    ds_item = self._dataset_item(dataset)
                    if ds_item is not None:
                        group.appendRow(ds_item)
                root.appendRow(group)
            self.tree.expandToDepth(0)
            return
        for branch in (
            self._classical_branch,
            *(() if getattr(self.catalogue, "flat", False) else (self._methods_branch,)),      # v2: methods are inner workings
            self._collections_branch,
            self._owners_branch,
            self._sphere_branch,
            self._reference_branch,
            self._basemaps_branch,
        ):
            item = branch()
            if not item.hasChildren():
                # Older published catalogues lack the data for some entry points; say so rather than
                # showing an empty folder.
                note = self._item(
                    "Not in this catalogue yet",
                    ("note",),
                    "The catalogue at the configured source does not contain this entry point. "
                    "It appears when the site publishes a newer catalogue (or point Settings at the vault).",
                )
                note.setEnabled(False)
                item.appendRow(note)
            root.appendRow(item)

    # -- actions -------------------------------------------------------------------

    def _lookup(self, data):
        dataset = self.catalogue.datasets[data[1]]
        service = next(s for s in dataset.services if s.id == data[2])
        return self.catalogue.leaves.get(data[3]) if data[3] else None, dataset, service

    def activate(self, index):
        data = index.data(ROLE)
        if not data:
            return
        if data[0] == "service":
            self.add_layer(*self._lookup(data))
        elif data[0] == "sharefile":
            self.add_layer(*self._share_lookup(data))
        elif data[0] == "tool":
            self.open_tool(self.catalogue.datasets[data[1]].tools[data[2]])
        elif data[0] == "basemap":
            self.add_basemap(self._basemap(data[1]))
        elif data[0] == "bmservice":
            basemap = self._basemap(data[1])
            self.add_basemap(basemap, next(s for s in basemap.services if s.id == data[2]))
        elif data[0] == "dataset":
            dataset = self.catalogue.datasets[data[1]]
            preferred = self._preferred(dataset)
            if preferred is not None:
                leaf = self.catalogue.leaves.get(data[2]) if data[2] else None
                self.add_layer(leaf, dataset, preferred)
        elif data[0] == "subset":
            leaf = self.catalogue.leaves.get(data[3]) if data[3] else None
            self.add_subset(leaf, self.catalogue.datasets[data[1]], data[2])
        elif data[0] == "refgeom":
            source = self.catalogue.references[data[1]]
            self.add_reference_layer(source, source.geometries[data[2]])

    def open_tool(self, tool):
        """Open the dialog of one of the plugin's Processing tools."""
        if QgsApplication.processingRegistry().algorithmById(tool["algorithm"]) is None:
            self.iface.messageBar().pushMessage("SemanticGIS", f"This plugin version does not have the tool '{tool['title']}'. Update the plugin, "
                                                "and check that the Processing plugin is on.", Qgis.MessageLevel.Warning)
            return None
        import processing
        return processing.execAlgorithmDialog(tool["algorithm"], dict(tool.get("parameters") or {}))

    def _run_layer_task(self, title, build, place):
        """Build a layer in a background task (it talks to a server), add it on the main thread."""
        main_thread = QCoreApplication.instance().thread()

        def task_function(task):
            layer = build()
            definition = crs_definition(layer)   # as text: the CRS object itself is tied to this thread
            layer.moveToThread(main_thread)
            return layer, definition

        def on_finished(exception, result=None):
            layer = None
            if exception is None:
                layer, definition = result
                rebind_crs(layer, definition)
            if exception is not None:
                # Some QGIS installations cannot create certain layers (seen with Datafordeleren's WMTS)
                # outside the main thread although the same layer is valid on it: try once more here.
                QgsMessageLog.logMessage(f"{title}: background load failed: {getattr(exception, 'detail', '') or exception}",
                                         "SemanticGIS", Qgis.MessageLevel.Warning)
                try:
                    layer = build()
                except RuntimeError as error:
                    QgsMessageLog.logMessage(f"{title}: {getattr(error, 'detail', '') or error}", "SemanticGIS", Qgis.MessageLevel.Critical)
                    self.iface.messageBar().pushMessage("SemanticGIS", f"{error} (details: View › Panels › Log Messages › SemanticGIS)",
                                                        Qgis.MessageLevel.Critical)
                    return
            place(layer)
            self.iface.messageBar().pushMessage("SemanticGIS", f"Added {title}", Qgis.MessageLevel.Success, 4)

        task = QgsTask.fromFunction(f"Load {title}", task_function, on_finished=on_finished)
        self._layer_tasks.add(task)
        task.taskCompleted.connect(lambda: self._layer_tasks.discard(task))
        task.taskTerminated.connect(lambda: self._layer_tasks.discard(task))
        QgsApplication.taskManager().addTask(task)
        self.iface.messageBar().pushMessage("SemanticGIS", f"Loading {title}…", Qgis.MessageLevel.Info, 2)

    def add_reference_layer(self, source, geometry):
        self._run_layer_task(
            f"{source.unit} {geometry.scale}",
            lambda: build_reference_layer(source, geometry),
            lambda layer: place_reference_layer(layer, source, geometry),
        )

    # -- attribution -----------------------------------------------------------------

    def copy_attribution(self, attribution, service_type, title):
        """Copy one dataset's or basemap's attribution line, saying how sure the wording is."""
        line = attribution.line(service_type) if attribution else ""
        bar = self.iface.messageBar()
        if not line:
            bar.pushMessage("SemanticGIS", f"No attribution is recorded for {title}: {STATUS_NOTE['unknown']}.",
                            Qgis.MessageLevel.Warning, 8)
            return
        QApplication.clipboard().setText(line)
        note = STATUS_NOTE.get(attribution.status, "")
        if note:
            bar.pushMessage("SemanticGIS", f"Copied: {line} ({note})", Qgis.MessageLevel.Warning, 8)
        else:
            bar.pushMessage("SemanticGIS", f"Copied: {line}", Qgis.MessageLevel.Success, 5)

    def copy_map_attributions(self):
        copy_layer_attributions(self.iface, None, self.catalogue)

    def _key_missing(self, service):
        """Unlock the keys if needed; warn and return True when the service's key is not set."""
        if not KEYS.loaded and KEYS.profile_of(service) is not None:
            KEYS.load(prompt=True)
        problem = KEYS.missing(service)
        if problem:
            self.iface.messageBar().pushMessage("SemanticGIS", problem, Qgis.MessageLevel.Warning, 10)
        return bool(problem)

    def _build_with_legend(self, service, dataset, only_in_view, preferred, legend, locale):
        layer = build_layer(service, dataset, only_in_view, preferred)
        if service.type == "file" and service.kind != "raster":
            legends.prepare(layer, dataset, service, legend, locale)      # here, not on the main thread: it reads the network
        return layer

    def add_layer(self, leaf, dataset, service, legend=None):
        if self._key_missing(service):
            return
        if service.type == "download":   # opened in the browser, so the key goes in the link
            QDesktopServices.openUrl(QUrl(KEYS.url_with_key(service.endpoint, service)))
            return
        preferred = QgsProject.instance().crs().authid() or PREFERRED_CRS
        only_in_view = self.only_in_view.isChecked()
        locale = QgsApplication.locale()
        owner_title = (self.catalogue.owners.get(dataset.page.strip("/").split("/")[1], None)
                       if dataset.page.startswith("/Datasets by Owner/") else self.catalogue.owners.get(dataset.owner, dataset.owner))
        context = f" ({leaf.title})" if leaf else ""
        self._run_layer_task(
            f"{dataset.title}{context}",
            lambda: self._build_with_legend(service, dataset, only_in_view, preferred, legend, locale),
            lambda layer: place_layer(layer, service, leaf, dataset, owner_title, legend, locale),
        )

    @staticmethod
    def page_url(path):
        """URL of a vault path on the published site (Quartz 5 turns spaces into hyphens and lowercases every address).
        Absolute URLs (e.g. Datafordeleren's own pages) are returned as they are."""
        if path.startswith(("http://", "https://")):
            return path
        site = (settings_value("v2_site_url", DEFAULT_SITE_URL) or DEFAULT_SITE_URL).rstrip("/")
        return site + "/" + path.lstrip("/").replace(" ", "-").lower()

    def _open_page(self, path):
        QDesktopServices.openUrl(QUrl(self.page_url(path)))

    def page_for(self, data):
        """The site page behind any node of the tree, or None. Addresses from before v2 are replaced by today's."""
        page = self._page_for(data)
        moved = getattr(self.catalogue, "redirects", {}) if self.catalogue else {}
        if page and moved:
            page = moved.get(page) or moved.get(page.rstrip("/") + "/index") or page
        return page

    def _page_for(self, data):
        if not data or not self.catalogue:
            return None
        kind, c = data[0], self.catalogue
        if kind in ("top", "page"):
            return data[1]
        if kind == "standard":
            return f"/Classical Classifications/{data[1]}/"
        if kind == "theme":
            return next((t["path"] for t in c.classical_themes if t["id"] == data[1]), None)
        if kind in ("method", "derived"):
            return f"/Collection Methods/{data[1]}"
        if kind == "owner":
            o = getattr(c, "organisations", {}).get(data[1]) or {}
            return o.get("page") or f"/Datasets by Owner/{data[1]}/"
        if kind == "sphere":
            return next((s.path for s in c.spheres if s.id == data[1]), None)
        if kind in ("twig", "heading"):
            twig = c.twigs.get(data[1])
            return twig.path if twig and not twig.draft else None
        if kind == "leaf":
            leaf = c.leaves.get(data[1])
            return leaf.path if leaf else None
        if kind == "variables":
            return c.leaves[data[1]].path
        if kind == "variable":
            return c.variables[data[1]].page
        if kind == "version":
            return next((v.page for v in c.datasets[data[1]].versions if v.id == data[2]), None)
        if kind in ("share", "sharefile", "tool"):
            return c.datasets[data[1]].page
        if kind in ("subsets", "subset"):
            dataset = c.datasets.get(data[1])
            realisation = c.realisations.get(dataset.realisation) if dataset and dataset.realisation else None
            return (realisation or {}).get("path") or (dataset.page if dataset else None)
        if kind in ("dataset", "service"):
            dataset = c.datasets.get(data[1])
            return dataset.page if dataset else None
        if kind == "realisation":
            return c.realisations.get(data[1], {}).get("path")
        if kind in ("basemap", "bmservice"):
            return next((b.page for b in c.basemaps if b.id == data[1]), None)
        if kind == "register":
            register = next((r for r in c.datafordeler if r["title"] == data[1]), None)
            return (register or {}).get("page") or "/Datasets by Collection/Datafordeleren/"
        if kind in ("reference", "refgeom"):
            return c.realisations.get(c.references[data[1]].realisation, {}).get("path")
        return None

    def _key_actions(self, menu, data):
        """'Set your <key>…' and 'How to get a <key>' for nodes whose service needs a key."""
        kind, services = data[0], []
        if kind == "service":
            services = [self._lookup(data)[2]]
        elif kind in ("share", "sharefile"):
            services = [self.catalogue.datasets[data[1]].share[data[2]]["files"][0]]
        elif kind == "dataset":
            services = self.catalogue.datasets[data[1]].services
        elif kind in ("basemap", "bmservice"):
            basemap = self._basemap(data[1])
            services = [s for s in basemap.services if kind == "basemap" or s.id == data[2]]
        profiles = {p.id: p for p in filter(None, (KEYS.profile_of(s) for s in services))}
        for profile in profiles.values():
            verb = "Change" if KEYS.has_key(profile.id) else "Set"
            menu.addAction(f"{verb} your {profile.title}…", self.open_settings)
            if profile.page:
                menu.addAction(f"How to get a {profile.title}", lambda p=profile: self._open_page(p.page))
        if profiles:
            menu.addSeparator()

    def _legend_menu(self, menu, leaf, dataset, service):
        """'Add layer with legend ▸' for a file with a column whose code scheme has a legend."""
        found = legends.column_for(dataset, service)
        if not found:
            return
        legend, _ = found
        sub = menu.addMenu("Add layer with legend")
        sub.setToolTip(f"{legend['title']} ({legends.ORIGIN[legend['origin']]})")
        seen = set()
        for c in legends.choices(legend, None, QgsApplication.locale()):
            if c.title not in seen:                  # polygons and lines have each their style, with one name
                seen.add(c.title)
                sub.addAction(c.title, lambda c=c: self.add_layer(leaf, dataset, service, self._legend_id(legend, c, service)))
        sub.addSeparator()
        sub.addAction("Show the legend's web page", lambda: self._open_page(legend["page"]))

    @staticmethod
    def _legend_id(legend, choice, service):
        """The id to ask for: for a style, any style in the same language (the layer's geometry decides which)."""
        return choice.id if not choice.href else "lang:" + choice.lang

    def context_menu(self, position):
        index = self.tree.indexAt(position)
        data = index.data(ROLE) if index.isValid() else None
        if not data or not self.catalogue:
            return
        menu = QMenu(self)
        page = self.page_for(data)
        if page:
            menu.addAction("Show web page", lambda: self._open_page(page))
        kind = data[0]
        self._key_actions(menu, data)
        if kind == "service":
            leaf, dataset, service = self._lookup(data)
            label = "Open download link" if service.type == "download" else "Add layer"
            menu.addAction(label, lambda: self.add_layer(leaf, dataset, service))
            menu.addAction("Copy attribution", lambda: self.copy_attribution(dataset.attribution, service.type, dataset.title))
            menu.addAction("Copy endpoint URL", lambda: QApplication.clipboard().setText(service.endpoint))
            if service.portal and service.portal.get("page"):
                menu.addAction(f"Show portal page ({service.portal['title']})", lambda: self._open_page(service.portal["page"]))
        elif kind == "sharefile":
            leaf, dataset, service = self._share_lookup(data)
            menu.addAction("Add layer", lambda: self.add_layer(leaf, dataset, service))
            self._legend_menu(menu, leaf, dataset, service)
            menu.addAction("Copy attribution", lambda: self.copy_attribution(dataset.attribution, "download", dataset.title))
        elif kind in ("basemap", "bmservice"):
            basemap = self._basemap(data[1])
            service = next((s for s in basemap.services if s.id == data[2]), None) if kind == "bmservice" else None
            menu.addAction("Add basemap", lambda: self.add_basemap(basemap, service))
            bm_type = (service or self._preferred(basemap) or (basemap.services or [None])[0])
            menu.addAction("Copy attribution", lambda: self.copy_attribution(
                basemap.attribution, bm_type.type if bm_type else None, basemap.title))
            if basemap.terms_url:
                menu.addAction("Open licence/terms", lambda: QDesktopServices.openUrl(QUrl(basemap.terms_url)))
        elif kind == "dataset":
            dataset = self.catalogue.datasets[data[1]]
            preferred = self._preferred(dataset)
            if preferred is not None:
                leaf = self.catalogue.leaves.get(data[2]) if data[2] else None
                menu.addAction("Add preferred service", lambda: self.add_layer(leaf, dataset, preferred))
            first = preferred or (dataset.services or [None])[0]
            menu.addAction("Copy attribution", lambda: self.copy_attribution(
                dataset.attribution, first.type if first else None, dataset.title))
        elif kind == "refgeom":
            source = self.catalogue.references[data[1]]
            geometry = source.geometries[data[2]]
            menu.addAction("Add layer", lambda: self.add_reference_layer(source, geometry))
            menu.addAction("Copy URL", lambda: QApplication.clipboard().setText(geometry.url))
        if not menu.isEmpty():
            menu.exec(self.tree.viewport().mapToGlobal(position))
