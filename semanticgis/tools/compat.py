"""Field types across QGIS versions: QgsField takes QMetaType from QGIS 3.38; older 3.x versions need QVariant."""
from qgis.core import QgsField
from qgis.PyQt.QtCore import QMetaType

try:
    INTEGER64, DOUBLE, STRING = QMetaType.Type.LongLong, QMetaType.Type.Double, QMetaType.Type.QString
    QgsField("probe", INTEGER64)
except (AttributeError, TypeError):
    from qgis.PyQt.QtCore import QVariant

    INTEGER64, DOUBLE, STRING = QVariant.LongLong, QVariant.Double, QVariant.String

from qgis.core import Qgis, QgsProcessingParameterDefinition

try:
    ADVANCED = Qgis.ProcessingParameterFlag.Advanced
except AttributeError:  # QGIS < 3.36
    ADVANCED = QgsProcessingParameterDefinition.FlagAdvanced


def advanced(parameter):
    """Show the parameter under Advanced parameters, folded away from beginners."""
    parameter.setFlags(parameter.flags() | ADVANCED)
    return parameter


def save_default_style(layer, name, description):
    """Store the layer's style in its GeoPackage as the default (saveStyleToDatabaseV2 from QGIS 3.40 / 4)."""
    if hasattr(layer, "saveStyleToDatabaseV2"):
        layer.saveStyleToDatabaseV2(name, description, True, "")
    else:
        layer.saveStyleToDatabase(name, description, True, "")


def set_creation_options(writer, options):
    if hasattr(writer, "setCreationOptions"):   # QGIS 3.44+
        writer.setCreationOptions(options)
    else:
        writer.setCreateOptions(options)
