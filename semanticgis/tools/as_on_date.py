"""Objects of a register as they were on a date, or what is new or gone between two dates (BBR, DAR, Matriklen, …).

A register with history keeps a row per version of each object: when it was valid in the world (virkningFra/Til),
when the register knew it (registreringFra/Til) and, in many registers, a life-cycle status. Which columns these are,
and which status codes mean that the object existed, comes from the catalogue (column maps per delivered layer and
status roles), so the tool works on any of them; a layer it does not know is read by the usual column names.
"""
import datetime as dt
import json
import os

from qgis.core import (
    QgsFeatureSink, QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException, QgsProcessingParameterBoolean,
    QgsProcessingParameterDateTime, QgsProcessingParameterEnum, QgsProcessingParameterFeatureSink,
    QgsProcessingParameterField, QgsProcessingParameterFeatureSource, QgsProcessingParameterString,
)

from .. import timeslice as T
from .compat import advanced

MODES = ["As it was on date 1", "New between date 1 and date 2 (exists on date 2, not on date 1)",
         "Gone between date 1 and date 2 (existed on date 1, not on date 2)"]
FIELD_PARAMS = (("VALID_FROM", "valid_from", "Valid from (virkningFra)"), ("VALID_TO", "valid_to", "Valid to (virkningTil)"),
                ("REG_FROM", "registered_from", "Registered from (registreringFra)"), ("REG_TO", "registered_to", "Registered to (registreringTil)"),
                ("STATUS", "status", "Status (life cycle)"), ("IDENTITY", "identity", "Object identity (id_lokalId)"))


def catalogue_parts():
    """(time_layers, status_roles) of the catalogue: the panel's, else the cached index, else SEMANTICGIS_CATALOGUE."""
    try:
        from qgis.utils import plugins
        cat = getattr(getattr(plugins.get("semanticgis"), "dock", None), "catalogue", None)
        if cat is not None and getattr(cat, "time_layers", None):
            return cat.time_layers, cat.status_roles
    except Exception:
        pass
    from qgis.core import QgsApplication
    for folder in (os.path.join(QgsApplication.qgisSettingsDirPath(), "semanticgis", "cache-v2"), os.environ.get("SEMANTICGIS_CATALOGUE", "")):
        p = os.path.join(folder, "index.v2.json")
        if folder and os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            return d.get("time_layers", {}), d.get("status_roles", {})
    return {}, {}


def plain(value):
    """A QGIS attribute value as text or a Python date: what timeslice reads."""
    if value is None:
        return None
    if hasattr(value, "isNull") and value.isNull():
        return None
    if hasattr(value, "toString") and hasattr(value, "timeSpec"):        # QDateTime: ISO with its offset
        from qgis.PyQt.QtCore import Qt
        return value.toString(Qt.DateFormat.ISODateWithMs)
    if hasattr(value, "toPyDate"):
        return value.toPyDate()
    return value


class AsOnDate(QgsProcessingAlgorithm):
    INPUT, MODE, DATE1, DATE2, KNOWN, BUILDING, CODES, OUTPUT = "INPUT", "MODE", "DATE1", "DATE2", "KNOWN", "BUILDING", "CODES", "OUTPUT"

    def name(self): return "asondate"
    def displayName(self): return "Objects as they were on a date (new / gone since)"
    def group(self): return "Vector selection"
    def groupId(self): return "vectorselection"
    def tags(self): return ["bitemporal", "temporal", "history", "virkning", "registrering", "livscyklus", "status", "bbr", "dar", "matriklen", "date", "new", "demolished"]
    def createInstance(self): return AsOnDate()

    def shortHelpString(self):
        return ("Selects the rows of a register layer with history that describe each object <b>as it was on a date</b> "
                "(e.g. all buildings in BBR on 1 January 2010), or the objects that are <b>new</b> or <b>gone</b> between two dates. "
                "An object counts as existing when one of its versions was valid on the date (virkningFra ≤ date &lt; virkningTil), "
                "as the register knows it now (or as it knew it on a date you give), and its status means that it existed "
                "(BBR: Opført, Gældende; under construction if you tick it). New and gone compare the objects by their identity (id_lokalId), "
                "so a building that went from projected to built counts as new.<br><br>"
                "The columns and the meaning of each status code come from the SemanticGIS catalogue (the column map of the "
                "delivered layer and the reviewed status roles); you can name other columns under Advanced parameters.<br><br>"
                "Use a layer with history (BBR's <i>…_hist</i>): a <i>current</i> layer holds only each object's latest version and "
                "cannot show the past. Datafordeleren's WFS gives the versions of the area you load.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(self.INPUT, "Register layer with history", [QgsProcessing.TypeVector]))
        self.addParameter(QgsProcessingParameterEnum(self.MODE, "What", options=MODES, defaultValue=0))
        self.addParameter(QgsProcessingParameterDateTime(self.DATE1, "Date 1", type=QgsProcessingParameterDateTime.Type.Date))
        self.addParameter(QgsProcessingParameterDateTime(self.DATE2, "Date 2 (new / gone; empty: today)", type=QgsProcessingParameterDateTime.Type.Date, optional=True))
        self.addParameter(QgsProcessingParameterBoolean(self.BUILDING, "Count objects under construction as existing", defaultValue=False))
        self.addParameter(advanced(QgsProcessingParameterDateTime(self.KNOWN, "As the register knew it on (empty: as known now)",
                                                                  type=QgsProcessingParameterDateTime.Type.Date, optional=True)))
        self.addParameter(advanced(QgsProcessingParameterString(self.CODES, "Status codes that count as existing (e.g. 6,7; empty: from the catalogue)", optional=True)))
        for key, _, label in FIELD_PARAMS:
            self.addParameter(advanced(QgsProcessingParameterField(key, f"{label} column (empty: from the catalogue)", parentLayerParameterName=self.INPUT, optional=True)))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Objects"))

    @staticmethod
    def _date(alg, parameters, name, context):
        if parameters.get(name) in (None, ""):
            return None
        q = alg.parameterAsDateTime(parameters, name, context)
        if not q.isValid():
            return None
        d = q.date()
        return dt.date(d.year(), d.month(), d.day())

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        mode = self.parameterAsEnum(parameters, self.MODE, context)
        d1 = self._date(self, parameters, self.DATE1, context)
        d2 = self._date(self, parameters, self.DATE2, context) or dt.date.today()
        known = self._date(self, parameters, self.KNOWN, context)
        if d1 is None:
            raise QgsProcessingException("Give date 1.")
        names = [f.name() for f in source.fields()]
        time_layers, roles = catalogue_parts()
        key = (T.layer_key(layer.source()) or layer.name().lower()) if layer else None   # a saved copy keeps its layer name (bygning_hist)
        tf = T.time_fields(names, time_layers, key)
        for p, attr, _ in FIELD_PARAMS:
            chosen = self.parameterAsString(parameters, p, context)
            if chosen:
                setattr(tf, attr, chosen)
        feedback.pushInfo(f"Layer {key or source.sourceName()}: {tf.form}; valid time {tf.valid_from}–{tf.valid_to}, registration "
                          f"{tf.registered_from}–{tf.registered_to}, status {tf.status}, identity {tf.identity}")
        if not tf.valid_from:
            raise QgsProcessingException("The layer has no valid-time column (virkningFra): name it under Advanced parameters.")
        if tf.form.startswith("current") and min(d1, d2 if mode else d1) < dt.date.today():
            raise QgsProcessingException(
                f"{key or 'This layer'} holds only the current version of each object: it cannot show the objects as they were on "
                f"{d1.isoformat()}. Load the layer with history from the same service (e.g. {(key or '').replace('_current', '_hist') or 'bygning_hist'}).")
        if mode and not tf.identity:
            raise QgsProcessingException("New and gone compare objects by their identity: name the identity column (id_lokalId) under Advanced parameters.")

        existing = None
        codes = self.parameterAsString(parameters, self.CODES, context).strip()
        if codes:
            existing = {c.strip() for c in codes.split(",") if c.strip()}
        elif tf.status and tf.status_list:
            include = ("exists", "under-construction") if self.parameterAsBool(parameters, self.BUILDING, context) else ("exists",)
            existing = T.existing_codes(roles, tf.status_list, include) or None
            lst = ((roles or {}).get("lists") or {}).get(tf.status_list) or {}
            feedback.pushInfo(f"Status codes counted as existing ({tf.status_list}, {lst.get('status', 'unknown')}): {', '.join(sorted(existing or [], key=lambda c: (len(c), c)))}")
        elif tf.status:
            feedback.pushWarning(f"The meaning of the status column {tf.status} is not known: every status is counted. Give the codes that count under Advanced parameters.")

        wanted = [x for x in (tf.valid_from, tf.valid_to, tf.registered_from, tf.registered_to, tf.status, tf.identity) if x]
        idx = {n: names.index(n) for n in wanted}
        t1, t2 = T.day_instant(d1), T.day_instant(d2)
        k = T.day_instant(known) if known else None
        on1, on2, total = [], [], 0
        n = source.featureCount() or 1
        for i, f in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break
            if i % 1000 == 0:
                feedback.setProgress(90 * i / n)
            attrs = f.attributes()
            row = {x: plain(attrs[j]) for x, j in idx.items()}
            total += 1
            if T.valid_on(row, tf, t1, k, existing):
                on1.append((row.get(tf.identity), f))
            if mode in (1, 2) and T.valid_on(row, tf, t2, k, existing):
                on2.append((row.get(tf.identity), f))
        if mode == 0:
            out = [f for _, f in on1]
        elif mode == 1:
            before = {i for i, _ in on1}
            out = [f for i, f in on2 if i not in before]
        else:
            after = {i for i, _ in on2}
            out = [f for i, f in on1 if i not in after]
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, source.fields(), source.wkbType(), source.sourceCrs())
        for f in out:
            sink.addFeature(f, QgsFeatureSink.FastInsert)
        if mode == 1 and tf.built and tf.built in names:
            j = names.index(tf.built)
            years = [plain(f.attributes()[j]) for f in out]
            old = sum(1 for y in years if isinstance(y, (int, float)) and 1000 < y < d1.year)
            unknown = sum(1 for y in years if not isinstance(y, (int, float)) or y <= 1000)
            if old or unknown:
                feedback.pushWarning(f"New in the register is not always newly built: {old} of the new rows have a construction year ({tf.built}) "
                                     f"before {d1.year}" + (f", {unknown} have none (1000 or empty)" if unknown else "") + ". The register added them "
                                     f"after {d1.isoformat()}: buildings registered late, or registered anew. To keep only new buildings, select on "
                                     f"{tf.built} as well.")
        ids = len({(plain(f.attributes()[idx[tf.identity]]) if tf.identity else f.id()) for f in out})
        what = ["existing on " + d1.isoformat(), f"new between {d1.isoformat()} and {d2.isoformat()}", f"gone between {d1.isoformat()} and {d2.isoformat()}"][mode]
        feedback.pushInfo(f"{len(out)} rows ({ids} objects) {what}, of {total} rows" + (f"; as the register knew it on {known.isoformat()}" if known else ""))
        if tf.identity and ids < len(out):
            feedback.pushWarning(f"{len(out) - ids} objects have more than one valid row: the register holds overlapping versions for them.")
        return {self.OUTPUT: dest}
