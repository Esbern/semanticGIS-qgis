"""Renumber feature ids: give every feature a unique fid (1, 2, 3, …) so the layer can be saved as a GeoPackage.

Tools that split or copy features (Multipart to singleparts, Explode lines, Buffer of a GeoPackage layer …)
keep each feature's old fid as an ordinary field. Copies then share a fid, and saving the result to a
GeoPackage fails with "UNIQUE constraint failed" or "failed to create feature", because a GeoPackage uses
that field as its feature id. This tool writes fresh numbers into the fid field (adding one if the layer
has none) and can keep the old numbers in another field.
"""
from qgis.core import (
    QgsFeature, QgsFeatureSink, QgsField, QgsFields, QgsProcessingAlgorithm, QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink, QgsProcessingParameterFeatureSource,
)

from .compat import INTEGER64

FID = "fid"
OLD = "old_fid"


class RenumberFid(QgsProcessingAlgorithm):
    def name(self): return "renumberfid"
    def displayName(self): return "Renumber feature ids (fid)"
    def group(self): return "Vector table"
    def groupId(self): return "vectortable"
    def tags(self): return ["fid", "unique", "id", "geopackage", "gpkg", "duplicate", "constraint", "renumber", "row_number"]
    def createInstance(self): return RenumberFid()

    def shortHelpString(self):
        return ("Gives every feature a unique fid (1, 2, 3, …), so the layer can be saved as a GeoPackage.\n\n"
                "Use it when saving fails with 'UNIQUE constraint failed' or 'failed to create feature'. That happens "
                "after tools such as Multipart to singleparts, which copy a feature's old fid to each of its parts.\n\n"
                "The features keep their order and all other fields. Tick 'Keep the old fid' to keep the previous "
                "numbers in a field old_fid, for example to join results back to the original layer.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource("INPUT", "Input layer"))
        self.addParameter(QgsProcessingParameterBoolean("KEEP_OLD", f"Keep the old fid in a field '{OLD}'", defaultValue=False))
        self.addParameter(QgsProcessingParameterFeatureSink("OUTPUT", "Renumbered"))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, "INPUT", context)
        keep_old = self.parameterAsBool(parameters, "KEEP_OLD", context)
        names = source.fields().names()
        fid_index = next((i for i, n in enumerate(names) if n.lower() == FID), -1)

        fields = QgsFields()
        if fid_index < 0:
            fields.append(QgsField(FID, INTEGER64))
        for i, field in enumerate(source.fields()):
            if i == fid_index:
                fields.append(QgsField(field.name(), INTEGER64))
                if keep_old:
                    fields.append(QgsField(OLD, INTEGER64))
            elif not (keep_old and field.name().lower() == OLD):
                fields.append(field)

        sink, dest = self.parameterAsSink(parameters, "OUTPUT", context, fields, source.wkbType(), source.sourceCrs())
        total = max(source.featureCount(), 1)
        duplicates, seen = 0, set()
        for n, feature in enumerate(source.getFeatures(), 1):
            if feedback.isCanceled():
                break
            values = [] if fid_index >= 0 else [n]
            for i, value in enumerate(feature.attributes()):
                if i == fid_index:
                    duplicates += value in seen
                    seen.add(value)
                    values.append(n)
                    if keep_old:
                        values.append(value)
                elif not (keep_old and names[i].lower() == OLD):
                    values.append(value)
            out = QgsFeature(fields)
            out.setGeometry(feature.geometry())
            out.setAttributes(values)
            sink.addFeature(out, QgsFeatureSink.FastInsert)
            feedback.setProgress(100 * n / total)
        if fid_index >= 0:
            feedback.pushInfo(f"{duplicates} feature(s) had a fid already used by another feature; all are now unique.")
        else:
            feedback.pushInfo("The layer had no fid field; one was added.")
        return {"OUTPUT": dest}
