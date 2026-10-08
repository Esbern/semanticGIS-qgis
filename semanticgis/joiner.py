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

from .layers import PROPERTY_PREFIX, ROOT_GROUP

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
    project.addMapLayer(layer, False)
    joins.insertLayer(0, layer)
