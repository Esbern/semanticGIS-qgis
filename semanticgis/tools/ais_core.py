"""AIS: one day of vessel messages from aisdata.ais.dk, streamed, cleaned and joined into lines. No QGIS imports.

A day is about 0.6 GB zipped and 2.7 GB as CSV. The zip is read as it arrives and unpacked on the way, so neither is
stored. Only the positions that pass the filters are kept in memory (about 30 bytes each).
"""
import array
import calendar
import csv
import datetime
import math
import struct
import urllib.request
import zlib

BASE = "http://aisdata.ais.dk/"
FIRST_DAILY = datetime.date(2024, 3, 1)     # before this the source has one file per month (up to 16 GB)
FIRST_2025 = datetime.date(2025, 2, 27)     # from this day the files lie in the root, not in the year's folder
NOT_VESSELS = {"Base Station", "AtoN"}
COLUMNS = ("# Timestamp", "Type of mobile", "MMSI", "Latitude", "Longitude", "Navigational status", "SOG", "IMO", "Callsign",
           "Name", "Ship type", "Width", "Length", "Draught", "Destination")


def day_url(day):
    folder = "" if day >= FIRST_2025 else f"{day.year}/"
    return f"{BASE}{folder}aisdk-{day.isoformat()}.zip"


class AisError(Exception):
    pass


def unzip_stream(chunks):
    """The bytes of the first file in a zip that arrives in chunks (stored or deflated)."""
    it = iter(chunks)
    buffer = b""
    while len(buffer) < 30:
        try:
            buffer += next(it)
        except StopIteration:
            raise AisError("The file is empty or not a zip.")
    if buffer[:4] != b"PK\x03\x04":
        raise AisError("The file is not a zip.")
    method, = struct.unpack("<H", buffer[8:10])
    n, m = struct.unpack("<HH", buffer[26:30])
    while len(buffer) < 30 + n + m:
        buffer += next(it)
    buffer = buffer[30 + n + m:]
    if method != 8:
        raise AisError(f"The zip uses a method this tool does not read ({method}).")
    unpack = zlib.decompressobj(-15)
    yield unpack.decompress(buffer)
    for chunk in it:
        if unpack.eof:
            return
        yield unpack.decompress(chunk)
    if not unpack.eof:
        raise AisError("The download stopped before the end of the file. Try again.")


def lines_of(chunks):
    rest = b""
    for chunk in chunks:
        rest += chunk
        parts = rest.split(b"\n")
        rest = parts.pop()
        for p in parts:
            yield p.decode("utf-8", "replace")
    if rest:
        yield rest.decode("utf-8", "replace")


def download(url, progress=None, cancelled=None, size=1 << 20):
    request = urllib.request.Request(url, headers={"User-Agent": "SemanticGIS-QGIS"})
    with urllib.request.urlopen(request, timeout=60) as response:
        total, got = int(response.headers.get("Content-Length") or 0), 0
        while True:
            chunk = response.read(size)
            if not chunk:
                return
            got += len(chunk)
            if progress:
                progress(got, total)
            if cancelled and cancelled():
                return
            yield chunk


def read_file(path, progress=None, cancelled=None, size=1 << 22):
    """A day that is already on disk: the zip, or the CSV."""
    import os
    total, got = os.path.getsize(path), 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(size)
            if not chunk:
                return
            got += len(chunk)
            if progress:
                progress(got, total)
            if cancelled and cancelled():
                return
            yield chunk


class Vessel:
    __slots__ = ("mmsi", "t", "x", "y", "sog", "facts", "last")

    def __init__(self, mmsi):
        self.mmsi, self.t, self.x, self.y, self.sog = mmsi, array.array("I"), array.array("d"), array.array("d"), array.array("f")
        self.facts, self.last = {}, None


FACTS = (("mobile_type", "Type of mobile"), ("name", "Name"), ("callsign", "Callsign"), ("imo", "IMO"), ("ship_type", "Ship type"),
         ("length", "Length"), ("width", "Width"), ("draught", "Draught"), ("destination", "Destination"))


def collect(text_lines, extent=None, classes=None, ship_types=None, hours=None, mmsi=None):
    """({mmsi: Vessel}, counts). extent: (west, south, east, north) in degrees; hours: (from, to), to not included.

    A ship's type is sent more rarely than its position, and can be missing in single messages, so the ship type is
    decided for the whole vessel afterwards (see by_ship_type)."""
    reader = csv.reader(text_lines)
    header = [h.strip() for h in next(reader)]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        raise AisError(f"The columns have changed at the source; missing: {', '.join(missing)}")
    at = {c: header.index(c) for c in COLUMNS}
    it, ik, im, iy, ix, iv = (at[c] for c in ("# Timestamp", "Type of mobile", "MMSI", "Latitude", "Longitude", "SOG"))
    facts = [(k, at[c]) for k, c in FACTS]
    width = len(header)
    vessels, n = {}, dict(rows=0, not_vessels=0, outside=0, impossible=0, repeated=0, unreadable=0, kept=0)
    days = {}
    for row in reader:
        n["rows"] += 1
        if len(row) < width:
            n["unreadable"] += 1
            continue
        kind = row[ik]
        if kind in NOT_VESSELS:
            n["not_vessels"] += 1
            continue
        if classes and kind not in classes:
            n["outside"] += 1
            continue
        if mmsi and row[im] not in mmsi:
            n["outside"] += 1
            continue
        try:
            y, x = float(row[iy]), float(row[ix])
            stamp = row[it]
            day = stamp[:10]
            if day not in days:
                days[day] = calendar.timegm((int(day[6:10]), int(day[3:5]), int(day[:2]), 0, 0, 0))
            hour = int(stamp[11:13])
            t = days[day] + hour * 3600 + int(stamp[14:16]) * 60 + int(stamp[17:19])
        except ValueError:
            n["unreadable"] += 1
            continue
        if not (-90 <= y <= 90 and -180 <= x <= 180) or (x == 0 and y == 0):
            n["impossible"] += 1
            continue
        if extent and not (extent[0] <= x <= extent[2] and extent[1] <= y <= extent[3]):
            n["outside"] += 1
            continue
        if hours and not hours[0] <= hour < hours[1]:
            n["outside"] += 1
            continue
        v = vessels.get(row[im])
        if v is None:
            v = vessels[row[im]] = Vessel(row[im])
        if v.last == t:                       # one position per vessel and second
            n["repeated"] += 1
            continue
        v.last = t
        v.t.append(t); v.x.append(x); v.y.append(y)
        try:
            v.sog.append(float(row[iv]) if row[iv] else -1)
        except ValueError:
            v.sog.append(-1)
        n["kept"] += 1
        for k, i in facts:
            value = row[i]
            if value and value not in ("Unknown", "Undefined"):
                seen = v.facts.setdefault(k, {})
                seen[value] = seen.get(value, 0) + 1
    return vessels, n


def fact(vessel, key):
    """The value the vessel sent most often."""
    seen = vessel.facts.get(key)
    return max(seen, key=seen.get) if seen else None


def by_ship_type(vessels, wanted):
    return {k: v for k, v in vessels.items() if (fact(v, "ship_type") or "Undefined") in wanted} if wanted else vessels


def metres(x0, y0, x1, y1):
    return math.hypot((x1 - x0) * math.cos(math.radians((y0 + y1) / 2)), y1 - y0) * 111320.0


def tracks(vessel, max_gap=1800, max_knots=100, patience=5):
    """(lists of positions (indexes), number of positions dropped as impossible jumps). Positions in time order.

    A silence longer than max_gap seconds starts a new line. A position that would need more than max_knots is dropped;
    when `patience` such positions follow each other, the earlier place was the wrong one and a new line starts."""
    order = range(len(vessel.t))
    if any(vessel.t[i] > vessel.t[i + 1] for i in range(len(vessel.t) - 1)):
        order = sorted(order, key=vessel.t.__getitem__)
    out, line, waiting, dropped = [], [], [], 0

    def reachable(i, j):
        seconds = max(vessel.t[j] - vessel.t[i], 1)
        return metres(vessel.x[i], vessel.y[i], vessel.x[j], vessel.y[j]) / seconds <= max_knots * 0.514444

    for i in order:
        if not line:
            line.append(i)
            continue
        if vessel.t[i] - vessel.t[line[-1]] > max_gap:
            dropped += len(waiting)
            out.append(line)
            line, waiting = [i], []
        elif reachable(line[-1], i):
            dropped += len(waiting)
            waiting = []
            line.append(i)
        else:
            if waiting and not reachable(waiting[-1], i):
                dropped += len(waiting)
                waiting = []
            waiting.append(i)
            if len(waiting) >= patience:
                out.append(line)
                line, waiting = waiting, []
    dropped += len(waiting)
    if line:
        out.append(line)
    return [l for l in out if len(l) > 1], dropped


def simplify(points, tolerance):
    """Douglas-Peucker in metres on a local plane; returns the indexes that are kept."""
    n = len(points)
    if n < 3 or tolerance <= 0:
        return list(range(n))
    k = math.cos(math.radians(points[0][1])) * 111320.0
    p = [(x * k, y * 111320.0) for x, y in points]
    keep = bytearray(n)
    keep[0] = keep[-1] = 1
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        ax, ay = p[a]
        dx, dy = p[b][0] - ax, p[b][1] - ay
        length2 = dx * dx + dy * dy
        worst, at = tolerance * tolerance, -1
        for i in range(a + 1, b):
            px, py = p[i][0] - ax, p[i][1] - ay
            if length2:
                u = max(0.0, min(1.0, (px * dx + py * dy) / length2))
                px, py = px - u * dx, py - u * dy
            d = px * px + py * py
            if d > worst:
                worst, at = d, i
        if at >= 0:
            keep[at] = 1
            stack += [(a, at), (at, b)]
    return [i for i in range(n) if keep[i]]
