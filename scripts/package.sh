#!/bin/sh
# Build an installable zip: dist/semanticgis-<version>.zip (QGIS > Plugins > Install from ZIP)
set -e
cd "$(dirname "$0")/.."
version=$(sed -n 's/^version=//p' semanticgis/metadata.txt)
mkdir -p dist
rm -f "dist/semanticgis-$version.zip"
zip -qr "dist/semanticgis-$version.zip" semanticgis -x "*/__pycache__/*" "*.pyc"
echo "dist/semanticgis-$version.zip"
