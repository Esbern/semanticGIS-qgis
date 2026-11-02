"""Field types across QGIS versions: QgsField takes QMetaType from QGIS 3.38; older 3.x versions need QVariant."""
from qgis.core import QgsField
from qgis.PyQt.QtCore import QMetaType

try:
    INTEGER64, DOUBLE, STRING = QMetaType.Type.LongLong, QMetaType.Type.Double, QMetaType.Type.QString
    QgsField("probe", INTEGER64)
except (AttributeError, TypeError):
    from qgis.PyQt.QtCore import QVariant

    INTEGER64, DOUBLE, STRING = QVariant.LongLong, QVariant.Double, QVariant.String
