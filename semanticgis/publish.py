"""Writing back to a research group's extension project, from QGIS's Layers panel:

  Link to leaf…              which SPHERE leaves the layer realises (stored in its metadata)
  Publish to research group… a file-based layer becomes a dataset of an extension project: the data
                             as COG (raster) or GeoParquet (vector) in the project's package registry,
                             its STAC Collection and the catalog's link to it in one commit
  Save metadata to GitLab    the layer's metadata (QGIS's metadata editor) back to its STAC Collection;
                             refused when someone else changed it since it was loaded
"""

import datetime
import json
import os
import tempfile

from qgis.core import (Qgis, QgsApplication, QgsMapLayer, QgsProject, QgsTask, QgsVectorFileWriter)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                                 QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QVBoxLayout)

from . import gitlab
from .layers import PROPERTY_PREFIX
from .network import get as network_get
from .stac_metadata import leaf_ids, new_collection, set_leaf_ids, slug, stac_from_metadata, wgs84_bbox

PACKAGE = "semanticgis-data"


class LeafPicker(QListWidget):
    """The SPHERE leaves with check boxes, filtered by a search field."""

    def __init__(self, leaves, checked=(), parent=None):
        super().__init__(parent)
        for leaf in sorted(leaves.values(), key=lambda l: l.title.lower()):
            item = QListWidgetItem(leaf.title)
            item.setData(Qt.ItemDataRole.UserRole, leaf.id)
            item.setToolTip(leaf.question or leaf.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if leaf.id in checked else Qt.CheckState.Unchecked)
            self.addItem(item)
        self.setMinimumHeight(220)

    def filter(self, text):
        text = text.lower()
        for i in range(self.count()):
            item = self.item(i)
            item.setHidden(bool(text) and text not in item.text().lower() and text not in (item.toolTip() or "").lower())

    def checked(self):
        return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())
                if self.item(i).checkState() == Qt.CheckState.Checked]


def _picker_with_search(layout, leaves, checked):
    search = QLineEdit()
    search.setPlaceholderText("Search leaves…")
    picker = LeafPicker(leaves, checked)
    search.textChanged.connect(picker.filter)
    layout.addRow("Leaves", search)
    layout.addRow(picker)
    return picker


def link_to_leaf(iface, catalogue, layer):
    """Choose the leaves a layer realises; stored as keywords (vocabulary semanticgis:leaf)."""
    dialog = QDialog(iface.mainWindow())
    dialog.setWindowTitle(f"Link {layer.name()} to SPHERE leaves")
    form = QFormLayout(dialog)
    note = QLabel("The leaves are stored in the layer's metadata (keywords, vocabulary <i>semanticgis:leaf</i>), "
                  "and go with it when you publish it or save its metadata to GitLab.")
    note.setWordWrap(True)
    form.addRow(note)
    picker = _picker_with_search(form, catalogue.leaves, leaf_ids(layer.metadata()))
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    if not dialog.exec():
        return
    metadata = layer.metadata()
    set_leaf_ids(metadata, picker.checked())
    layer.setMetadata(metadata)
    names = ", ".join(catalogue.leaves[i].title for i in picker.checked()) or "no leaf"
    iface.messageBar().pushMessage("SemanticGIS", f"{layer.name()} realises: {names}", Qgis.MessageLevel.Success, 5)


# -- publishing ------------------------------------------------------------------------

def publishable(layer):
    """Layers whose data can be written to a file: GDAL rasters and vector layers."""
    if layer.type() == QgsMapLayer.LayerType.RasterLayer:
        return layer.providerType() == "gdal"
    return layer.type() == QgsMapLayer.LayerType.VectorLayer


def export_layer(layer, folder, name):
    """Write the layer as a cloud-native file: COG for rasters, GeoParquet for vectors. Main thread."""
    if layer.type() == QgsMapLayer.LayerType.RasterLayer:
        from osgeo import gdal
        path = os.path.join(folder, name + ".tif")
        gdal.Translate(path, layer.source(), format="COG", creationOptions=["COMPRESS=DEFLATE", "PREDICTOR=YES", "BLOCKSIZE=256"])
        return path
    path = os.path.join(folder, name + ".parquet")
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "Parquet"
    error, message, *_ = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, QgsProject.instance().transformContext(), options)
    if error != QgsVectorFileWriter.WriterError.NoError:
        raise RuntimeError(f"Could not write GeoParquet: {message}")
    return path


class PublishDialog(QDialog):
    def __init__(self, iface, catalogue, layer):
        super().__init__(iface.mainWindow())
        self.catalogue = catalogue
        self.setWindowTitle(f"Publish {layer.name()} to a research group")
        self.setMinimumWidth(520)
        metadata = layer.metadata()
        form = QFormLayout(self)
        self.extension = QComboBox()
        for extension in catalogue.extensions:
            self.extension.addItem(f"{extension.title} ({extension.server}/{extension.path})", extension)
        form.addRow("Research group project", self.extension)
        self.title = QLineEdit(metadata.title() or layer.name())
        form.addRow("Title", self.title)
        self.description = QPlainTextEdit(metadata.abstract())
        self.description.setPlaceholderText("What is it, how was it made, what is it for?")
        self.description.setFixedHeight(90)
        form.addRow("Description", self.description)
        self.picker = _picker_with_search(form, catalogue.leaves, leaf_ids(metadata))
        self.derived = QComboBox()
        self.derived.addItem("(not derived from a SemanticGIS dataset)", None)
        for other in QgsProject.instance().mapLayers().values():
            dataset = catalogue.datasets.get(other.customProperty(PROPERTY_PREFIX + "dataset") or "")
            if other is not layer and dataset is not None:
                self.derived.addItem(f"{other.name()}  ←  {dataset.title}", dataset)
        if self.derived.count() == 2:
            self.derived.setCurrentIndex(1)
        form.addRow("Derived from", self.derived)
        self.attribution = QLineEdit((metadata.rights() or [""])[0])
        self.attribution.setPlaceholderText("Credit line for maps, e.g. Contains data from …")
        form.addRow("Attribution", self.attribution)
        kind = "COG (Cloud Optimized GeoTIFF)" if layer.type() == QgsMapLayer.LayerType.RasterLayer else "GeoParquet"
        note = QLabel(f"The data is written as {kind} to the project's package registry; its description "
                      "(STAC) and the catalog's link to it are saved in one commit. Only members of the project can see it.")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton("Publish", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


def publish(iface, catalogue, layer, on_done):
    """Ask for the details, export the layer, then upload and commit in a background task."""
    if not getattr(catalogue, "extensions", None):
        iface.messageBar().pushMessage("SemanticGIS", "No research group project: add an extension server in Settings.",
                                       Qgis.MessageLevel.Warning, 8)
        return None
    dialog = PublishDialog(iface, catalogue, layer)
    if not dialog.exec():
        return None
    return start_publish(
        iface, catalogue, layer, dialog.extension.currentData(), dialog.title.text().strip() or layer.name(),
        dialog.description.toPlainText().strip(), dialog.picker.checked(), dialog.derived.currentData(),
        dialog.attribution.text().strip(), on_done,
    )


def start_publish(iface, catalogue, layer, extension, title, description, leaves, derived_from, attribution, on_done):
    """Export the layer (main thread), then upload it and commit its description (background task)."""
    taken = {d.stac.get("id") for d in extension.datasets if d.stac}
    collection_id = f"{slug(title)}-{datetime.date.today().isoformat()}"
    n = 2
    while collection_id in taken:
        collection_id = f"{slug(title)}-{datetime.date.today().isoformat()}-{n}"
        n += 1

    # The layer's metadata follows the dialog, so the published description and the layer agree.
    metadata = layer.metadata()
    metadata.setTitle(title)
    metadata.setAbstract(description)
    set_leaf_ids(metadata, leaves)
    if attribution:
        metadata.setRights([attribution])
    layer.setMetadata(metadata)

    folder = tempfile.mkdtemp(prefix="semanticgis-publish-")
    path = export_layer(layer, folder, collection_id)   # reads the layer: main thread
    bbox = wgs84_bbox(layer)
    collection_path = f"collections/{collection_id}/collection.json"

    def task_function(task):
        version = datetime.date.today().strftime("%Y.%m.%d")
        url = gitlab.upload_package_file(extension, PACKAGE, version, os.path.basename(path), path)
        collection = new_collection(collection_id, title, description, url, path, extension.title, derived_from)
        collection = stac_from_metadata(metadata, None, collection, catalogue.leaves)
        if bbox:
            collection["extent"]["spatial"]["bbox"] = [bbox]
        catalog = json.loads(json.dumps(extension.catalog))
        href = f"./{collection_path}"
        if not any(link.get("href") == href for link in catalog.get("links", [])):
            catalog.setdefault("links", []).append({"rel": "child", "href": href, "type": "application/json", "title": title})
        return catalog, gitlab.commit(extension, f"Publish {title}\n\nPublished from QGIS with the SemanticGIS plugin.", [
            {"action": "create", "file_path": collection_path, "content": json.dumps(collection, indent=2, ensure_ascii=False) + "\n"},
            {"action": "update", "file_path": extension.catalog_path, "last_commit_id": extension.catalog_commit,
             "content": json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"},
        ])

    def finished(exception, result=None):
        if exception is not None:
            _report(iface, extension, extension.catalog_path, exception)
            return
        catalog, commit_id = result
        extension.catalog, extension.catalog_commit = catalog, commit_id   # a next publish starts from here
        layer.setCustomProperty(PROPERTY_PREFIX + "dataset", f"ext:{extension.server}:{extension.path}:{collection_id}")
        iface.messageBar().pushMessage("SemanticGIS", f"Published {title} to {extension.title} (commit {commit_id[:8]})",
                                       Qgis.MessageLevel.Success, 8)
        on_done()

    return _run(f"Publish {title}", task_function, finished)


# -- saving metadata -------------------------------------------------------------------

def save_metadata(iface, catalogue, layer, on_done):
    """Commit the layer's metadata to its STAC Collection, starting from the version that was loaded."""
    dataset = catalogue.datasets.get(layer.customProperty(PROPERTY_PREFIX + "dataset") or "")
    if dataset is None or dataset.extension is None or not dataset.stac_path:
        iface.messageBar().pushMessage("SemanticGIS", "This layer is not a dataset of a research group project "
                                       "(or the panel has not loaded it yet).", Qgis.MessageLevel.Warning, 8)
        return None
    extension = dataset.extension
    collection = stac_from_metadata(layer.metadata(), layer, dataset.stac, catalogue.leaves)
    if collection == dataset.stac:
        iface.messageBar().pushMessage("SemanticGIS", "Nothing to save: the metadata is unchanged.", Qgis.MessageLevel.Info, 5)
        return None
    text = json.dumps(collection, indent=2, ensure_ascii=False) + "\n"

    def task_function(task):
        return gitlab.commit(extension, f"Update the description of {collection.get('title', dataset.title)}\n\n"
                             "Edited in QGIS's metadata editor, saved with the SemanticGIS plugin.", [
            {"action": "update", "file_path": dataset.stac_path, "last_commit_id": dataset.stac_commit, "content": text},
        ])

    def finished(exception, commit_id=None):
        if exception is not None:
            _report(iface, extension, dataset.stac_path, exception)
            return
        dataset.stac, dataset.stac_commit = collection, commit_id   # the next save starts from this version
        dataset.title = collection.get("title", dataset.title)
        iface.messageBar().pushMessage("SemanticGIS", f"Saved the description of {dataset.title} (commit {commit_id[:8]})",
                                       Qgis.MessageLevel.Success, 8)
        on_done()

    return _run(f"Save {dataset.title}", task_function, finished)


# -- shared ----------------------------------------------------------------------------

_tasks = set()


def _run(title, task_function, finished):
    task = QgsTask.fromFunction(title, task_function, on_finished=finished)
    _tasks.add(task)
    task.taskCompleted.connect(lambda: _tasks.discard(task))
    task.taskTerminated.connect(lambda: _tasks.discard(task))
    QgsApplication.taskManager().addTask(task)
    return task


def _report(iface, extension, path, exception):
    if isinstance(exception, gitlab.ConflictError):
        change = gitlab.last_change(extension, path, network_get)
        who = f"\n\nLast change: {change[0]}, {change[1]}: “{change[2]}”" if change else ""
        QMessageBox.warning(
            iface.mainWindow(), "Changed by someone else",
            f"{path} in {extension.title} was changed by someone else after you loaded it, so your version was "
            f"not saved (nothing is overwritten).{who}\n\nReload the SemanticGIS panel to see their version. "
            "Your edits are still in the layer's metadata: compare, adjust and save again.",
        )
        return
    iface.messageBar().pushMessage("SemanticGIS", f"Saving to GitLab failed: {exception}", Qgis.MessageLevel.Critical, 12)
