"""Dialog: import a table from Danmarks Statistik's StatBank as a wide QGIS table, ready to calculate and join."""

from qgis.core import Qgis, QgsApplication, QgsFeature, QgsField, QgsProject, QgsTask, QgsVectorLayer
from qgis.PyQt.QtCore import QMetaType, Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from . import statbank
from .layers import PROPERTY_PREFIX

try:
    STRING, DOUBLE = QMetaType.Type.QString, QMetaType.Type.Double
    QgsField("probe", STRING)
except (AttributeError, TypeError):
    from qgis.PyQt.QtCore import QVariant
    STRING, DOUBLE = QVariant.String, QVariant.Double

CODE = Qt.ItemDataRole.UserRole


def build_layer(info, wide, url):
    """A table layer (no geometry) from a wide table: code and label per row variable, one number column per cell."""
    layer = QgsVectorLayer("None", f"{info.id} — {info.title}" + (f" ({', '.join(wide.fixed)})" if wide.fixed else ""), "memory")
    p = layer.dataProvider()
    fields = [QgsField(n, STRING, comment=a) for n, a, _, _ in wide.row_fields] + [QgsField(c.name, DOUBLE, comment=c.alias) for c in wide.columns]
    p.addAttributes(fields)
    layer.updateFields()
    for i, (n, a, _, _) in enumerate(wide.row_fields):
        layer.setFieldAlias(i, a)
    for j, c in enumerate(wide.columns):
        layer.setFieldAlias(len(wide.row_fields) + j, c.alias)
    feats = []
    for row in wide.rows:
        f = QgsFeature(layer.fields())
        f.setAttributes([None if v is None else (float(v) if k >= len(wide.row_fields) else v) for k, v in enumerate(row)])
        feats.append(f)
    p.addFeatures(feats)
    for key, value in {"statbank_table": info.id, "statbank_title": info.title, "statbank_unit": info.unit,
                       "statbank_updated": info.updated, "statbank_url": url, "attribution": f"Kilde: Danmarks Statistik, {info.id}"}.items():
        layer.setCustomProperty(PROPERTY_PREFIX + key, value)
    return layer


class StatBankDialog(QDialog):
    def __init__(self, iface, catalogue, parent=None, open_join=None):
        super().__init__(parent)
        self.iface, self.catalogue, self.open_join = iface, catalogue, open_join
        self.info, self.layer, self._tasks = None, None, set()
        self.setWindowTitle("Import a table from Danmarks Statistik")
        self.setMinimumWidth(620); self.setMinimumHeight(560)
        layout = QVBoxLayout(self)
        intro = QLabel("Fetches a StatBank table straight from Danmarks Statistik (no file to download or edit): codes and names "
                       "for each area, and one number column per combination of the other values you tick — e.g. Kvinder and "
                       "Maend, or one column per period — so you can calculate ratios and changes in the field calculator, "
                       "and join the table to the map. Give a table code (FOLK1A) or paste a link to the table.")
        intro.setWordWrap(True); layout.addWidget(intro)
        row = QHBoxLayout()
        self.table = QLineEdit(); self.table.setPlaceholderText("FOLK1A"); self.table.returnPressed.connect(self.fetch_info)
        self.fetch_button = QPushButton("Fetch table"); self.fetch_button.clicked.connect(self.fetch_info)
        row.addWidget(self.table); row.addWidget(self.fetch_button); layout.addLayout(row)
        self.title = QLabel(); self.title.setWordWrap(True); layout.addWidget(self.title)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["Variables and values (tick what you want)"])
        self.tree.itemChanged.connect(self._changed); layout.addWidget(self.tree)
        self.time_rows = QCheckBox("Keep time as rows (one row per area and period) instead of one column per period")
        self.time_rows.toggled.connect(self._changed); layout.addWidget(self.time_rows)
        self.size = QLabel(); layout.addWidget(self.size)
        buttons = QHBoxLayout()
        self.import_button = QPushButton("Import table"); self.import_button.setEnabled(False); self.import_button.clicked.connect(self.run_import)
        self.join_button = QPushButton("Join it to the map…"); self.join_button.setEnabled(False); self.join_button.clicked.connect(self._join)
        buttons.addWidget(self.import_button); buttons.addWidget(self.join_button); buttons.addStretch(); layout.addLayout(buttons)
        self.details = QLabel(); self.details.setWordWrap(True); layout.addWidget(self.details)

    def _start(self, task):
        self._tasks.add(task)
        task.taskCompleted.connect(lambda: self._tasks.discard(task)); task.taskTerminated.connect(lambda: self._tasks.discard(task))
        QgsApplication.taskManager().addTask(task)

    # -- the table's variables ----------------------------------------------------------------------
    def fetch_info(self):
        tid = statbank.table_id(self.table.text())
        if not tid:
            self.details.setText("Give a table code such as FOLK1A, or a link to a table on statistikbanken.dk."); return
        self.fetch_button.setEnabled(False); self.details.setText(f"Fetching the description of {tid}…")

        def task_function(task):
            return statbank.parse_tableinfo(statbank.fetch(statbank.tableinfo_url(tid)))

        def done(exception, info=None):
            self.fetch_button.setEnabled(True)
            if exception is not None:
                self.details.setText(f"Could not fetch {tid}: {exception}"); return
            self.show_info(info)

        self._start(QgsTask.fromFunction(f"Fetch StatBank table {tid}", task_function, on_finished=done))

    def show_info(self, info):
        self.info = info
        self.title.setText(f"<b>{info.id}: {info.title}</b> ({info.unit}; updated {info.updated[:10]})")
        sel = info.default_selection()
        self.tree.blockSignals(True); self.tree.clear()
        for v in info.variables:
            what = "the map: one row per area" if v.map else "time" if v.time else "leave all unticked for the total" if v.elimination else "tick at least one"
            top = QTreeWidgetItem([f"{v.id} — {v.text} ({len(v.values)} values; {what})"])
            top.setData(0, CODE, v.id); top.setFlags(top.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
            for code, label in v.values:
                it = QTreeWidgetItem([f"{code}  {label}" if code != label else code]); it.setData(0, CODE, code)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, Qt.CheckState.Checked if code in sel.get(v.id, []) else Qt.CheckState.Unchecked)
                top.addChild(it)
            self.tree.addTopLevelItem(top)
            top.setExpanded(not v.map and len(v.values) <= 12)
        self.tree.blockSignals(False); self._changed()

    def selection(self):
        sel = {}
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            codes = [top.child(j).data(0, CODE) for j in range(top.childCount()) if top.child(j).checkState(0) == Qt.CheckState.Checked]
            if codes:
                sel[top.data(0, CODE)] = codes
        return sel

    def _changed(self, *args):
        if not self.info:
            return
        sel = self.selection(); n = statbank.cells(sel, self.info)
        missing = [v.id for v in self.info.variables if not v.elimination and v.id not in sel]
        ok = not missing and 0 < n <= statbank.MAX_CELLS
        self.size.setText(f"{n:,} cells selected" + (f" — tick at least one value of {', '.join(missing)}" if missing else "")
                          + (f" — too many (at most {statbank.MAX_CELLS:,})" if n > statbank.MAX_CELLS else ""))
        self.import_button.setEnabled(ok)

    # -- import -------------------------------------------------------------------------------------
    def run_import(self):
        info, sel = self.info, self.selection(); url = statbank.data_url(info.id, sel)
        area = info.area(); rows = [area.id] if area else [next(v.id for v in info.variables if not v.time)]
        time_rows = self.time_rows.isChecked()
        self.import_button.setEnabled(False); self.details.setText(f"Fetching {statbank.cells(sel, info):,} cells…")

        def task_function(task):
            return statbank.pivot(statbank.read_jsonstat(statbank.fetch(url)), rows, time_as_rows=time_rows)

        def done(exception, wide=None):
            self.import_button.setEnabled(True)
            if exception is not None:
                self.details.setText(f"Import failed: {exception}"); return
            self.layer = build_layer(info, wide, url)
            QgsProject.instance().addMapLayer(self.layer)
            self.iface.setActiveLayer(self.layer)
            self.join_button.setEnabled(self.open_join is not None)
            cols = ", ".join(c.name for c in wide.columns[:6]) + (" …" if len(wide.columns) > 6 else "")
            self.details.setText(f"Added '{self.layer.name()}': {len(wide.rows)} rows; columns {cols}. "
                                 f"In the field calculator e.g. \"{wide.columns[-1].name}\" / \"{wide.columns[0].name}\".")
            self.iface.messageBar().pushMessage("SemanticGIS", f"Imported {info.id} from Danmarks Statistik", Qgis.MessageLevel.Success, 5)

        self._start(QgsTask.fromFunction(f"Import StatBank {info.id}", task_function, on_finished=done))

    def _join(self):
        if self.open_join and self.layer:
            self.open_join(self.layer)
