# SemanticGIS plugin repository

QGIS plugin repositories (update channels) for the SemanticGIS plugin. Files on this branch are
written by `scripts/release.py`; do not edit them by hand.

| Channel | For | Add this address in QGIS |
| --- | --- | --- |
| stable | students and everyday use | `https://raw.githubusercontent.com/Esbern/semanticGIS-qgis/plugin-repository/stable.xml` |
| dev | trying prototypes (versions like 0.8.0-dev1) | `https://raw.githubusercontent.com/Esbern/semanticGIS-qgis/plugin-repository/dev.xml` |

In QGIS: *Plugins → Manage and Install Plugins… → Settings → Add…*, give it a name and paste the
address. The plugin also adds its own channel the first time it starts.
