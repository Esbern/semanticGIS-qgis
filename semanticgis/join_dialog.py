"""Dialog: join a table to reference units (NUTS, LAU, …) with automatic ID detection."""

import os

import datetime as dt

from qgis.core import Qgis, QgsApplication, QgsMapLayerProxyModel, QgsSettings, QgsTask
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDateEdit,
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
from .join import explain, KeyIndex, detect
from .joiner import add_joined_layer, build_dated_layer, build_joined_layer, matched_units, table_columns
from .timeslice import latest_period_start, period_start, time_field
from .settings import SETTINGS_PREFIX

try:  # QGIS >= 3.34 names the layer filters on Qgis
    VECTOR_FILTER = Qgis.LayerFilter.VectorLayer
except AttributeError:
    VECTOR_FILTER = getattr(QgsMapLayerProxyModel, "Filter", QgsMapLayerProxyModel).VectorLayer


def cache_dir():
    return os.path.join(QgsApplication.qgisSettingsDirPath(), "semanticgis", "references")


def _needs_key(source):
    """True when the source's geometry needs an API key the user has not set."""
    if not getattr(source, "wfs", None):
        return False
    from .access import KEYS
    return not KEYS.has_key(source.wfs.get("access", "datafordeler"))


def load_indexes(sources, country, task=None):
    """KeyIndex per reference source. Small sources are read first, so a country's bounding
    box (from NUTS level 0) is known before the large per-country sources are read."""
    cache = cache_dir()
    sized = []
    for source in sources:
        if getattr(source, "units", None) is not None:      # a join key: its units are in the catalogue
            sized.append((-1, source))
            continue
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
        if getattr(source, "units", None) is not None:
            indexes.append(KeyIndex(source, source.units, country))
            continue
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
        # units as they were: for join keys whose register keeps its history (DAGI on Datafordeleren, from 2018)
        self.when = QComboBox()
        self.when.currentIndexChanged.connect(self._when_changed)
        self.when_label = QLabel("Units as they were")
        scale_row.addRow(self.when_label, self.when)
        self.day = QDateEdit()
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("yyyy-MM-dd")
        self.day_label = QLabel("Date")
        scale_row.addRow(self.day_label, self.day)
        layout.addLayout(scale_row)
        self._show_when(False)

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
        self._columns = columns
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
            # a source that needs a key the user has not set comes after an equally good one that needs none
            self.candidates.sort(key=lambda c: (-round(c.rate, 2), _needs_key(c.source)))
            for candidate in self.candidates[:10]:
                text = candidate.describe() + (" — needs your Datafordeleren API key" if _needs_key(candidate.source) else "")
                self.results.addItem(QListWidgetItem(text))
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
        self._fill_when(candidate)
        for geometry in candidate.source.geometries_by_detail():
            self.scale.addItem(geometry.scale, geometry)
        unmatched = ", ".join(candidate.unmatched) or "none"
        notes = explain(candidate, getattr(self, "_columns", {}).get(candidate.field, []))
        key = f"\nJoin key: {candidate.source.join_key}" if getattr(candidate.source, "join_key", None) else ""
        self.details.setText(f"From: {candidate.source.title}{key}\nUnmatched values (examples): {unmatched}" + "".join(f"\n• {n}" for n in notes))
        self.join_button.setEnabled(True)

    # -- units as they were -------------------------------------------------------------

    def _show_when(self, on):
        for w in (self.when, self.when_label, self.day, self.day_label):
            w.setVisible(on)

    def _fill_when(self, candidate):
        """Today / on a date / each row at its period, for a join key with history (DAGI). The date defaults to the
        start of the latest period named in the table's columns (a wide StatBank table); time kept as rows joins each
        row to its own period."""
        self.when.blockSignals(True)
        self.when.clear()
        cfg = (getattr(candidate.source, "wfs", None) or {}).get("graphql")
        self._show_when(bool(cfg))
        if not cfg:
            self.when.blockSignals(False)
            return
        columns = getattr(self, "_columns", {})
        self._time_field = time_field({k: v for k, v in columns.items() if k != candidate.field})
        layer = self.table.currentLayer()
        latest = latest_period_start([f.name() for f in layer.fields()]) if layer else None
        self.when.addItem("today (the units valid now)", "today")
        self.when.addItem(f"on a date (from {cfg.get('from', '2018')[:10]}, DAGI's history)", "date")
        if self._time_field:
            self.when.addItem(f"each row at its own period ({self._time_field})", "rows")
        default = "rows" if self._time_field else "date" if latest else "today"
        self.when.setCurrentIndex(self.when.findData(default))
        day = latest or dt.date.today()
        from qgis.PyQt.QtCore import QDate
        self.day.setDate(QDate(day.year, day.month, day.day))
        self.when.blockSignals(False)
        self._when_changed()

    def _when_changed(self, *args):
        mode = self.when.currentData()
        self.day.setEnabled(mode == "date")
        self.scale.setEnabled(mode in (None, "today"))

    def run_dated_join(self, candidate, layer, mode):
        """The join with the units valid on a date, or each row with the units of its own period."""
        units = matched_units(layer, candidate)
        source = candidate.source
        if mode == "rows":
            field = self._time_field
            row_day = {}
            for f in layer.getFeatures():
                d = period_start(f[field])
                if d:
                    row_day[f.id()] = d
            days = sorted(set(row_day.values()))
            label = f"as at each {field}"
        else:
            q = self.day.date()
            days = [dt.date(q.year(), q.month(), q.day())]
            label = f"as on {days[0].isoformat()}"
        self.join_button.setEnabled(False)
        self.details.setText(f"Fetching the {source.unit} units valid on {len(days)} date{'s' if len(days) > 1 else ''} from Datafordeleren… "
                             "(the first time per date can take a minute: the borders are at full detail)")

        def task_function(task):
            def progress(i, n):
                task.setProgress(100 * i / max(n, 1))
                return task.isCanceled()
            return references.units_on(source, list(units), days, cache_dir(), progress)

        def on_finished(exception, result=None):
            self.join_button.setEnabled(True)
            if exception is not None:
                self.details.setText(f"Fetching the units failed: {exception}")
                return
            path, chosen, begins = result
            if mode == "rows":
                version_of_row = lambda row, unit: chosen.get(row_day.get(row.id()), {}).get(unit.id)
            else:
                version_of_row = lambda row, unit: chosen[days[0]].get(unit.id)
            try:
                joined, matched, unmatched = build_dated_layer(layer, candidate, path, version_of_row, label, self._time_field if mode == "rows" else None)
            except RuntimeError as error:
                self.details.setText(str(error))
                return
            add_joined_layer(joined)
            early = [d for d in days if begins and d < begins]
            note = (f" Before {begins.isoformat()} DAGI has no history: {len(early)} date{'s' if len(early) > 1 else ''} got the units of "
                    f"{begins.isoformat()} (municipalities and regions are unchanged since 2007; parishes and postcodes may differ).") if early else ""
            missing = f"; unmatched: {', '.join(unmatched[:8])}" if unmatched else ""
            self.details.setText(f"Added '{joined.name()}' with {matched} rows{missing}.{note}"
                                 + (" Its rows have their period's dates: step through them with the temporal controller." if mode == "rows" else ""))
            self.iface.messageBar().pushMessage("SemanticGIS", f"Joined {matched} rows of {layer.name()} to {source.unit} {label}", Qgis.MessageLevel.Success, 5)

        self._start(QgsTask.fromFunction("Fetch SemanticGIS units by date", task_function, on_finished=on_finished))

    # -- join ------------------------------------------------------------------------

    def run_join(self):
        row = self.results.currentRow()
        layer = self.table.currentLayer()
        geometry = self.scale.currentData()
        if row < 0 or layer is None or geometry is None:
            return
        candidate = self.candidates[row]
        mode = self.when.currentData() if self.when.isVisible() or self.when.count() else None
        if mode in ("date", "rows"):
            self.run_dated_join(candidate, layer, mode)
            return
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
