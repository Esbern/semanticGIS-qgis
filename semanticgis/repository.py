"""The plugin's update channels: plugin repositories (plugins.xml) on the repository's GitHub.

  stable  released versions, for students                  stable.xml
  dev     also prototypes (versions like 0.8.0-dev1)       dev.xml
  v2      the v2 data structure, for testers: the 0.9 prototypes  v2.xml

The v2 channel stands alone: its releases are listed nowhere else, and stable and dev releases are not listed in it.

On first start the plugin adds its own channel to QGIS's plugin repositories (Plugins > Manage and
Install Plugins > Settings), so QGIS offers updates from then on. It does so once per channel: a
user who removes the entry keeps it removed.
"""

from qgis.core import QgsSettings

REPOSITORY_BASE = "https://raw.githubusercontent.com/Esbern/semanticGIS-qgis/plugin-repository/"
CHANNELS = {
    "stable": ("SemanticGIS", REPOSITORY_BASE + "stable.xml"),
    "dev": ("SemanticGIS (dev)", REPOSITORY_BASE + "dev.xml"),
    "v2": ("SemanticGIS (v2 test)", REPOSITORY_BASE + "v2.xml"),
}
QGIS_REPOSITORIES = "app/plugin_repositories"   # where QGIS's plugin installer keeps them
REGISTERED = "semanticgis/repository_registered"


def channel_of(version):
    """Prototype versions carry a suffix (0.8.0-dev1); releases do not. The 0.9 prototypes are the v2 line: a marker
    in the suffix (0.9.0-v2dev1) is not an option, QGIS would order v2dev10 before v2dev9 and 0.9.0 before both."""
    if (version or "").startswith("0.9.") and "-" in version:
        return "v2"
    return "dev" if "-" in (version or "") else "stable"


def register(version):
    """Add the channel of this version to QGIS's plugin repositories, once. Returns whether it was added."""
    channel = channel_of(version)
    settings = QgsSettings()
    done = [c for c in (settings.value(REGISTERED, "") or "").split(",") if c]
    if channel in done:
        return False
    name, url = CHANNELS[channel]
    settings.beginGroup(QGIS_REPOSITORIES)
    present = any(settings.value(f"{key}/url", "") == url for key in settings.childGroups())
    if not present:
        settings.setValue(f"{name}/url", url)
        settings.setValue(f"{name}/enabled", True)
    settings.endGroup()
    settings.setValue(REGISTERED, ",".join(done + [channel]))
    return not present
