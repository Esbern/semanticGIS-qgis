# SemanticGIS for QGIS

A QGIS plugin for browsing the [SemanticGIS](https://semanticgis.org) data network and adding its services as layers.

SemanticGIS (the SPHERE protocol) organises geospatial data as a network rather than a flat catalogue:

- **Spheres** are thematic hubs: Atmosphere, Biosphere, Hydrosphere, Geosphere and Anthroposphere, plus a non-thematic Reference Framework.
- **Twigs** are narrower hubs within a sphere, such as Planning or Groundwater.
- **Leaves** are the questions you can ask of the data, such as *"Which nature protection designations apply here?"*. A leaf links to every twig it is relevant to.
- **Datasets** realise a leaf, and their **services** (WFS, WMS, WMTS) are what this plugin loads.

## Features

- **Browse** the network: sphere → twig → leaf → dataset → service. A ★ marks the leaves that have that twig as their primary lens.
- **Search** leaves by title or by the question they answer.
- **Add a layer** by double-clicking a service. WFS, WMS and WMTS services are supported. By default, WFS layers fetch only the features in the current map view.
- **Verified services only** (on by default) hides services that failed the last capabilities check. That check confirms each layer still exists on its server.
- **Provenance tags.** Every added layer is grouped under *SemanticGIS › <leaf>* and tagged with custom properties (`semanticgis/leaf`, `semanticgis/dataset`, `semanticgis/service`). A saved project therefore still knows where each layer came from.
- **Context menu:** open the leaf or dataset page on semanticgis.org, open download links, or copy an endpoint URL.
- **Works offline** from the last catalogue it loaded.

## Installation

The plugin is not yet in the official QGIS plugin repository. To install it:

1. Download or build `semanticgis-<version>.zip` (run `scripts/package.sh`).
2. In QGIS, open **Plugins › Manage and Install Plugins › Install from ZIP**.
3. Open the panel from **Web › SemanticGIS**.

The plugin requires QGIS 3.34 or later, and runs on both QGIS 3 (Qt5) and QGIS 4 (Qt6).

## Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| Catalogue source | `https://semanticgis.org/Data/assets/` | Base URL or local folder holding `sphere-index.v1.json` and `services.v1.json` |
| Documentation site | `https://semanticgis.org/Data` | Used to open leaf and dataset pages |
| Dataforsyningen token | — | Needed for Dataforsyningen services ([get one here](https://dataforsyningen.dk)). The token is added to the layer's URL, so it is saved in project files that contain those layers. |

## Where the catalogue comes from

The plugin reads two published JSON files:

- `sphere-index.v1.json`: spheres, twigs, leaves and threads.
- `services.v1.json`: the datasets behind each leaf, with their WFS/WMS/WMTS and download endpoints. Each service carries the result of a live GetCapabilities check: its status, the layer name as the server knows it, its CRS and its tile matrix sets.

Both files are produced from the SemanticGIS knowledge base, which is maintained separately. The plugin contains no data of its own.

## Development

The catalogue and URI-building code (`catalogue.py`, `layers.py`) has no GUI dependencies. It can be tested with QGIS's Python, without starting QGIS:

```sh
# Build real layers from a sample of verified services (5 per type)
SEMANTICGIS_CATALOGUE=<folder or URL> python tests/live_layers.py 5

# Offscreen test of the dock panel
QT_QPA_PLATFORM=offscreen SEMANTICGIS_CATALOGUE=<folder or URL> python tests/dock_smoke.py
```

On macOS, QGIS's bundled Python needs the following environment:

```sh
export PYTHONHOME=/Applications/QGIS.app/Contents/Resources
R=$PYTHONHOME/python3.12; export PYTHONPATH=$R:$R/site-packages:$R/lib-dynload
export PROJ_DATA=$PYTHONHOME/qgis/proj QGIS_PLUGINPATH=/Applications/QGIS.app/Contents/PlugIns/qgis
/Applications/QGIS.app/Contents/MacOS/python3.12 tests/live_layers.py
```

## Roadmap

- Entity downloads: save a layer, optionally clipped to the map view, into a local GeoPackage.
- A filter by collection method (register, in-situ, remote sensing and so on).
- Datafordeler GraphQL realisations, perhaps later.

## Licence

GPL-2.0-or-later, the same licence as QGIS.
