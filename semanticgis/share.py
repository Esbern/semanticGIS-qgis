"""Files on the RUC data share (Nextcloud), read over the network by GDAL.

The user gives the share's link and password once (Settings). The password is handed to GDAL for this server path only,
in this session: it is never part of a layer's source, so a saved project does not contain it. The link's token is part of
the source; without the password it opens nothing.
"""

from urllib.parse import quote, urlsplit

from .catalogue_v2 import SHARE

LINK = SHARE + "-link"


def parse_link(link):
    """(server, token) of a Nextcloud share link https://server/s/<token>, or None."""
    parts = urlsplit((link or "").strip())
    pieces = [p for p in parts.path.split("/") if p]
    if parts.scheme != "https" or not parts.hostname or "s" not in pieces[:-1]:
        return None
    return f"https://{parts.netloc}", pieces[pieces.index("s") + 1]


def base_path(link):
    found = parse_link(link)
    return f"/vsicurl/{found[0]}/public.php/dav/files/{found[1]}/" if found else None


def configure(link, password):
    """Give GDAL the share's login for its path. Returns whether the share can be used."""
    from osgeo import gdal

    base = base_path(link)
    if not base or not password:
        return False
    token = parse_link(link)[1]
    gdal.SetPathSpecificOption(base, "GDAL_HTTP_USERPWD", f"{token}:{password}")
    gdal.SetPathSpecificOption(base, "GDAL_HTTP_AUTH", "BASIC")
    gdal.SetPathSpecificOption(base, "GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    gdal.VSICurlPartialClearCache(base)      # forget a refusal from before the password was given
    return True


def source(link, href):
    return base_path(link) + quote(href)
