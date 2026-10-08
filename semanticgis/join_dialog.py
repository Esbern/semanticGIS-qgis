"""Dialog: join a table to reference units (NUTS, LAU, …) with automatic ID detection."""

import os

from qgis.core import Qgis, QgsApplication, QgsMapLayerProxyModel, QgsSettings, QgsTask
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from . import references
from .join import KeyIndex, detect
from .joiner import add_joined_layer, build_joined_layer, matched_units, table_columns
from .settings import SETTINGS_PREFIX

try:  # QGIS >= 3.34 names the layer filters on Qgis
    VECTOR_FILTER = Qgis.LayerFilter.VectorLayer
except AttributeError:
    VECTOR_FILTER = getattr(QgsMapLayerProxyModel, "Filter", QgsMapLayerProxyModel).VectorLayer


def cache_dir():
    return os.path.join(QgsApplication.qgisSettingsDirPath(), "semanticgis", "references")


def load_indexes(sources, country, task=None):
    """KeyIndex per reference source. Small sources are read first, so a country's bounding
    box (from NUTS level 0) is known before the large per-country sources are read."""
    cache = cache_dir()
    sized = []
    for source in sources:
        try:
            _, count = references.describe(source.index_geometry.url)
        except RuntimeError:
            count = -1
        sized.append((count if count >= 0 else 10**9, source))
    units_read, indexes, problems = [], [], []
    for i, (_, source) in enumerate(sorted(sized, key=lambda s: s[0])):
        if task is not None:
            if task.isCanceled():
                return indexes, problems
            task.setProgress(100 * i / max(len(sized), 1))
        bbox = references.country_bbox(units_read, country)
        try:
            units = references.read_units(source, cache, country=country, country_bbox=bbox)
        except (RuntimeError, ValueError) as error:
            problems.append(f"{source.label}: {error}")
            continue
        units_read.extend(units)
        indexes.append(KeyIndex(source, units, country))
    return indexes, problems


class JoinDialog(QDialog):
    def __init__(self, iface, catalogue, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.catalogue = catalogue
        self.candidates = []
        self._tasks = set()
        self.setWindowTitle("Join a table to reference units")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Joins a table that carries unit IDs or names (for example a statistics table per "
            "municipality or NUTS region) to the geometry of those units. The join is detected "
            "from the values: codes in any format, such as 101, 0101, '101 København' or DK_101, "
            "and names as a last resort."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.table = QgsMapLayerComboBox()
        self.table.setFilters(VECTOR_FILTER)
        form.addRow("Table", self.table)
        self.country = QLineEdit(QgsSettings().value(SETTINGS_PREFIX + "join_country", "DK"))
        self.country.setMaxLength(2)
        self.country.setToolTip("Country used to interpret national codes such as municipality numbers")
        form.addRow("Country for national codes", self.country)
        layout.addLayout(form)

        detect_row = QHBoxLayout()
        self.detect_button = QPushButton("Detect join")
        self.detect_button.clicked.connect(self.run_detect)
        detect_row.addWidget(self.detect_button)
        detect_row.addStretch()
        layout.addLayout(detect_row)

        self.results = QListWidget()
        self.results.currentRowChanged.connect(self.select_candidate)
        layout.addWidget(self.results)
        self.details = QLabel()
        self.details.setWordWrap(True)
        layout.addWidget(self.details)

        scale_row = QFormLayout()
        self.scale = QComboBox()
        scale_row.addRow("Geometry scale", self.scale)
        layout.addLayout(scale_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.join_button = buttons.addButton("Join", QDialogButtonBox.ButtonRole.AcceptRole)
        self.join_button.setEnabled(False)
        self.join_button.clicked.connect(self.run_join)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if not catalogue.references:
            self.detect_button.setEnabled(False)
            self.details.setText("The catalogue has no reference units with geometries.")

    # -- detection -------------------------------------------------------------------

    def _start(self, task):
        self._tasks.add(task)
        task.taskCompleted.connect(lambda: self._tasks.discard(task))
        task.taskTerminated.connect(lambda: self._tasks.discard(task))
        QgsApplication.taskManager().addTask(task)

    def run_detect(self):
        layer = self.table.currentLayer()
        if layer is None:
            return
        country = self.country.text().strip().upper()
        QgsSettings().setValue(SETTINGS_PREFIX + "join_country", country)
        columns = table_columns(layer)
        sources = list(self.catalogue.references)
        self.results.clear()
        self.scale.clear()
        self.join_button.setEnabled(False)
        self.detect_button.setEnabled(False)
        self.details.setText("Reading reference units… (the first time per country can take a few seconds)")

        def task_function(task):
            indexes, problems = load_indexes(sources, country, task)
            return detect(columns, indexes), problems

        def on_finished(exception, result=None):
            self.detect_button.setEnabled(True)
            if exception is not None:
                self.details.setText(f"Detection failed: {exception}")
                return
            self.candidates, problems = result
            for candidate in self.candidates[:10]:
                self.results.addItem(QListWidgetItem(candidate.describe()))
            note = ("\n" + "\n".join(problems)) if problems else ""
            if self.candidates:
                self.results.setCurrentRow(0)
            else:
                self.details.setText("No column of this table matches a reference unit." + note)

        self._start(QgsTask.fromFunction("Detect SemanticGIS join", task_function, on_finished=on_finished))

    def select_candidate(self, row):
        self.scale.clear()
        if row < 0 or row >= len(self.candidates):
            self.join_button.setEnabled(False)
            return
        candidate = self.candidates[row]
        for geometry in candidate.source.geometries_by_detail():
            self.scale.addItem(geometry.scale, geometry)
        unmatched = ", ".join(candidate.unmatched) or "none"
        self.details.setText(f"From: {candidate.source.title}\nUnmatched values (examples): {unmatched}")
        self.join_button.setEnabled(True)

    # -- join ------------------------------------------------------------------------

    def run_join(self):
        row = self.results.currentRow()
        layer = self.table.currentLayer()
        geometry = self.scale.currentData()
        if row < 0 or layer is None or geometry is None:
            return
        candidate = self.candidates[row]
        units = matched_units(layer, candidate)
        bbox = references.union_bbox(units.values())
        self.join_button.setEnabled(False)
        self.details.setText(f"Fetching {len(units)} geometries at {geometry.scale}…")

        def task_function(task):
            return references.fetch_geometries(candidate.source, geometry, list(units), cache_dir(), bbox)

        def on_finished(exception, path=None):
            self.join_button.setEnabled(True)
            if exception is not None:
                self.details.setText(f"Fetching geometries failed: {exception}")
                return
            try:
                joined, matched, unmatched = build_joined_layer(layer, candidate, path, geometry.scale)
            except RuntimeError as error:
                self.details.setText(str(error))
                return
            add_joined_layer(joined)
            missing = f"; unmatched: {', '.join(unmatched[:8])}" if unmatched else ""
            self.details.setText(f"Added '{joined.name()}' with {matched} rows{missing}")
            self.iface.messageBar().pushMessage(
                "SemanticGIS", f"Joined {matched} rows of {layer.name()} to {candidate.source.unit}",
                Qgis.MessageLevel.Success, 5,
            )

        self._start(QgsTask.fromFunction("Fetch SemanticGIS reference geometries", task_function, on_finished=on_finished))
