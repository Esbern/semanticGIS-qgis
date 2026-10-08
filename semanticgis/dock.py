"""Dock panel: browse the SPHERE network and add layers from its services."""

import os

from qgis.core import Qgis, QgsApplication, QgsProject, QgsSettings, QgsTask
from qgis.PyQt.QtCore import QCoreApplication, QUrl, Qt
from qgis.PyQt.QtGui import QDesktopServices, QStandardItem, QStandardItemModel
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
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

from .catalogue import DEFAULT_BASE_URL, CatalogueError, load_catalogue
from .join_dialog import JoinDialog
from .layers import (
    PREFERRED_CRS,
    build_layer,
    build_reference_layer,
    missing_credentials,
    place_layer,
    place_reference_layer,
)
from .settings import DEFAULT_SITE_URL, SETTINGS_PREFIX, SettingsDialog

ROLE = Qt.ItemDataRole.UserRole + 1
STATUS_TEXT = {
    "ok": "",
    "needs-auth": "needs credentials",
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
        super().__init__("SemanticGIS", parent)
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
        source = settings_value("catalogue_source", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
        cache_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "semanticgis", "cache")
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
            self.join_button.setEnabled(bool(self.catalogue.references))
            checked = self.catalogue.services_checked or "never"
            services = f"services checked {checked}" if self.catalogue.has_services else "no services published yet"
            self.status.setText(
                f"SPHERE v{self.catalogue.version} · {len(self.catalogue.leaves)} leaves · "
                f"{services}" + (" · offline copy" if from_cache else "")
            )
            self.rebuild()

        self._task = QgsTask.fromFunction("Load SemanticGIS catalogue", task_function, on_finished=on_finished)
        QgsApplication.taskManager().addTask(self._task)

    def open_join(self):
        if self.catalogue is not None:
            JoinDialog(self.iface, self.catalogue, self).show()

    def open_settings(self):
        if SettingsDialog(self).exec():
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
            if (s.verified and s.loadable) or s.type == "download" or not self.verified_only.isChecked()
        ]

    def _service_items(self, parent, dataset, leaf_id=None):
        services = sorted(self._visible_services(dataset), key=lambda s: s.priority)
        preferred = dataset.preferred_service
        for service in services:
            note = STATUS_TEXT.get(service.status, service.status)
            portal = f"{service.portal['title']} · " if service.portal else ""
            star = " ★" if service is preferred else ""
            text = f"{service.type.upper()} · {portal}{service.label}{star}" + (f"  ({note})" if note else "")
            tip = f"{service.endpoint}\nlayer: {service.layer_name or '—'}"
            parent.appendRow(self._item(text, ("service", dataset.id, service.id, leaf_id), tip))
        return bool(services)

    def _dataset_item(self, dataset, leaf_id=None):
        preferred = dataset.preferred_service
        tip = dataset.page + (f"\nDouble-click adds the preferred service ({preferred.type.upper()}"
                              f"{' · ' + preferred.portal['title'] if preferred.portal else ''})" if preferred else "")
        item = self._item(dataset.title, ("dataset", dataset.id, leaf_id), tip)
        return item if self._service_items(item, dataset, leaf_id) else None

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
        for dataset in leaf.datasets:
            ds_item = self._dataset_item(dataset, leaf.id)
            if ds_item is not None:
                item.appendRow(ds_item)
        if not item.hasChildren():
            item.setToolTip(f"{leaf.question}\n\nNo loadable services yet.")
        return item

    def _sphere_item(self, sphere):
        sphere_item = self._item(sphere.title, ("sphere", sphere.id), sphere.description, bold=True)
        for twig in sphere.twigs:
            twig_item = self._item(f"{twig.title} ({len(twig.leaves)})", ("twig", twig.id))
            for leaf in twig.leaves:
                twig_item.appendRow(self._leaf_item(leaf, twig))
            sphere_item.appendRow(twig_item)
        return sphere_item

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
        top = self._top("Collection Methods", "How the data was produced: register, field measurement, remote sensing, modelled, volunteered, cartographic", "/Collection Methods/")
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

    def _collections_branch(self):
        top = self._top("Datasets by Collection", "Register documentation (Grunddatamodellen); accessed through GraphQL and file downloads", "/Datasets by Collection/")
        items = {}
        for collection in self.catalogue.collections:
            item = self._item(collection["title"], ("page", collection["path"]), collection["path"])
            items[collection["path"]] = item
            parent = items.get(collection["parent"])
            (parent or top).appendRow(item)
        return top

    def _owners_branch(self):
        top = self._top("Datasets by Owner", "Every harvested dataset, by the organisation that publishes it", "/Datasets by Owner/")
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

    def rebuild(self):
        self.model.clear()
        if not self.catalogue:
            return
        root = self.model.invisibleRootItem()
        text = self.search.text().strip()
        if text:
            leaves = self.catalogue.search(text)
            datasets = [d for d in self.catalogue.search_datasets(text) if self._visible_services(d)]
            if leaves:
                group = self._top(f"Leaves ({len(leaves)})", "Leaves whose title or question matches", "/Leaves/")
                for leaf in leaves:
                    group.appendRow(self._leaf_item(leaf))
                root.appendRow(group)
            if datasets:
                group = self._top(f"Datasets ({len(datasets)})", "Datasets whose title matches", "/Datasets by Owner/")
                for dataset in datasets:
                    ds_item = self._dataset_item(dataset)
                    if ds_item is not None:
                        group.appendRow(ds_item)
                root.appendRow(group)
            self.tree.expandToDepth(0)
            return
        for branch in (
            self._classical_branch,
            self._methods_branch,
            self._collections_branch,
            self._owners_branch,
            self._sphere_branch,
            self._reference_branch,
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
        elif data[0] == "dataset":
            dataset = self.catalogue.datasets[data[1]]
            preferred = dataset.preferred_service
            if preferred is not None:
                leaf = self.catalogue.leaves.get(data[2]) if data[2] else None
                self.add_layer(leaf, dataset, preferred)
        elif data[0] == "refgeom":
            source = self.catalogue.references[data[1]]
            self.add_reference_layer(source, source.geometries[data[2]])

    def _run_layer_task(self, title, build, place):
        """Build a layer in a background task (it talks to a server), add it on the main thread."""
        main_thread = QCoreApplication.instance().thread()

        def task_function(task):
            layer = build()
            layer.moveToThread(main_thread)
            return layer

        def on_finished(exception, layer=None):
            if exception is not None:
                self.iface.messageBar().pushMessage("SemanticGIS", str(exception), Qgis.MessageLevel.Critical)
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

    def credentials(self):
        return {
            key: settings_value(key)
            for key in ("dataforsyningen_token", "datafordeler_api_key")
        }

    def add_layer(self, leaf, dataset, service):
        if service.type == "download":
            QDesktopServices.openUrl(QUrl(service.endpoint))
            return
        credentials = self.credentials()
        problem = missing_credentials(service, credentials)
        if problem:
            self.iface.messageBar().pushMessage("SemanticGIS", problem, Qgis.MessageLevel.Warning)
            return
        preferred = QgsProject.instance().crs().authid() or PREFERRED_CRS
        only_in_view = self.only_in_view.isChecked()
        owner_title = self.catalogue.owners.get(dataset.page.strip("/").split("/")[1], None) if dataset.page.startswith("/Datasets by Owner/") else None
        context = f" ({leaf.title})" if leaf else ""
        self._run_layer_task(
            f"{dataset.title}{context}",
            lambda: build_layer(service, dataset, credentials, only_in_view, preferred),
            lambda layer: place_layer(layer, service, leaf, dataset, owner_title),
        )

    @staticmethod
    def page_url(path):
        """URL of a vault path on the published site (Quartz turns spaces into hyphens)."""
        site = (settings_value("site_url", DEFAULT_SITE_URL) or DEFAULT_SITE_URL).rstrip("/")
        return site + "/" + path.lstrip("/").replace(" ", "-")

    def _open_page(self, path):
        QDesktopServices.openUrl(QUrl(self.page_url(path)))

    def page_for(self, data):
        """The site page behind any node of the tree, or None."""
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
            return f"/Datasets by Owner/{data[1]}/"
        if kind == "sphere":
            return next((s.path for s in c.spheres if s.id == data[1]), None)
        if kind == "twig":
            twig = c.twigs.get(data[1])
            return twig.path if twig and not twig.draft else None
        if kind == "leaf":
            leaf = c.leaves.get(data[1])
            return leaf.path if leaf else None
        if kind in ("dataset", "service"):
            dataset = c.datasets.get(data[1])
            return dataset.page if dataset else None
        if kind == "realisation":
            return c.realisations.get(data[1], {}).get("path")
        if kind in ("reference", "refgeom"):
            return c.realisations.get(c.references[data[1]].realisation, {}).get("path")
        return None

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
        if kind == "service":
            leaf, dataset, service = self._lookup(data)
            label = "Open download link" if service.type == "download" else "Add layer"
            menu.addAction(label, lambda: self.add_layer(leaf, dataset, service))
            menu.addAction("Copy endpoint URL", lambda: QApplication.clipboard().setText(service.endpoint))
            if service.portal and service.portal.get("page"):
                menu.addAction(f"Show portal page ({service.portal['title']})", lambda: self._open_page(service.portal["page"]))
        elif kind == "dataset":
            dataset = self.catalogue.datasets[data[1]]
            preferred = dataset.preferred_service
            if preferred is not None:
                leaf = self.catalogue.leaves.get(data[2]) if data[2] else None
                menu.addAction("Add preferred service", lambda: self.add_layer(leaf, dataset, preferred))
        elif kind == "refgeom":
            source = self.catalogue.references[data[1]]
            geometry = source.geometries[data[2]]
            menu.addAction("Add layer", lambda: self.add_reference_layer(source, geometry))
            menu.addAction("Copy URL", lambda: QApplication.clipboard().setText(geometry.url))
        if not menu.isEmpty():
            menu.exec(self.tree.viewport().mapToGlobal(position))
