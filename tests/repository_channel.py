"""The plugin adds its update channel to QGIS's plugin repositories once, the dev channel for
prototype versions, and leaves a removed entry removed. Throw-away QGIS settings folder.

  python tests/repository_channel.py
"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
scratch = tempfile.mkdtemp(prefix="sgis-repo-")
from qgis.PyQt.QtCore import QCoreApplication, QSettings
from qgis.core import QgsApplication, QgsSettings
QCoreApplication.setOrganizationName("SemanticGISTest")
app = QgsApplication([], False); app.initQgis()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, scratch)
assert QgsSettings().fileName().startswith(scratch)

from semanticgis.repository import CHANNELS, QGIS_REPOSITORIES, channel_of, register

failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


def repositories():
    settings = QgsSettings()
    settings.beginGroup(QGIS_REPOSITORIES)
    found = {key: settings.value(f"{key}/url", "") for key in settings.childGroups()}
    settings.endGroup()
    return found


expect("channel of 0.7.2", channel_of("0.7.2"), "stable")
expect("channel of 0.8.0-dev1", channel_of("0.8.0-dev1"), "dev")
expect("first start adds stable", register("0.7.2"), True)
expect("listed for QGIS", repositories().get("SemanticGIS"), CHANNELS["stable"][1])
expect("second start adds nothing", register("0.7.2"), False)
QgsSettings().remove(f"{QGIS_REPOSITORIES}/SemanticGIS")
expect("removed entry stays removed", (register("0.7.2"), "SemanticGIS" in repositories()), (False, False))
expect("a prototype adds dev", register("0.8.0-dev1"), True)
expect("dev listed", repositories().get("SemanticGIS (dev)"), CHANNELS["dev"][1])

app.exitQgis()
sys.exit(1 if failures else 0)
