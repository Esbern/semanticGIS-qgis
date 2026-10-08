"""Attribution on the map: the credit line is stored on each layer SemanticGIS adds, and the
lines of a project's layers can be collected for a print layout.

The line is written to three places on the layer: the layer's attribution (shown by QGIS Server
and some exports), its metadata rights (read by the layout expression map_credits()) and a
SemanticGIS custom property (read by "Copy attributions for the map").
"""

from .layers import PROPERTY_PREFIX

# A layout label that follows the layers of a map item (QGIS >= 3.20); 'Map 1' is the item's id.
LAYOUT_EXPRESSION = "[% array_to_string(map_credits('Map 1'), '\\n') %]"

STATUS_NOTE = {
    "verified": "",
    "default": "standard wording; the publisher's own terms have not been checked",
    "unknown": "the publisher is not recorded; find it in the metadata",
}


def stamp(layer, attribution, service_type):
    """Store the attribution line for this service type on the layer. Main thread only."""
    line = attribution.line(service_type) if attribution else ""
    layer.setCustomProperty(PROPERTY_PREFIX + "attribution", line)
    layer.setCustomProperty(PROPERTY_PREFIX + "attribution_status", attribution.status if attribution else "unknown")
    if not line:
        return
    try:
        layer.serverProperties().setAttribution(line)   # QGIS >= 3.38
    except AttributeError:
        layer.setAttribution(line)
    metadata = layer.metadata()
    metadata.setRights([line])
    layer.setMetadata(metadata)


def layer_attribution(layer, catalogue=None):
    """The attribution line of a layer: the one SemanticGIS stored, else one derived from the
    catalogue (layers added by older plugin versions), else the layer's own attribution or rights."""
    line = layer.customProperty(PROPERTY_PREFIX + "attribution") or ""
    if line:
        return line
    service_id = layer.customProperty(PROPERTY_PREFIX + "service") or ""
    service_type = service_id.split("|", 1)[0] or None
    if catalogue is not None:
        dataset = catalogue.datasets.get(layer.customProperty(PROPERTY_PREFIX + "dataset") or "")
        basemap_id = layer.customProperty(PROPERTY_PREFIX + "basemap") or ""
        basemap = next((b for b in catalogue.basemaps if b.id == basemap_id), None) if basemap_id else None
        source = dataset or basemap
        if source is not None and source.attribution:
            return source.attribution.line(service_type)
    try:
        own = layer.serverProperties().attribution()
    except AttributeError:
        own = layer.attribution() if hasattr(layer, "attribution") else ""
    return own or "; ".join(layer.metadata().rights())


def map_attributions(layers, catalogue=None):
    """Unique attribution lines of the layers, in layer order, and the names of layers without one."""
    lines, missing = [], []
    for layer in layers:
        line = layer_attribution(layer, catalogue)
        if not line:
            missing.append(layer.name())
        elif line not in lines:
            lines.append(line)
    return lines, missing


def copy_layer_attributions(iface, layers=None, catalogue=None):
    """Copy the unique attribution lines of the layers (default: all project layers) to the
    clipboard and report layers without one in the message bar. Main thread only."""
    from qgis.core import Qgis, QgsProject
    from qgis.PyQt.QtWidgets import QApplication

    if layers is None:
        layers = [node.layer() for node in QgsProject.instance().layerTreeRoot().findLayers() if node.layer()]
    lines, missing = map_attributions(layers, catalogue)
    bar = iface.messageBar()
    if not lines:
        bar.pushMessage("SemanticGIS", "None of the layers has an attribution recorded.", Qgis.MessageLevel.Warning, 6)
        return lines
    QApplication.clipboard().setText("\n".join(lines))
    text = f"Copied {len(lines)} attribution line(s); paste them into a label in the print layout."
    if missing:
        shown = ", ".join(missing[:5]) + (" …" if len(missing) > 5 else "")
        text += f" No attribution recorded for: {shown}."
    bar.pushMessage("SemanticGIS", text, Qgis.MessageLevel.Warning if missing else Qgis.MessageLevel.Success, 10)
    return lines
