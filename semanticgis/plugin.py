"""Plugin entry point: a toolbar/menu action that shows the SemanticGIS dock."""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from . import network
from .dock import SemanticGisDock

MENU = "&SemanticGIS"


class SemanticGisPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.action = None
        self.network_fix = None

    def initGui(self):
        self.network_fix = network.install()
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "SemanticGIS data network", self.iface.mainWindow())
        self.action.setCheckable(True)
        # Only user clicks drive the panel. Qt reports a docked panel as "not visible" while it
        # sits behind another tab; reacting to that would close the panel and drop its tab.
        self.action.triggered.connect(self.toggle_dock)
        self.iface.addWebToolBarIcon(self.action)
        self.iface.addPluginToWebMenu(MENU, self.action)

    def _ensure_dock(self):
        if self.dock is None:
            self.dock = SemanticGisDock(self.iface, self.iface.mainWindow())
            self.dock.visibilityChanged.connect(self._sync_action)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
            self.dock.reload()
        return self.dock

    def _sync_action(self, *_):
        # Checked means "open", whether or not the panel's tab is the one in front.
        if self.dock is not None:
            self.action.setChecked(not self.dock.isHidden())

    def _behind_another_tab(self):
        tabbed = self.iface.mainWindow().tabifiedDockWidgets(self.dock)
        return bool(tabbed) and (not self.dock.isVisible() or self.dock.visibleRegion().isEmpty())

    def toggle_dock(self, *_):
        """Toolbar/menu click: open a closed panel, bring a tabbed-away panel to the front,
        or close a panel that is already in front."""
        fresh = self.dock is None
        dock = self._ensure_dock()
        if fresh or dock.isHidden():
            dock.show()
            dock.raise_()
        elif self._behind_another_tab():
            dock.raise_()
        else:
            dock.hide()
        self._sync_action()

    def unload(self):
        network.uninstall(self.network_fix)
        self.network_fix = None
        self.iface.removePluginWebMenu(MENU, self.action)
        self.iface.removeWebToolBarIcon(self.action)
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
