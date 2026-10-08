"""Keys per access profile: stored (plain and encrypted), migrated from 0.6 settings, and added to
requests as they are sent, never to layer sources or saved projects. Runs in a throw-away QGIS
settings folder and authentication database. The live part runs when DATAFORDELER_API_KEY is set.

  SEMANTICGIS_CATALOGUE=<folder or URL> [DATAFORDELER_API_KEY=…] python tests/access_keys.py
"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
scratch = tempfile.mkdtemp(prefix="sgis-access-")
os.environ["QGIS_AUTH_DB_DIR_PATH"] = scratch
from qgis.PyQt.QtCore import QCoreApplication, QSettings, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY, QgsProject,
                       QgsSettings)
QCoreApplication.setOrganizationName("SemanticGISTest")
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"):
    QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], False); app.initQgis()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, scratch)
assert QgsSettings().fileName().startswith(scratch), QgsSettings().fileName()

from semanticgis import network; network.install()
from semanticgis.access import KEY_PREFIX, KeyStore
from semanticgis.catalogue import load_catalogue

failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


def sent(store, url):
    request = QNetworkRequest(QUrl(url))
    store.preprocess(request)
    return request.url().toString()


catalogue, _ = load_catalogue(os.environ["SEMANTICGIS_CATALOGUE"])
expect("profiles in catalogue", sorted(catalogue.access_profiles), ["carto", "datafordeler", "dataforsyningen"])

# 0.6 settings move to the per-profile keys.
QgsSettings().setValue("semanticgis/datafordeler_api_key", "legacy-df")
store = KeyStore(); store.set_profiles(catalogue.access_profiles); store.load(prompt=False)
expect("legacy key migrated", store.key("datafordeler"), "legacy-df")
expect("legacy setting removed", QgsSettings().value("semanticgis/datafordeler_api_key", ""), "")

store.save({"datafordeler": "DFKEY", "carto": "CARTOKEY"}, encrypted=False)
expect("plain store", QgsSettings().value(KEY_PREFIX + "carto", ""), "CARTOKEY")

# Request time: the matching profile's key is added, once, and only to its own hosts.
expect("datafordeler request", sent(store, "https://wcs.datafordeler.dk/DHM/DHM_Terraen/1.0.0/WCS?service=WCS"),
       "https://wcs.datafordeler.dk/DHM/DHM_Terraen/1.0.0/WCS?service=WCS&apikey=DFKEY")
expect("carto tile", sent(store, "https://basemaps.cartocdn.com/light_all/5/16/9.png"),
       "https://basemaps.cartocdn.com/light_all/5/16/9.png?key=CARTOKEY")
expect("key already in URL", sent(store, "https://wms.datafordeler.dk/x?apikey=OLD"), "https://wms.datafordeler.dk/x?apikey=OLD")
expect("no key set (dataforsyningen)", sent(store, "https://api.dataforsyningen.dk/wms"), "https://api.dataforsyningen.dk/wms")
expect("other host", sent(store, "https://tile.openstreetmap.org/1/1/1.png"), "https://tile.openstreetmap.org/1/1/1.png")
expect("look-alike host", sent(store, "https://notdatafordeler.dk/x"), "https://notdatafordeler.dk/x")

carto = next(b for b in catalogue.basemaps if b.id == "carto-positron").services[0]
expect("carto service profile", carto.access, "carto")
store.save({"carto": ""}, encrypted=False)
expect("missing key message", (store.missing(carto) or "").startswith("This service needs your own CARTO basemaps API key"), True)
expect("other keys kept", store.key("datafordeler"), "DFKEY")

# Encrypted: nothing readable in the settings file; a new session needs the master password.
manager = QgsApplication.authManager()
manager.setMasterPassword("test-master-password", True)
store.save({"datafordeler": "SECRETKEY"}, encrypted=True)
expect("encrypted: not in settings", QgsSettings().value(KEY_PREFIX + "datafordeler", ""), "")
expect("encrypted: not in auth db as text", b"SECRETKEY" in open(os.path.join(scratch, "qgis-auth.db"), "rb").read(), False)
again = KeyStore(); again.set_profiles(catalogue.access_profiles)
expect("encrypted: read back", (again.load(prompt=False), again.key("datafordeler")), (True, "SECRETKEY"))
manager.clearMasterPassword()
locked = KeyStore(); locked.set_profiles(catalogue.access_profiles)
expect("encrypted: locked without master password", (locked.load(prompt=False), locked.loaded), (False, False))

# Live: the DHM WCS through the request-time key, and a saved project without the key.
real_key = os.environ.get("DATAFORDELER_API_KEY")
if real_key:
    from semanticgis.access import KEYS
    from semanticgis.layers import build_layer, place_layer
    manager.setMasterPassword("test-master-password", True)
    KEYS.set_profiles(catalogue.access_profiles)
    KEYS.save({"datafordeler": real_key}, encrypted=False)
    KEYS.install()
    dataset, service = next((d, s) for d in catalogue.datasets.values() for s in d.services
                            if s.type == "wcs" and s.layer_name == "dhm_terraen")
    layer = place_layer(build_layer(service, dataset, True, "EPSG:25832"), service, None, dataset)
    to_layer = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), layer.crs(), QgsProject.instance())
    value, ok = layer.dataProvider().sample(to_layer.transform(QgsPointXY(9.68397, 56.10094)), 1)
    expect("live WCS Himmelbjerget ~147 m", ok and 140 < value < 155, True)
    expect("key not in layer source", real_key in layer.source(), False)
    project_file = os.path.join(scratch, "project.qgs")
    QgsProject.instance().write(project_file)
    expect("key not in saved project", real_key in open(project_file, encoding="utf8").read(), False)
    expect("layer records its profile", layer.customProperty("semanticgis/access"), "datafordeler")
    KEYS.uninstall()
else:
    print("skip live part: DATAFORDELER_API_KEY not set")

app.exitQgis()
sys.exit(1 if failures else 0)
