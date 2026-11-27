"""Building the joined layer in QGIS: table rows + reference geometry (main thread only)."""

from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFields,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QMetaType

from .layers import PROPERTY_PREFIX, ROOT_GROUP, ensure_project_crs

# QgsField takes QMetaType from QGIS 3.38; older 3.x versions need QVariant.
try:
    STRING_TYPE = QMetaType.Type.QString
    QgsField("probe", STRING_TYPE)
except (AttributeError, TypeError):
    from qgis.PyQt.QtCore import QVariant

    STRING_TYPE = QVariant.String

SAMPLE_FEATURES = 5000
JOIN_GROUP = "Joins"


def table_columns(layer, limit=SAMPLE_FEATURES):
    """Distinct values per field from the first `limit` features (enough to detect the join)."""
    names = [f.name() for f in layer.fields()]
    columns = {name: {} for name in names}
    for i, feature in enumerate(layer.getFeatures()):
        if i >= limit:
            break
        for name in names:
            value = feature[name]
            if value is not None and str(value) != "NULL":
                columns[name][str(value)] = None
    return {name: list(values) for name, values in columns.items() if values}


def matched_units(layer, candidate):
    """The reference units the table's rows match, keyed by their ID."""
    units = {}
    for feature in layer.getFeatures():
        unit = candidate.lookup(feature[candidate.field])
        if unit is not None:
            units[unit.id] = unit
    return units


def build_joined_layer(table, candidate, geometry_path, geometry_scale):
    """A memory layer with every matched table row, its attributes and the unit's geometry.

    Returns (layer, matched_rows, unmatched_values).
    """
    source = candidate.source
    reference = QgsVectorLayer(geometry_path, "reference", "ogr")
    if not reference.isValid():
        raise RuntimeError(f"Could not open the reference geometries ({geometry_path})")
    geometries = {str(f[source.id_scheme]): f.geometry() for f in reference.getFeatures()}

    fields = QgsFields()
    for f in table.fields():
        fields.append(QgsField(f))
    taken = {f.name() for f in table.fields()}
    extra = {}
    for name, label in (("ref_id", source.id_scheme), ("ref_name", "name")):
        unique = name
        while unique in taken:
            unique = "_" + unique
        extra[name] = unique
        fields.append(QgsField(unique, STRING_TYPE, comment=f"Reference unit {label}"))

    geometry_type = QgsWkbTypes.displayString(reference.wkbType()) or "MultiPolygon"
    name = f"{table.name()} — {source.unit} {geometry_scale}"
    joined = QgsVectorLayer(f"{geometry_type}?crs={reference.crs().authid()}", name, "memory")
    provider = joined.dataProvider()
    provider.addAttributes(fields.toList())
    joined.updateFields()

    features, matched, unmatched = [], 0, {}
    for row in table.getFeatures():
        value = row[candidate.field]
        unit = candidate.lookup(value)
        geometry = geometries.get(unit.id) if unit else None
        if geometry is None:
            unmatched[str(value)] = None
            continue
        feature = QgsFeature(joined.fields())
        for f in table.fields():
            feature[f.name()] = row[f.name()]
        feature[extra["ref_id"]] = unit.id
        feature[extra["ref_name"]] = unit.name
        feature.setGeometry(geometry)
        features.append(feature)
        matched += 1
    provider.addFeatures(features)
    joined.updateExtents()

    for key, value in {
        "join_table": table.name(),
        "join_field": candidate.field,
        "join_mode": candidate.mode,
        "join_reference": source.realisation,
        "join_id_scheme": source.id_scheme,
        "join_scale": geometry_scale,
        "leaf": source.leaf or "",
    }.items():
        joined.setCustomProperty(PROPERTY_PREFIX + key, value)
    return joined, matched, [v for v in unmatched if v not in ("", "NULL")]


def add_joined_layer(layer):
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    group = root.findGroup(ROOT_GROUP) or root.insertGroup(0, ROOT_GROUP)
    joins = group.findGroup(JOIN_GROUP) or group.insertGroup(0, JOIN_GROUP)
    ensure_project_crs(project, layer)
    project.addMapLayer(layer, False)
    joins.insertLayer(0, layer)


try:
    DATE_TYPE = QMetaType.Type.QDate
    QgsField("probe", DATE_TYPE)
except (AttributeError, TypeError):
    from qgis.PyQt.QtCore import QVariant

    DATE_TYPE = QVariant.Date


def build_dated_layer(table, candidate, versions_path, version_of_row, label, period_field=None):
    """Like build_joined_layer, with the units as they were: each row gets the geometry of the version of its unit that
    `version_of_row(row, unit)` names (a timeslice.UnitVersion, from the cache at `versions_path`). With `period_field`
    (time kept as rows) each row also gets the dates of its period, and the layer is set up for the temporal controller."""
    from qgis.core import QgsFeatureRequest, QgsVectorLayerTemporalProperties
    from qgis.PyQt.QtCore import QDate

    from .timeslice import period_range

    source = candidate.source
    reference = QgsVectorLayer(f"{versions_path}|layername=versions", "versions", "ogr")
    if not reference.isValid():
        raise RuntimeError(f"Could not open the units' versions ({versions_path})")
    wanted, per_row = set(), []
    for row in table.getFeatures():
        unit = candidate.lookup(row[candidate.field])
        version = version_of_row(row, unit) if unit else None
        per_row.append((row, unit, version))
        if version:
            wanted.add((version.code, version.stamp))
    codes = sorted({c for c, _ in wanted})
    geometries = {}
    if codes:
        expr = '"code" IN (' + ", ".join("'" + c.replace("'", "''") + "'" for c in codes) + ")"
        for f in reference.getFeatures(QgsFeatureRequest().setFilterExpression(expr)):
            k = (str(f["code"]), str(f["stamp"]))
            if k in wanted:
                geometries[k] = f.geometry()

    fields = QgsFields()
    for f in table.fields():
        fields.append(QgsField(f))
    taken = {f.name() for f in table.fields()}
    extra = {}
    specs = [("ref_id", STRING_TYPE, f"Reference unit {source.id_scheme}"), ("ref_name", STRING_TYPE, "Reference unit name"),
             ("ref_from", DATE_TYPE, "The unit's version valid from"), ("ref_to", DATE_TYPE, "The unit's version valid until (the first day it was not)")]
    if period_field:
        specs += [("period_from", DATE_TYPE, "First day of the row's period"), ("period_to", DATE_TYPE, "First day after the row's period")]
    for name, kind, comment in specs:
        unique = name
        while unique in taken:
            unique = "_" + unique
        extra[name] = unique
        fields.append(QgsField(unique, kind, comment=comment))

    geometry_type = QgsWkbTypes.displayString(reference.wkbType()) or "MultiPolygon"
    joined = QgsVectorLayer(f"{geometry_type}?crs={reference.crs().authid()}", f"{table.name()} — {source.unit} {label}", "memory")
    provider = joined.dataProvider()
    provider.addAttributes(fields.toList())
    joined.updateFields()
    qdate = lambda d: QDate(d.year, d.month, d.day) if d else None

    features, unmatched = [], {}
    for row, unit, version in per_row:
        geometry = geometries.get((version.code, version.stamp)) if version else None
        if geometry is None:
            unmatched[str(row[candidate.field])] = None
            continue
        feature = QgsFeature(joined.fields())
        for f in table.fields():
            feature[f.name()] = row[f.name()]
        feature[extra["ref_id"]] = version.code
        feature[extra["ref_name"]] = version.name or unit.name
        feature[extra["ref_from"]] = qdate(version.valid_from)
        feature[extra["ref_to"]] = qdate(version.valid_to)
        if period_field:
            r = period_range(row[period_field])
            if r:
                feature[extra["period_from"]], feature[extra["period_to"]] = qdate(r[0]), qdate(r[1])
        feature.setGeometry(geometry)
        features.append(feature)
    provider.addFeatures(features)
    joined.updateExtents()

    if period_field:
        tp = joined.temporalProperties()
        tp.setMode(QgsVectorLayerTemporalProperties.TemporalMode.ModeFeatureDateTimeStartAndEndFromFields)
        tp.setStartField(extra["period_from"])
        tp.setEndField(extra["period_to"])
        tp.setIsActive(True)
    for key, value in {"join_table": table.name(), "join_field": candidate.field, "join_mode": candidate.mode,
                       "join_reference": source.realisation, "join_id_scheme": source.id_scheme, "join_scale": "full detail",
                       "join_units_as_of": label, "leaf": source.leaf or ""}.items():
        joined.setCustomProperty(PROPERTY_PREFIX + key, value)
    return joined, len(features), [v for v in unmatched if v not in ("", "NULL")]
