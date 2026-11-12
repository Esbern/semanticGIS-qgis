"""Combination of values: the unordered set of values a feature has across several fields.

After flattening a snapshot series, each piece has one column per period (y2026_crop, y2024_crop, …). The order of
the crops depends on where a field is in its rotation, so two fields in the same rotation can show it in different
orders. This tool writes the values as an unordered combination, sorted and without repeats: "Spring barley,
Winter wheat" whether barley came before wheat or after.
"""
from collections import Counter

from qgis.core import (
    QgsFeature, QgsFeatureSink, QgsField, QgsFields, QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterBoolean, QgsProcessingParameterFeatureSink, QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField, QgsProcessingParameterString,
)

from .compat import INTEGER64, STRING
from .flatten_snapshots import FIELD_ANY


def combination(values, separator=", ", with_counts=False, keep_empty=False):
    """The distinct values, sorted (case-insensitive), joined; with_counts adds how often each occurs: 'Oats ×2'."""
    cleaned = []
    for v in values:
        empty = v is None or (hasattr(v, "isNull") and v.isNull()) or str(v).strip() == "" or str(v) == "NULL"
        if empty:
            if keep_empty:
                cleaned.append("(none)")
            continue
        cleaned.append(str(v).strip())
    counts = Counter(cleaned)
    names = sorted(counts, key=lambda s: (s.lower(), s))
    parts = [f"{n} ×{counts[n]}" if with_counts else n for n in names]
    return separator.join(parts), len(names)


def filled(values):
    """How many of the values are not empty."""
    return sum(1 for v in values if not (v is None or (hasattr(v, "isNull") and v.isNull()) or str(v).strip() in ("", "NULL")))


class ValueCombination(QgsProcessingAlgorithm):
    INPUT, FIELDS, NAME, SEPARATOR, COUNTS, EMPTY, NUMBER, OUTPUT = (
        "INPUT", "FIELDS", "FIELD_NAME", "SEPARATOR", "WITH_COUNTS", "KEEP_EMPTY", "ADD_NUMBER", "OUTPUT")

    def name(self): return "valuecombination"
    def displayName(self): return "Combination of values (unordered)"
    def group(self): return "Vector table"
    def groupId(self): return "vectortable"
    def createInstance(self): return ValueCombination()

    def shortHelpString(self):
        return (
            "Adds a field with the <b>unordered combination</b> of the values in the fields you pick: the distinct "
            "values, sorted and joined. Made for a flattened snapshot series, where the period columns "
            "(y2026_acr_class … y2020_acr_class) hold a field's crops through time: two fields in the same crop "
            "rotation, at different stages of it, get the same combination.<br><br>"
            "Wheat, barley, wheat, wheat gives <i>Spring barley, Winter wheat</i>; with <b>how often</b> ticked it "
            "gives <i>Spring barley ×1, Winter wheat ×3</i>, which tells a wheat-dominated rotation from a "
            "barley-dominated one but is still unordered.<br>"
            "Empty values (a period in which the piece had no field) are left out, unless you choose to count "
            "them as '(none)'.<br>"
            "Two more fields can be added: <b>…_n</b>, the number of distinct values (1 = the same value in every "
            "filled period), and <b>…_periods</b>, the number of periods with a value. Use …_periods to see how "
            "complete a piece's history is: <i>Permanent grass</i> from one period of six is not the same as from "
            "all six.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(self.INPUT, "Input layer", [QgsProcessing.TypeVector]))
        self.addParameter(QgsProcessingParameterField(self.FIELDS, "Fields to combine (e.g. the period columns)", None, self.INPUT,
                                                      FIELD_ANY, allowMultiple=True))
        self.addParameter(QgsProcessingParameterString(self.NAME, "Name of the new field", "combination"))
        self.addParameter(QgsProcessingParameterString(self.SEPARATOR, "Separator", ", "))
        self.addParameter(QgsProcessingParameterBoolean(self.COUNTS, "Add how often each value occurs (Winter wheat ×3)", False))
        self.addParameter(QgsProcessingParameterBoolean(self.EMPTY, "Count empty values as '(none)'", False))
        self.addParameter(QgsProcessingParameterBoolean(self.NUMBER, "Add fields with the number of distinct values and of filled periods", True))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Combined"))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        as_strings = getattr(self, "parameterAsStrings", None) or self.parameterAsFields   # parameterAsFields before QGIS 3.40
        picked = as_strings(parameters, self.FIELDS, context)
        if not picked:
            raise QgsProcessingException("Choose the fields to combine.")
        name = self.parameterAsString(parameters, self.NAME, context).strip() or "combination"
        separator = self.parameterAsString(parameters, self.SEPARATOR, context)
        separator = separator if separator != "" else ", "
        with_counts = self.parameterAsBool(parameters, self.COUNTS, context)
        keep_empty = self.parameterAsBool(parameters, self.EMPTY, context)
        add_number = self.parameterAsBool(parameters, self.NUMBER, context)

        fields = QgsFields(source.fields())
        if fields.indexOf(name) >= 0:
            raise QgsProcessingException(f"The layer already has a field {name}; choose another name.")
        fields.append(QgsField(name, STRING))
        number_name, filled_name = f"{name}_n", f"{name}_periods"
        if add_number:
            for extra in (number_name, filled_name):
                if fields.indexOf(extra) >= 0:
                    raise QgsProcessingException(f"The layer already has a field {extra}.")
                fields.append(QgsField(extra, INTEGER64))
        indexes = [source.fields().indexOf(f) for f in picked]

        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, source.wkbType(), source.sourceCrs())
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        total = source.featureCount() or 1
        distinct = Counter()
        for i, f in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break
            values = [f.attribute(j) for j in indexes]
            text, n = combination(values, separator, with_counts, keep_empty)
            out = QgsFeature(fields)
            out.setGeometry(f.geometry())
            out.setAttributes(f.attributes() + ([text or None, n, filled(values)] if add_number else [text or None]))
            sink.addFeature(out, QgsFeatureSink.FastInsert)
            distinct[text] += 1
            feedback.setProgress(100 * i / total)
        feedback.pushInfo(f"{len(distinct)} different combinations; the most common:")
        for text, n in distinct.most_common(10):
            feedback.pushInfo(f"  {n:6}  {text or '(no values)'}")
        return {self.OUTPUT: dest}
