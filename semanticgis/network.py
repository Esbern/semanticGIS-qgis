"""Workaround for Dataforsyningen/Datafordeleren in QGIS 4 (Qt 6).

Over HTTP/2 the Dataforsyningen API gateway answers Qt 6 with a repeated Content-Encoding
header ("gzip, gzip, gzip, gzip"); Qt then hands QGIS a still-compressed body and every
WMS/WMTS/WFS layer from api.dataforsyningen.dk fails. curl and browsers are unaffected.
HTTP/1.1 responses are fine, so requests to these hosts are sent with HTTP/2 disabled while
the plugin is loaded. Broken replies already in the QGIS network cache are cleared once.
"""

from qgis.core import QgsNetworkAccessManager, QgsSettings
from qgis.PyQt.QtNetwork import QNetworkRequest

HTTP1_HOSTS = ("dataforsyningen.dk", "datafordeler.dk")
CACHE_FLAG = "semanticgis/http1_cache_cleared"


def _force_http1(request):
    host = request.url().host()
    if host.endswith(HTTP1_HOSTS):
        request.setAttribute(QNetworkRequest.Attribute.Http2AllowedAttribute, False)


def install():
    """Register the workaround; returns an id for uninstall()."""
    preprocessor_id = QgsNetworkAccessManager.setRequestPreprocessor(_force_http1)
    settings = QgsSettings()
    if not settings.value(CACHE_FLAG, False, type=bool):
        cache = QgsNetworkAccessManager.instance().cache()
        if cache is not None:
            cache.clear()
        settings.setValue(CACHE_FLAG, True)
    return preprocessor_id


def uninstall(preprocessor_id):
    if preprocessor_id:
        QgsNetworkAccessManager.removeRequestPreprocessor(preprocessor_id)


def get(url, timeout_ms=30000):
    """GET through the QGIS network manager (proxy settings, keys added by access.py); bytes.
    Safe in a QgsTask. Raises OSError with the HTTP status on failure."""
    from qgis.core import QgsBlockingNetworkRequest
    from qgis.PyQt.QtCore import QUrl

    request = QgsBlockingNetworkRequest()
    network_request = QNetworkRequest(QUrl(url))
    network_request.setTransferTimeout(timeout_ms)
    if request.get(network_request, True) != QgsBlockingNetworkRequest.ErrorCode.NoError:
        reply = request.reply()
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        raise OSError(f"HTTP {status}: {request.errorMessage()}" if status else request.errorMessage())
    return bytes(request.reply().content())
