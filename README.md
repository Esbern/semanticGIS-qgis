# SemanticGIS for QGIS

A QGIS plugin for browsing the [SemanticGIS](https://semanticgis.org) data network and adding its services as layers.

SemanticGIS (the SPHERE protocol) organises geospatial data as a network rather than a flat catalogue:

- **Spheres** are thematic hubs: Atmosphere, Biosphere, Hydrosphere, Geosphere and Anthroposphere, plus a non-thematic Reference Framework.
- **Twigs** are narrower hubs within a sphere, such as Planning or Groundwater.
- **Leaves** are the questions you can ask of the data, such as *"Which nature protection designations apply here?"*. A leaf links to every twig it is relevant to.
- **Datasets** realise a leaf, and their **services** (WFS, WMS, WMTS) are what this plugin loads.

## Features

- **Browse** from seven entry points, the same as on the website:
  - **Classical Classifications:** INSPIRE, ISO 19115 and UN-GGIM themes and the twigs they land in.
  - **Collection Methods:** register, field measurement, passive or active remote sensing, modelled, volunteered and cartographic, with the realisations of each.
  - **Datasets by Collection:** the Grunddatamodellen registers. These open their documentation pages, since they are accessed through GraphQL and file downloads.
  - **Datasets by Owner:** every harvested dataset with services, by publishing organisation.
  - **SPHERE:** the thematic spheres, then twig, leaf, dataset and service. A ★ marks leaves whose primary lens is that twig.
  - **Reference Framework:** coordinate systems and the reference units that other data refers to by ID: administrative units, statistical units and addresses. Their geometries, such as NUTS and LAU, can be loaded at each scale.
  - **Basemaps:** topographic maps, imagery, historical maps and terrain. The Danish ones are Skærmkort, the orthophotos, the målebordsblade and the DHM shaded relief; the international ones are OpenStreetMap, OpenTopoMap, CARTO, Esri and EOX Sentinel-2 cloudless. Double-click adds a basemap at the bottom of the layer tree, with its attribution set; right-click opens its licence. Google tiles are not included, because Google's terms forbid direct tile use.
- **Search** leaves (by title or by the question they answer) and datasets (by title).
- **Add a layer** by double-clicking a service. WFS, WMS and WMTS services are supported. By default, WFS layers fetch only the features in the current map view.
- **Preferred services.** When a dataset is served by several providers, its services are listed in the order the knowledge base ranks them: Datafordeleren before Dataforsyningen, unless a dataset note says otherwise. The first verified service is marked ★, and double-clicking the dataset adds it.
- **Verified services only** (on by default) hides services that failed the last capabilities check. That check confirms each layer still exists on its server.
- **Provenance tags.** Every added layer is grouped under *SemanticGIS › <leaf>* (or the owner, when browsed by owner) and tagged with custom properties (`semanticgis/leaf`, `semanticgis/dataset`, `semanticgis/service`). A saved project therefore still knows where each layer came from.
- **Show web page:** every node in the tree, from the six top-level folders down to services and geometries, has *Show web page* on its right-click menu. It opens the matching page on semanticgis.org; a service opens its dataset's page. The same menu adds layers, opens download links, or copies an endpoint URL.
- **Works offline** from the last catalogue it loaded.

### Joining a table to reference units

Statistics are usually published per unit ID, without geometry: population per municipality, an indicator per NUTS region. **Join a table to reference units…** gives such a table its geometry.

1. **Load the table** into QGIS, for example a CSV export from Danmarks Statistik's Statbank or from Eurostat.
2. **Choose the table and press *Detect join*.** Every column is matched against the reference units in the catalogue (currently NUTS 2024 and LAU 2024 from Eurostat GISCO). The plugin recognises:
   - full IDs, such as `DK011` and `DK_671`;
   - national codes in any format, such as `101`, `0101` and `101 København`, read for the country you set;
   - names, as a last resort.

   The best matches are listed with their match rate and examples of unmatched values, such as national totals.
3. **Choose a scale and press *Join*.** The same unit ID has geometries at several scales: NUTS comes at 1:1M, 1:3M, 1:10M, 1:20M and 1:60M. Only the geometries of the matched units are fetched, by range requests on the remote GeoPackage. Denmark's 99 municipalities come out of the 78 MB European LAU file in a few seconds. Fetched units are cached in the QGIS profile.

The result is a new layer under *SemanticGIS › Joins*. It contains every table row that matched, its attributes, the unit's ID and name, and the geometry, tagged with the join's provenance (`semanticgis/join_*`).

The reference units, their ID schemes and their geometries per scale are part of the catalogue: they come from the `reference_units` field of the reference realisations in the SemanticGIS knowledge base.

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
| Dataforsyningen token | — | Needed for Dataforsyningen services ([get one here](https://dataforsyningen.dk)) |
| Datafordeleren API key | — | Needed for Datafordeleren services on wms./wmts./wfs.datafordeler.dk (username/password service users are deprecated) |

The catalogue never contains credentials: the plugin adds your own token or API key to the service URL when it loads a layer. They are therefore saved in project files that contain those layers.

**QGIS 4 and Dataforsyningen.** Over HTTP/2, Dataforsyningen's API gateway answers Qt 6 with a repeated `Content-Encoding` header, and QGIS 4 then cannot read the reply, so every Dataforsyningen layer fails. While the plugin is loaded it sends requests to `dataforsyningen.dk` and `datafordeler.dk` over HTTP/1.1, which works. On first start it also clears QGIS's network cache once, to remove broken replies.

## Where the catalogue comes from

The plugin reads two published JSON files:

- `sphere-index.v1.json`: spheres, twigs, leaves and threads.
- `services.v1.json`: the datasets behind each leaf, with their WFS/WMS/WMTS and download endpoints. Each service carries the result of a live GetCapabilities check: its status, the layer name as the server knows it, its CRS and its tile matrix sets.

Both files are produced from the SemanticGIS knowledge base, which is maintained separately. The plugin contains no data of its own.

## Development

The catalogue and URI-building code (`catalogue.py`, `layers.py`) has no GUI dependencies. It can be tested with QGIS's Python, without starting QGIS:

```sh
# Matching core of the auto-join (plain Python, no QGIS needed)
python3 tests/test_join_core.py

# Detect and build joins for semicolon-separated CSVs (e.g. Statbank exports)
SEMANTICGIS_CATALOGUE=<folder or URL> python tests/join_live.py table.csv

# Every node's web page exists on the site
QT_QPA_PLATFORM=offscreen SEMANTICGIS_CATALOGUE=<folder or URL> python tests/page_links.py

# Offscreen test of the join dialog
QT_QPA_PLATFORM=offscreen SEMANTICGIS_CATALOGUE=<folder or URL> python tests/join_dialog_smoke.py table.csv

# Build real layers from a sample of verified services (5 per type)
SEMANTICGIS_CATALOGUE=<folder or URL> python tests/live_layers.py 5

# The panel survives being tabbed with another dock
QT_QPA_PLATFORM=offscreen SEMANTICGIS_CATALOGUE=<folder or URL> python tests/dock_tabs.py

# Offscreen test of the dock panel
QT_QPA_PLATFORM=offscreen SEMANTICGIS_CATALOGUE=<folder or URL> python tests/dock_smoke.py
```

On macOS, QGIS's bundled Python needs the following environment:

```sh
export PYTHONHOME=/Applications/QGIS.app/Contents/Resources
R=$PYTHONHOME/python3.12; export PYTHONPATH=$R:$R/site-packages:$R/lib-dynload
export PROJ_DATA=$PYTHONHOME/qgis/proj GDAL_DATA=$PYTHONHOME/qgis/gdal QGIS_PLUGINPATH=/Applications/QGIS.app/Contents/PlugIns/qgis
/Applications/QGIS.app/Contents/MacOS/python3.12 tests/live_layers.py
```

## Roadmap

- Entity downloads: save a layer, optionally clipped to the map view, into a local GeoPackage.
- Full-resolution Danish units (DAGI) as a join target, once their geometry URLs are in the catalogue.
- Joining directly from catalogue realisations that declare `anchored_by`, such as Danmarks Statistik tables.
- A filter by collection method (register, in-situ, remote sensing and so on).
- Datafordeler GraphQL realisations, perhaps later.

## Licence

GPL-2.0-or-later, the same licence as QGIS.
