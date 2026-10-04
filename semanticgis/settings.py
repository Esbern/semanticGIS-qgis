"""Plugin settings: catalogue source, site URL and Dataforsyningen token."""

from qgis.core import QgsSettings
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit

from .catalogue import DEFAULT_BASE_URL

SETTINGS_PREFIX = "semanticgis/"
DEFAULT_SITE_URL = "https://semanticgis.org/Data"


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SemanticGIS settings")
        settings = QgsSettings()
        form = QFormLayout(self)

        self.source = QLineEdit(settings.value(SETTINGS_PREFIX + "catalogue_source", DEFAULT_BASE_URL))
        self.source.setToolTip("Base URL or local folder holding sphere-index.v1.json and services.v1.json")
        form.addRow("Catalogue source", self.source)

        self.site = QLineEdit(settings.value(SETTINGS_PREFIX + "site_url", DEFAULT_SITE_URL))
        form.addRow("Documentation site", self.site)

        self.token = QLineEdit(settings.value(SETTINGS_PREFIX + "dataforsyningen_token", ""))
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Dataforsyningen token", self.token)
        note = QLabel(
            "The token is added to the service URL of Dataforsyningen layers, "
            "so it is saved in project files that contain those layers."
        )
        note.setWordWrap(True)
        form.addRow(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self):
        settings = QgsSettings()
        settings.setValue(SETTINGS_PREFIX + "catalogue_source", self.source.text().strip() or DEFAULT_BASE_URL)
        settings.setValue(SETTINGS_PREFIX + "site_url", self.site.text().strip() or DEFAULT_SITE_URL)
        settings.setValue(SETTINGS_PREFIX + "dataforsyningen_token", self.token.text().strip())
        self.accept()
