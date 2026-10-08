"""Plugin settings: catalogue source, site URL and the user's own keys (one per access profile)."""

from qgis.core import QgsSettings
from qgis.PyQt.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox

from .access import KEYS
from .catalogue import DEFAULT_BASE_URL

SETTINGS_PREFIX = "semanticgis/"
DEFAULT_SITE_URL = "https://semanticgis.org/Data"


class SettingsDialog(QDialog):
    def __init__(self, profiles, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SemanticGIS settings")
        self.setMinimumWidth(460)
        settings = QgsSettings()
        form = QFormLayout(self)

        self.source = QLineEdit(settings.value(SETTINGS_PREFIX + "catalogue_source", DEFAULT_BASE_URL))
        self.source.setToolTip("Base URL or local folder holding sphere-index.v1.json and services.v1.json")
        form.addRow("Catalogue source", self.source)

        self.site = QLineEdit(settings.value(SETTINGS_PREFIX + "site_url", DEFAULT_SITE_URL))
        form.addRow("Documentation site", self.site)

        heading = QLabel("<b>Your keys</b>")
        form.addRow(heading)
        self.unlocked = KEYS.loaded or KEYS.load(prompt=True)
        self.keys = {}
        for profile in sorted(profiles.values(), key=lambda p: p.title.lower()):
            field = QLineEdit(KEYS.key(profile.id))
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setEnabled(self.unlocked)
            hint = f"Get one at {profile.signup_url}" if profile.signup_url else ""
            field.setPlaceholderText(hint)
            field.setToolTip("\n".join(filter(None, [f"Sent as '{profile.param}' to {', '.join(profile.hosts)}", profile.cost, hint])))
            form.addRow(profile.title, field)
            self.keys[profile.id] = field

        self.encrypt = QCheckBox("Keep the keys encrypted (QGIS master password)")
        self.encrypt.setChecked(KEYS.encrypted())
        self.encrypt.setEnabled(self.unlocked)
        self.encrypt.setToolTip("Stores the keys in the QGIS authentication database instead of the QGIS settings file. "
                                "QGIS asks for the master password once per session when a key is needed.")
        form.addRow(self.encrypt)
        note = QLabel(
            "Use your own keys: the catalogue never contains any. A key is added to each request to its "
            "provider as the request is sent, and is not written into layers or saved projects."
            + ("" if self.unlocked else "<br><b>Your keys are locked:</b> the QGIS master password was not given.")
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
        if self.unlocked:
            try:
                KEYS.save({i: field.text() for i, field in self.keys.items()}, self.encrypt.isChecked())
            except RuntimeError as error:
                QMessageBox.warning(self, "SemanticGIS", str(error))
                return
        self.accept()
