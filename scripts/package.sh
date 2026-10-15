#!/bin/sh
# Build an installable zip: dist/semanticgis.<version>.zip (QGIS > Plugins > Install from ZIP).
# The name must start with "semanticgis." : QGIS's plugin installer takes the plugin's id from the
# file name up to the first dot, so "semanticgis-0.7.2.zip" would look like another plugin.
set -e
cd "$(dirname "$0")/.."
version=$(sed -n 's/^version=//p' semanticgis/metadata.txt)
mkdir -p dist
rm -f "dist/semanticgis.$version.zip"
zip -qr "dist/semanticgis.$version.zip" semanticgis -x "*/__pycache__/*" "*.pyc"
echo "dist/semanticgis.$version.zip"
