"""Import a table from Danmarks Statistik's StatBank: the same as the panel's dialog, as a Processing tool (models, batch)."""
from qgis.core import (
    QgsFeature, QgsFeatureSink, QgsField, QgsFields, QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterBoolean, QgsProcessingParameterFeatureSink, QgsProcessingParameterString, QgsWkbTypes,
)

from .. import statbank
from .compat import DOUBLE, STRING


def join_codes():
    """The code sets of the catalogue's join keys, when the SemanticGIS panel has loaded a catalogue."""
    try:
        from qgis.utils import plugins
        cat = getattr(getattr(plugins.get("semanticgis"), "dock", None), "catalogue", None)
        return [{c["code"] for c in k.get("codes", [])} for k in (getattr(cat, "join_keys", None) or {}).values()]
    except Exception:
        return []


class StatBankTable(QgsProcessingAlgorithm):
    TABLE, SELECTION, TIME_ROWS, OUTPUT = "TABLE", "SELECTION", "TIME_ROWS", "OUTPUT"

    def name(self): return "statbanktable"
    def displayName(self): return "Import a table from Danmarks Statistik"
    def group(self): return "Data access"
    def groupId(self): return "dataaccess"
    def tags(self): return ["statbank", "statistikbanken", "danmarks statistik", "dst", "statistics", "table", "folk1a", "jsonstat", "kommune", "postnummer"]
    def createInstance(self): return StatBankTable()

    def shortHelpString(self):
        return ("Fetches a table from Danmarks Statistik's StatBank (api.statbank.dk, no key) and writes it as a table: a code and "
                "a name column for each area, and one number column per combination of the other values (e.g. Kvinder and Maend, "
                "or one column per period), ready for the field calculator and for <i>Join a table to reference units</i>.<br><br>"
                "<b>Table</b>: a code such as FOLK1A, or a link to the table on statistikbanken.dk.<br>"
                "<b>Values</b>: what to fetch beyond the defaults, as <code>VARIABLE=code,code</code> separated by semicolons, e.g. "
                "<code>KØN=1,2; Tid=2025K4,2026K4</code>; <code>*</code> means all values, an empty list leaves the variable out (its total). "
                "Defaults: all areas, the latest period, the total for every variable that can be left out, else its first value. "
                "The table's variables and codes are listed on its page on statistikbanken.dk, or in the panel's dialog.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterString(self.TABLE, "Table (code or link)", defaultValue="FOLK1A"))
        self.addParameter(QgsProcessingParameterString(self.SELECTION, "Values (VARIABLE=code,code; …)", optional=True, multiLine=True))
        self.addParameter(QgsProcessingParameterBoolean(self.TIME_ROWS, "Keep time as rows (one row per area and period)", defaultValue=False))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Table", QgsProcessing.TypeVector))

    def processAlgorithm(self, parameters, context, feedback):
        tid = statbank.table_id(self.parameterAsString(parameters, self.TABLE, context))
        if not tid:
            raise QgsProcessingException("Give a table code such as FOLK1A, or a link to a table on statistikbanken.dk.")
        feedback.pushInfo(f"Fetching the description of {tid}…")
        info = statbank.parse_tableinfo(statbank.fetch(statbank.tableinfo_url(tid)))
        codes = join_codes()
        try:
            sel = statbank.parse_selection(self.parameterAsString(parameters, self.SELECTION, context), info)
        except ValueError as e:
            raise QgsProcessingException(str(e))
        area = info.area(codes)
        if area and area.id not in sel:
            sel[area.id] = [c for c, _ in area.values]
        missing = [v.id for v in info.variables if not v.elimination and v.id not in sel]
        if missing:
            raise QgsProcessingException(f"Choose at least one value of {', '.join(missing)}")
        n = statbank.cells(sel, info)
        if n > statbank.MAX_CELLS:
            raise QgsProcessingException(f"{n:,} cells: at most {statbank.MAX_CELLS:,}; choose fewer values")
        feedback.pushInfo(f"{info.title}: fetching {n:,} cells ({'; '.join(f'{k}={len(v)} values' for k, v in sel.items())})")
        rows = [area.id] if area else [next(v.id for v in info.variables if not v.time)]
        wide = statbank.pivot(statbank.read_jsonstat(statbank.fetch_data(tid, sel, info=info)), rows,
                              time_as_rows=self.parameterAsBool(parameters, self.TIME_ROWS, context))
        fields = QgsFields()
        for name, alias, _, _ in wide.row_fields:
            fields.append(QgsField(name, STRING, comment=alias))
        for c in wide.columns:
            fields.append(QgsField(c.name, DOUBLE, comment=c.alias))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, QgsWkbTypes.NoGeometry)
        k = len(wide.row_fields)
        for row in wide.rows:
            f = QgsFeature(fields)
            f.setAttributes([None if v is None else (float(v) if i >= k else v) for i, v in enumerate(row)])
            sink.addFeature(f, QgsFeatureSink.FastInsert)
        feedback.pushInfo(f"{len(wide.rows)} rows; columns: {', '.join(c.name for c in wide.columns[:8])}{' …' if len(wide.columns) > 8 else ''}. "
                          f"Source: Danmarks Statistik, {tid} (updated {info.updated[:10]}).")
        return {self.OUTPUT: dest}
