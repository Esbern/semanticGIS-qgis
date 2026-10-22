"""SSL problems with Datafordeleren, Dataforsyningen and the other servers the plugin talks to.

Both portals use fairly new root certificates: Datafordeleren's chain ends in Sectigo Public Server
Authentication Root R46, Dataforsyningen's (Let's Encrypt) in ISRG Root X2. Windows downloads such
roots only when a program asks Windows to check a certificate, which browsers do and QGIS does not:
QGIS reads the system's roots once and checks certificates itself. A computer that has not met these
roots yet then fails with "The root certificate of the certificate chain is self-signed, and untrusted".

ensure_roots() adds the roots the plugin needs to QGIS's own list of trusted authorities (the
authentication database), when QGIS does not trust them already. The roots ship with the plugin and
are checked against their published SHA-256 fingerprints before use.

diagnose() checks the connections to the servers and says what is wrong: a missing root, HTTPS
inspection by antivirus or a network (a certificate from another issuer than the server's), a wrong
clock, or no connection at all.
"""

import os

from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsNetworkAccessManager
from qgis.PyQt.QtCore import QCryptographicHash, QEventLoop, QTimer, QUrl
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest, QSslCertificate, QSslError

CERT_DIR = os.path.join(os.path.dirname(__file__), "certs")

# Roots the servers chain to, with their SHA-256 fingerprints (as published by the CAs).
BUNDLED_ROOTS = {
    "sectigo-public-server-authentication-root-r46.pem": "7bb647a62aeeac88bf257aa522d01ffea395e0ab45c73f93f65654ec38f25a06",
    "isrg-root-x2.pem": "69729b8e15a86efc177a57afb7171dfc64add28c2fca8cf1507e34453ccb1470",
    "isrg-root-x1.pem": "96bcec06264976f37460779acf28c5a7cfe8a3c0aae11a8ffcee05c0bddf08c6",
    "usertrust-rsa-certification-authority.pem": "e793c9b02fd8aa13e21c31228accb08119643b749c898964b1746d46c3d4cbd2",
}

# The servers to test, and who issues their certificates; another issuer means someone in between.
SERVERS = [
    ("Datafordeleren WMS", "https://wms.datafordeler.dk/"),
    ("Datafordeleren WMTS", "https://wmts.datafordeler.dk/"),
    ("Datafordeleren WFS", "https://wfs.datafordeler.dk/"),
    ("Datafordeleren WCS", "https://wcs.datafordeler.dk/"),
    ("Dataforsyningen", "https://api.dataforsyningen.dk/"),
    ("semanticgis.org (catalogue)", "https://semanticgis.org/"),
]
PUBLIC_ISSUERS = ("Sectigo Limited", "Let's Encrypt", "Internet Security Research Group", "The USERTRUST Network",
                  "DigiCert Inc", "GlobalSign nv-sa", "Google Trust Services", "Amazon", "Microsoft Corporation")
CLOCK_ERRORS = (QSslError.SslError.CertificateNotYetValid, QSslError.SslError.CertificateExpired)
TAG = "SemanticGIS"


def _digest(certificate):
    return bytes(certificate.digest(QCryptographicHash.Algorithm.Sha256)).hex()


def bundled_roots():
    """The bundled roots that match their fingerprints (a changed file is left out and logged)."""
    roots = []
    for name, fingerprint in BUNDLED_ROOTS.items():
        certificates = QSslCertificate.fromPath(os.path.join(CERT_DIR, name))
        if certificates and _digest(certificates[0]) == fingerprint:
            roots.append(certificates[0])
        else:
            QgsMessageLog.logMessage(f"Root certificate {name} does not match its fingerprint; not used", TAG, Qgis.MessageLevel.Critical)
    return roots


def trusted_roots():
    """The roots QGIS trusts now (system roots and its own authorities). Computed rather than read
    from QGIS's cache, which stays empty until QGIS first starts its authentication system."""
    manager = QgsApplication.authManager()
    if hasattr(manager, "ensureInitialized"):
        manager.ensureInitialized()
    return manager.trustedCaCerts()


def ensure_roots():
    """Add the bundled roots QGIS does not trust yet to its authorities. Returns their names."""
    manager = QgsApplication.authManager()
    if manager.isDisabled():
        return []
    trusted = {_digest(c) for c in trusted_roots()}
    added = []
    for root in bundled_roots():
        if _digest(root) in trusted:
            continue
        if manager.storeCertAuthority(root):
            added.append(root.subjectDisplayName())
    if added:
        manager.rebuildCaCertsCache()
        manager.rebuildCertTrustCache()
        manager.rebuildTrustedCaCertsCache()
        QgsNetworkAccessManager.instance().clearConnectionCache()   # open connections pick them up too
        QgsMessageLog.logMessage("Added root certificates QGIS did not trust: " + ", ".join(added), TAG, Qgis.MessageLevel.Info)
    return added


# -- diagnosis ---------------------------------------------------------------------------

def classify(errors, chain, network_error):
    """(state, advice) for one server. `errors` are QSslError.SslError values, `chain` the
    certificates the server presented (leaf first), `network_error` a QNetworkReply.NetworkError."""
    issuers = [c.issuerInfo(QSslCertificate.SubjectInfo.Organization) for c in chain[:1]]
    issuer = (issuers[0][0] if issuers and issuers[0] else "") if chain else ""
    if any(e in CLOCK_ERRORS for e in errors):
        return "clock", "The certificate is not valid at this computer's date: check the date, time and time zone."
    if chain and issuer and not any(issuer.startswith(p) for p in PUBLIC_ISSUERS):
        return "inspected", (f"The certificate comes from “{issuer}”, not from the server's certificate authority: "
                             "antivirus or a network is inspecting HTTPS. Switch off HTTPS/SSL scanning for QGIS "
                             "(often called “web shield” or “HTTPS scanning”), or try another network.")
    if errors:
        return "untrusted", ("QGIS does not trust the certificate's root. Click “Add root certificates”; "
                             "if that does not help, send the report to your teacher.")
    if network_error in (QNetworkReply.NetworkError.NoError, QNetworkReply.NetworkError.ContentNotFoundError,
                         QNetworkReply.NetworkError.AuthenticationRequiredError, QNetworkReply.NetworkError.ContentAccessDenied,
                         QNetworkReply.NetworkError.ContentOperationNotPermittedError, QNetworkReply.NetworkError.UnknownContentError,
                         QNetworkReply.NetworkError.ProtocolInvalidOperationError):
        return "ok", ""   # the server answered over a trusted connection; the page itself does not matter
    return "unreachable", "No connection to the server: check the network, a proxy or a firewall."


def check_server(url, timeout_ms=20000, roots=None):
    """Connect the way QGIS does (its trusted roots and proxy) without QGIS's SSL dialog.
    `roots` replaces QGIS's trusted roots (tests use it to imitate a computer that lacks one)."""
    manager = QNetworkAccessManager()
    manager.setProxy(QgsNetworkAccessManager.instance().proxy())
    request = QNetworkRequest(QUrl(url))
    config = request.sslConfiguration()
    config.setCaCertificates(roots if roots is not None else trusted_roots())
    request.setSslConfiguration(config)
    found = {"errors": [], "chain": []}

    def on_ssl_errors(errors):
        found["errors"] = [e.error() for e in errors]
        found["texts"] = [e.errorString() for e in errors]
        found["chain"] = reply.sslConfiguration().peerCertificateChain()

    reply = manager.head(request)
    reply.sslErrors.connect(on_ssl_errors)
    loop = QEventLoop()
    reply.finished.connect(loop.quit)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    if not reply.isFinished():
        reply.abort()
    chain = found["chain"] or reply.sslConfiguration().peerCertificateChain()
    state, advice = classify(found["errors"], chain, reply.error())
    return {
        "url": url, "state": state, "advice": advice,
        "ssl_errors": found.get("texts", []),
        "network_error": reply.errorString() if reply.error() != QNetworkReply.NetworkError.NoError else "",
        "issuer": chain[0].issuerDisplayName() if chain else "",
        "root": chain[-1].subjectDisplayName() if chain else "",
    }


def diagnose(extra=()):
    """Check every server; a list of (label, result)."""
    return [(label, check_server(url)) for label, url in list(SERVERS) + list(extra)]


def report(results):
    """A plain-text report to copy into an e-mail."""
    from .dock import plugin_version
    from qgis.PyQt.QtNetwork import QSslSocket

    lines = [f"SemanticGIS {plugin_version()} connection report", f"QGIS {Qgis.version()} · TLS {QSslSocket.sslLibraryVersionString()}", ""]
    for label, r in results:
        lines.append(f"{label}: {r['state']}  ({r['url']})")
        if r["issuer"]:
            lines.append(f"    certificate from: {r['issuer']}  ·  chain ends at: {r['root']}")
        for text in r["ssl_errors"]:
            lines.append(f"    SSL: {text}")
        if r["network_error"] and r["state"] != "ok":
            lines.append(f"    network: {r['network_error']}")
    return "\n".join(lines)


class ConnectionDialog:
    """Settings › Test connections: a table of the servers, what is wrong and what to do."""

    @staticmethod
    def show(parent=None):
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtGui import QGuiApplication
        from qgis.PyQt.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHeaderView, QLabel,
                                         QTableWidget, QTableWidgetItem, QVBoxLayout)

        dialog = QDialog(parent)
        dialog.setWindowTitle("SemanticGIS connections")
        dialog.setMinimumSize(720, 360)
        layout = QVBoxLayout(dialog)
        intro = QLabel("Checks the secure (HTTPS) connections to the servers the plugin uses, the way QGIS makes them.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Server", "Result", "What to do"])
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.setWordWrap(True)
        layout.addWidget(table)
        added_label = QLabel()
        added_label.setWordWrap(True)
        layout.addWidget(added_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        add_button = buttons.addButton("Add root certificates", QDialogButtonBox.ButtonRole.ActionRole)
        copy_button = buttons.addButton("Copy report", QDialogButtonBox.ButtonRole.ActionRole)
        retry_button = buttons.addButton("Test again", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        state = {"results": []}
        words = {"ok": "OK", "untrusted": "Root not trusted", "inspected": "HTTPS inspected", "clock": "Wrong clock",
                 "unreachable": "No connection"}

        def run():
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                state["results"] = diagnose()
            finally:
                QApplication.restoreOverrideCursor()
            table.setRowCount(0)
            for label, result in state["results"]:
                row = table.rowCount()
                table.insertRow(row)
                for column, text in enumerate((label, words.get(result["state"], result["state"]), result["advice"])):
                    item = QTableWidgetItem(text)
                    item.setToolTip("\n".join([result["url"], *result["ssl_errors"], result["network_error"]]).strip())
                    table.setItem(row, column, item)
            table.resizeRowsToContents()
            add_button.setEnabled(any(r["state"] == "untrusted" for _, r in state["results"]))

        def add():
            added = ensure_roots()
            added_label.setText("Added: " + ", ".join(added) if added else
                                "QGIS already trusts the plugin's root certificates; the problem lies elsewhere (see the report).")
            run()

        add_button.clicked.connect(add)
        copy_button.clicked.connect(lambda: QGuiApplication.clipboard().setText(report(state["results"])))
        retry_button.clicked.connect(run)
        dialog.show()
        run()
        dialog.exec()
