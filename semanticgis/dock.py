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
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .catalogue import DEFAULT_BASE_URL, CatalogueError, load_catalogue
from .layers import PREFERRED_CRS, build_layer, place_layer
from .settings import DEFAULT_SITE_URL, SETTINGS_PREFIX, SettingsDialog

ROLE = Qt.ItemDataRole.UserRole + 1
STATUS_TEXT = {
    "ok": "",
    "needs-auth": "needs token",
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
            checked = self.catalogue.services_checked or "never"
            self.status.setText(
                f"SPHERE v{self.catalogue.version} · {len(self.catalogue.leaves)} leaves · "
                f"services checked {checked}" + (" · offline copy" if from_cache else "")
            )
            self.rebuild()

        self._task = QgsTask.fromFunction("Load SemanticGIS catalogue", task_function, on_finished=on_finished)
        QgsApplication.taskManager().addTask(self._task)

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

    def _leaf_item(self, leaf, twig=None):
        marker = " ★" if twig and leaf.primary_lens == twig.id else ""
        item = self._item(leaf.title + marker, ("leaf", leaf.id), leaf.question)
        for dataset in leaf.datasets:
            services = self._visible_services(dataset)
            if not services:
                continue
            ds_item = self._item(dataset.title, ("dataset", dataset.id, leaf.id), dataset.page)
            for service in services:
                note = STATUS_TEXT.get(service.status, service.status)
                text = f"{service.type.upper()} · {service.label}" + (f"  ({note})" if note else "")
                tip = f"{service.endpoint}\nlayer: {service.layer_name or '—'}"
                ds_item.appendRow(self._item(text, ("service", dataset.id, service.id, leaf.id), tip))
            item.appendRow(ds_item)
        if not item.hasChildren():
            item.setToolTip(f"{leaf.question}\n\nNo loadable services yet.")
        return item

    def rebuild(self):
        self.model.clear()
        if not self.catalogue:
            return
        root = self.model.invisibleRootItem()
        text = self.search.text().strip()
        if text:
            for leaf in self.catalogue.search(text):
                root.appendRow(self._leaf_item(leaf))
            self.tree.expandToDepth(0)
            return
        for sphere in self.catalogue.spheres:
            sphere_item = self._item(sphere.title, ("sphere", sphere.id), sphere.description, bold=True)
            for twig in sphere.twigs:
                twig_item = self._item(f"{twig.title} ({len(twig.leaves)})", ("twig", twig.id))
                for leaf in twig.leaves:
                    twig_item.appendRow(self._leaf_item(leaf, twig))
                sphere_item.appendRow(twig_item)
            root.appendRow(sphere_item)

    # -- actions -------------------------------------------------------------------

    def _lookup(self, data):
        dataset = self.catalogue.datasets[data[1]]
        service = next(s for s in dataset.services if s.id == data[2])
        return self.catalogue.leaves[data[3]], dataset, service

    def activate(self, index):
        data = index.data(ROLE)
        if data and data[0] == "service":
            self.add_layer(*self._lookup(data))

    def add_layer(self, leaf, dataset, service):
        if service.type == "download":
            QDesktopServices.openUrl(QUrl(service.endpoint))
            return
        token = settings_value("dataforsyningen_token")
        if service.auth == "dataforsyningen-token" and not token:
            self.iface.messageBar().pushMessage(
                "SemanticGIS", "This service needs a Dataforsyningen token (Settings).", Qgis.MessageLevel.Warning
            )
            return
        # Building a layer talks to the server and can be slow, so it runs as a background task;
        # the finished layer is moved to the main thread and added there.
        preferred = QgsProject.instance().crs().authid() or PREFERRED_CRS
        only_in_view = self.only_in_view.isChecked()
        main_thread = QCoreApplication.instance().thread()

        def task_function(task):
            layer = build_layer(service, dataset, token, only_in_view, preferred)
            layer.moveToThread(main_thread)
            return layer

        def on_finished(exception, layer=None):
            if exception is not None:
                self.iface.messageBar().pushMessage("SemanticGIS", str(exception), Qgis.MessageLevel.Critical)
                return
            place_layer(layer, service, leaf, dataset)
            self.iface.messageBar().pushMessage(
                "SemanticGIS", f"Added {dataset.title} ({leaf.title})", Qgis.MessageLevel.Success, 4
            )

        task = QgsTask.fromFunction(f"Load {dataset.title}", task_function, on_finished=on_finished)
        self._layer_tasks.add(task)
        task.taskCompleted.connect(lambda: self._layer_tasks.discard(task))
        task.taskTerminated.connect(lambda: self._layer_tasks.discard(task))
        QgsApplication.taskManager().addTask(task)
        self.iface.messageBar().pushMessage("SemanticGIS", f"Loading {dataset.title}…", Qgis.MessageLevel.Info, 2)

    def _open_page(self, path):
        site = (settings_value("site_url", DEFAULT_SITE_URL) or DEFAULT_SITE_URL).rstrip("/")
        QDesktopServices.openUrl(QUrl(site + "/" + path.lstrip("/").replace(" ", "-")))

    def context_menu(self, position):
        index = self.tree.indexAt(position)
        data = index.data(ROLE) if index.isValid() else None
        if not data or not self.catalogue:
            return
        menu = QMenu(self)
        kind = data[0]
        if kind == "leaf":
            leaf = self.catalogue.leaves[data[1]]
            menu.addAction("Open leaf page", lambda: self._open_page(leaf.path))
        elif kind == "dataset":
            dataset = self.catalogue.datasets[data[1]]
            menu.addAction("Open dataset page", lambda: self._open_page(dataset.page))
        elif kind == "service":
            leaf, dataset, service = self._lookup(data)
            label = "Open download link" if service.type == "download" else "Add layer"
            menu.addAction(label, lambda: self.add_layer(leaf, dataset, service))
            menu.addAction("Copy endpoint URL", lambda: QApplication.clipboard().setText(service.endpoint))
        menu.exec(self.tree.viewport().mapToGlobal(position))
