"""SSL roots and connection diagnosis (network). Throw-away QGIS settings and authentication database.

- the bundled roots match their fingerprints;
- ensure_roots() adds a root QGIS does not trust, so new connections (also in background tasks) use
  it, and adds nothing on a second run or when QGIS trusts them all;
- the diagnosis tells the cases apart, live: a computer without Sectigo R46 sees Datafordeleren as
  "untrusted" with the student's error; with the roots it is "ok"; plus inspected / clock / unreachable.

  python tests/certificates.py
"""
import os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
DATA = os.path.join(os.path.dirname(__file__), "data")
os.environ["QGIS_AUTH_DB_DIR_PATH"] = tempfile.mkdtemp(prefix="sgis-ssl-")
from qgis.PyQt.QtCore import QCoreApplication, QSettings, QUrl, QEventLoop
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest, QSslCertificate, QSslError
from qgis.core import QgsApplication, QgsNetworkAccessManager, QgsTask
QCoreApplication.setOrganizationName("SemanticGISTestSSL")
app = QgsApplication([], False); app.initQgis()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())

from semanticgis import certificates as certs

failures = 0


def expect(label, got, want):
    global failures
    good = got == want
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {got!r}" + ("" if good else f" (expected {want!r})"))


manager = QgsApplication.authManager()
digest = certs._digest
fresh = certs.check_server("https://wms.datafordeler.dk/")
expect("fresh session, before anything else: ok", fresh["state"], "ok")
roots = certs.bundled_roots()
expect("bundled roots match their fingerprints", len(roots), len(certs.BUNDLED_ROOTS))
expect("nothing to add when QGIS trusts them all", certs.ensure_roots(), [])

# A root QGIS does not trust (stands in for R46 on a student's computer): added once.
test_root = QSslCertificate.fromPath(os.path.join(DATA, "testca.pem"))[0]
certs.CERT_DIR = DATA
certs.BUNDLED_ROOTS = {"testca.pem": digest(test_root)}
expect("added when missing", certs.ensure_roots(), ["SemanticGIS Test Root"])
expect("now trusted by QGIS", digest(test_root) in {digest(c) for c in certs.trusted_roots()}, True)
expect("second run adds nothing", certs.ensure_roots(), [])
result = {}


def background(task):
    reply = QgsNetworkAccessManager.instance().get(QNetworkRequest(QUrl("https://wfs.datafordeler.dk/")))
    loop = QEventLoop(); reply.finished.connect(loop.quit); loop.exec()
    result["roots"] = {digest(c) for c in reply.sslConfiguration().caCertificates()}
    return True


task = QgsTask.fromFunction("probe", background); QgsApplication.taskManager().addTask(task)
deadline = time.time() + 60
while "roots" not in result and time.time() < deadline:
    QgsApplication.processEvents(); time.sleep(0.05)
expect("used by connections in background tasks", digest(test_root) in result.get("roots", set()), True)

# Classification of the four cases.
r46 = next(r for r in roots if "R46" in r.subjectDisplayName())
av = QSslCertificate.fromPath(os.path.join(DATA, "av-root.pem"))[0]
E = QSslError.SslError
expect("missing root", certs.classify([E.SelfSignedCertificateInChain], [r46], QNetworkReply.NetworkError.SslHandshakeFailedError)[0], "untrusted")
expect("antivirus inspection", certs.classify([E.UnableToGetLocalIssuerCertificate], [av], QNetworkReply.NetworkError.SslHandshakeFailedError)[0], "inspected")
expect("names the inspector", "AVAST Software" in certs.classify([], [av], QNetworkReply.NetworkError.NoError)[1], True)
expect("wrong clock", certs.classify([E.CertificateNotYetValid], [r46], QNetworkReply.NetworkError.SslHandshakeFailedError)[0], "clock")
expect("no connection", certs.classify([], [], QNetworkReply.NetworkError.HostNotFoundError)[0], "unreachable")

# Live: Datafordeleren from a computer that lacks R46, and with QGIS's roots.
without = [c for c in certs.trusted_roots() if digest(c) != digest(r46)]
student = certs.check_server("https://wms.datafordeler.dk/", roots=without)
expect("live, without R46: untrusted", student["state"], "untrusted")
expect("the student's error message", any("self-signed, and untrusted" in t or "local issuer" in t for t in student["ssl_errors"]), True)
expect("chain ends at R46", "R46" in student["root"], True)
states = {label: r["state"] for label, r in certs.diagnose()}
expect("live, all servers ok", set(states.values()), {"ok"})
print("\n" + certs.report([("Datafordeleren WMS (simulated, without R46)", student)]))

app.exitQgis()
sys.exit(1 if failures else 0)
