"""Plugin settings: catalogue source, site URL and the user's own keys (one per access profile)."""

from qgis.core import QgsSettings
from qgis.PyQt.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox

from .access import KEYS
from .certificates import ConnectionDialog
from .extensions import TOPIC, server_profile
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

        self.servers = QLineEdit(settings.value(SETTINGS_PREFIX + "extension_servers", ""))
        self.servers.setPlaceholderText("e.g. git.ruc.dk")
        self.servers.setToolTip("GitLab servers with your research groups' SemanticGIS extension projects "
                                f"(projects with the topic '{TOPIC}'). Separate several with commas.")
        self.servers.editingFinished.connect(self._add_server_rows)
        form.addRow("Extension servers", self.servers)

        heading = QLabel("<b>Your keys</b>")
        form.addRow(heading)
        self.form = form
        self.unlocked = KEYS.loaded or KEYS.load(prompt=True)
        self.keys = {}
        for profile in sorted(profiles.values(), key=lambda p: p.title.lower()):
            self._add_key_row(profile)
        self._add_server_rows()

        self.keys_end = form.rowCount()   # key rows for servers added later go before this
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
        test = buttons.addButton("Test connections…", QDialogButtonBox.ButtonRole.HelpRole)
        test.setToolTip("Check the secure connections to Datafordeleren, Dataforsyningen and semanticgis.org "
                        "and see what to do about SSL errors")
        test.clicked.connect(lambda: ConnectionDialog.show(self))
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _add_key_row(self, profile, row=None):
        if profile.id in self.keys:
            return
        field = QLineEdit(KEYS.key(profile.id))
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setEnabled(self.unlocked)
        hint = f"Create one at {profile.signup_url}" if profile.signup_url else ""
        field.setPlaceholderText(hint)
        how = f"header {profile.header}" if profile.header else f"'{profile.param}'"
        field.setToolTip("\n".join(filter(None, [f"Sent as {how} to {', '.join(profile.hosts)}", profile.cost, hint])))
        if row is None:
            self.form.addRow(profile.title, field)
        else:
            self.form.insertRow(row, profile.title, field)
            self.keys_end += 1
        self.keys[profile.id] = field

    def _server_list(self):
        return [s.strip().rstrip("/") for s in self.servers.text().replace(";", ",").split(",") if s.strip()]

    def _add_server_rows(self):
        """A token field for each extension server, as soon as it is entered."""
        for server in self._server_list():
            profile = server_profile(server)
            self._add_key_row(profile, getattr(self, "keys_end", None))

    def save(self):
        settings = QgsSettings()
        settings.setValue(SETTINGS_PREFIX + "catalogue_source", self.source.text().strip() or DEFAULT_BASE_URL)
        settings.setValue(SETTINGS_PREFIX + "site_url", self.site.text().strip() or DEFAULT_SITE_URL)
        servers = self._server_list()
        settings.setValue(SETTINGS_PREFIX + "extension_servers", ", ".join(servers))
        KEYS.set_extra_profiles({p.id: p for p in map(server_profile, servers)})
        if self.unlocked:
            try:
                KEYS.save({i: field.text() for i, field in self.keys.items()}, self.encrypt.isChecked())
            except RuntimeError as error:
                QMessageBox.warning(self, "SemanticGIS", str(error))
                return
        self.accept()
