"""The user's own keys for services that need one, added to each request as it is sent.

Each access profile (Data/Access Profiles on the site) names the hosts that need a key and the
query parameter it travels as. Keys are never written into layer sources: a QGIS request
preprocessor adds `<param>=<key>` to every request to a matching host. A saved or shared project
therefore contains no keys, and whoever opens it uses their own.

Keys are kept in the QGIS settings of the user profile, or, when chosen in Settings, encrypted in
the QGIS authentication database (unlocked with the QGIS master password).
"""

from qgis.core import QgsApplication, QgsNetworkAccessManager, QgsSettings
from qgis.PyQt.QtCore import QUrlQuery

from .catalogue import BUILTIN_PROFILES

KEY_PREFIX = "semanticgis/keys/"
KEY_IDS = "semanticgis/key_ids"            # profiles with a stored key (the encrypted store cannot list)
ENCRYPTED = "semanticgis/keys_encrypted"
LEGACY_SETTINGS = {"dataforsyningen": "semanticgis/dataforsyningen_token", "datafordeler": "semanticgis/datafordeler_api_key"}


class KeyStore:
    def __init__(self):
        self.profiles = dict(BUILTIN_PROFILES)
        self._keys = {}
        self.loaded = False
        self._preprocessor = None

    # -- profiles and keys ---------------------------------------------------------------

    def set_profiles(self, profiles):
        self.profiles = dict(BUILTIN_PROFILES)
        self.profiles.update(profiles or {})

    @staticmethod
    def encrypted():
        return QgsSettings().value(ENCRYPTED, False, type=bool)

    def _stored_ids(self):
        return [i for i in (QgsSettings().value(KEY_IDS, "") or "").split(",") if i]

    def load(self, prompt=True):
        """Read the keys into memory. With the encrypted store this needs the master password: it is
        asked for only when `prompt` is true. Returns whether the keys are available."""
        settings = QgsSettings()
        for profile_id, legacy in LEGACY_SETTINGS.items():   # keys saved by plugin versions before 0.7
            value = settings.value(legacy, "")
            if value and not self.encrypted() and not settings.value(KEY_PREFIX + profile_id, ""):
                settings.setValue(KEY_PREFIX + profile_id, value)
                settings.setValue(KEY_IDS, ",".join(sorted(set(self._stored_ids()) | {profile_id})))
            if value:
                settings.remove(legacy)
        if self.encrypted():
            manager = QgsApplication.authManager()
            if manager.isDisabled():
                return False
            if not manager.masterPasswordIsSet() and (not prompt or not manager.setMasterPassword(True)):
                return False
            self._keys = {i: manager.authSetting(KEY_PREFIX + i, "", True) or "" for i in self._stored_ids()}
        else:
            self._keys = {i: settings.value(KEY_PREFIX + i, "") or "" for i in self._stored_ids()}
        self._keys = {i: k for i, k in self._keys.items() if k}
        self.loaded = True
        return True

    def save(self, keys, encrypted):
        """Store the keys (a dict profile id -> key; empty removes) in the chosen store."""
        settings = QgsSettings()
        manager = QgsApplication.authManager()
        if encrypted and not manager.masterPasswordIsSet() and not manager.setMasterPassword(True):
            raise RuntimeError("The QGIS master password is needed to store keys encrypted.")
        keys = {**self._keys, **{i: (k or "").strip() for i, k in keys.items()}}   # profiles not shown keep theirs
        for profile_id in set(self._stored_ids()) | set(keys):   # clear both stores, then write one
            settings.remove(KEY_PREFIX + profile_id)
            if manager.existsAuthSetting(KEY_PREFIX + profile_id):
                manager.removeAuthSetting(KEY_PREFIX + profile_id)
        kept = {i: k for i, k in keys.items() if k}
        for profile_id, key in kept.items():
            if encrypted:
                manager.storeAuthSetting(KEY_PREFIX + profile_id, key, True)
            else:
                settings.setValue(KEY_PREFIX + profile_id, key)
        settings.setValue(KEY_IDS, ",".join(sorted(kept)))
        settings.setValue(ENCRYPTED, bool(encrypted))
        self._keys = kept
        self.loaded = True

    def use(self, keys):
        """Use these keys (profile id -> key) in this session without storing them (scripts, tests)."""
        self._keys.update({i: k for i, k in keys.items() if k})
        self.loaded = True

    # The .env names of semanticGIS-code, for scripts and tests.
    ENV = {"datafordeler": "DATAFORDELER_API_KEY", "dataforsyningen": "DATAFORSYNINGEN_TOKEN", "carto": "CARTO_API_KEY"}

    def use_environment(self):
        import os

        self.use({i: os.environ.get(name, "") for i, name in self.ENV.items()})

    def key(self, profile_id):
        return self._keys.get(profile_id, "")

    def has_key(self, profile_id):
        return bool(self._keys.get(profile_id))

    # -- services ----------------------------------------------------------------------

    def profile_of(self, service):
        return self.profiles.get(service.access) if service.access else None

    def missing(self, service):
        """A message when the service needs a key the user has not set, else None."""
        profile = self.profile_of(service)
        if profile is None or self.has_key(profile.id):
            return None
        if not self.loaded and self.encrypted():
            return f"This service needs your {profile.title}; unlock your keys with the QGIS master password (SemanticGIS Settings)."
        where = f" Get one at {profile.signup_url}." if profile.signup_url else ""
        return f"This service needs your own {profile.title} (SemanticGIS Settings).{where}"

    def url_with_key(self, url, service):
        """`url` with the key in it, for links opened outside QGIS (downloads in the browser)."""
        from .layers import with_params

        profile = self.profile_of(service)
        return with_params(url, **{profile.param: self.key(profile.id)}) if profile else url

    # -- request time ------------------------------------------------------------------

    def preprocess(self, request):
        """Add the matching profile's key to a request (QGIS request preprocessor; any thread)."""
        url = request.url()
        host = url.host().lower()
        for profile in list(self.profiles.values()):
            key = self._keys.get(profile.id)
            if not key or not profile.matches(host):
                continue
            query = QUrlQuery(url)
            if any(name.lower() == profile.param.lower() for name, _ in query.queryItems()):
                return   # the URL already carries a key (e.g. a layer added by an older version)
            query.addQueryItem(profile.param, key)
            url.setQuery(query)
            request.setUrl(url)
            return

    def install(self):
        if self._preprocessor is None:
            self._preprocessor = QgsNetworkAccessManager.setRequestPreprocessor(self.preprocess)

    def uninstall(self):
        if self._preprocessor is not None:
            QgsNetworkAccessManager.removeRequestPreprocessor(self._preprocessor)
            self._preprocessor = None


KEYS = KeyStore()
