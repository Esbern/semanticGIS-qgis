"""AIS tool: core on made-up messages, and the algorithm on a small file (no network unless AIS_FILE names a real day).
  QT_QPA_PLATFORM=offscreen python tests/ais_days.py"""
import os, sys, tempfile, zipfile, io, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import *
QgsApplication.setPrefixPath(os.environ.get("QGIS_PREFIX", "/Applications/QGIS.app/Contents/MacOS"), True)
if os.environ.get("QGIS_PLUGINPATH"): QgsApplication.setPluginPath(os.environ["QGIS_PLUGINPATH"])
app = QgsApplication([], True); app.initQgis()
from semanticgis.tools import ais_core as A, ais_days
fails = 0
def expect(label, got, want=True):
    global fails
    ok = got == want; fails += not ok; print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": {got!r} != {want!r}"))
H = "# Timestamp,Type of mobile,MMSI,Latitude,Longitude,Navigational status,ROT,SOG,COG,Heading,IMO,Callsign,Name,Ship type,Cargo type,Width,Length,Type of position fixing device,Draught,Destination,ETA,Data source type,A,B,C,D"
def row(t, mmsi, y, x, kind="Class A", name="ANNA", ship="Cargo", sog="10"): return f'22/11/2026 {t},{kind},{mmsi},{y},{x},Under way,,{sog},,,Unknown,Unknown,"{name}",{ship},,10,100,GPS,5,"A, B",,AIS,,,,'
R = [H, row("00:00:00", 1, 55.0, 12.0, name="", ship="Undefined"), row("00:00:00", 1, 55.0, 12.0), row("00:01:00", 1, 55.001, 12.0), row("00:02:00", 1, 58.0, 5.0), row("00:03:00", 1, 55.003, 12.0),   # a jump
     row("01:00:00", 1, 55.02, 12.0), row("01:01:00", 1, 55.021, 12.0),                                                         # after a silence
     row("00:00:00", 2, 91, 181), row("00:00:05", 2, 56.0, 11.0), row("00:00:00", 3, 55, 12, "Base Station"), row("00:00:00", 4, 55, 12, "AtoN"),
     row("05:00:00", 5, 57.0, 11.0, "Class B", "BÅD", "Sailing"), row("05:01:00", 5, 57.001, 11.0, "Class B", "BÅD", "Sailing"), "broken,row"]
vs, n = A.collect(iter(R)); expect("counts", (n["rows"], n["not_vessels"], n["impossible"], n["repeated"], n["unreadable"], n["kept"]), (14, 2, 1, 1, 1, 9))
t, d = A.tracks(vs["1"]); expect("two lines, jump dropped", ([len(x) for x in t], d), ([3, 2], 1)); expect("no line from one position", A.tracks(vs["2"])[0], [])
expect("most common fact", (A.fact(vs["1"], "name"), A.fact(vs["1"], "ship_type")), ("ANNA", "Cargo")); expect("ship type filter", sorted(A.by_ship_type(vs, {"Sailing"})), ["5"])
expect("class filter", sorted(A.collect(iter(R), classes={"Class B"})[0]), ["5"]); expect("hours", sorted(A.collect(iter(R), hours=(1, 6))[0]), ["1", "5"]); expect("extent", sorted(A.collect(iter(R), extent=(10, 56.5, 12, 58))[0]), ["5"]); expect("mmsi", sorted(A.collect(iter(R), mmsi={"2"})[0]), ["2"])
v = A.Vessel("9"); [(v.t.append(i * 60), v.x.append(x), v.y.append(55.0), v.sog.append(1)) for i, x in enumerate([5.0, 12.0, 12.001, 12.002, 12.003, 12.004, 12.005])]
expect("a wrong first position gives way", [len(x) for x in A.tracks(v)[0]], [6])
expect("simplify keeps corners", A.simplify([(12, 55), (12.001, 55), (12.002, 55), (12.002, 55.001)], 5), [0, 2, 3])
expect("addresses", (A.day_url(datetime.date(2024, 3, 1)), A.day_url(datetime.date(2025, 2, 27))), ("http://aisdata.ais.dk/2024/aisdk-2024-03-01.zip", "http://aisdata.ais.dk/aisdk-2025-02-27.zip"))
b = io.BytesIO(); z = zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED); z.writestr("a.csv", "\n".join(R)); z.close(); raw = b.getvalue()
expect("zip in small chunks", list(A.lines_of(A.unzip_stream(raw[i:i + 7] for i in range(0, len(raw), 7)))), R)
def broken(x):
    try: list(A.lines_of(A.unzip_stream([x]))); return False
    except A.AisError: return True
expect("half a zip is reported", broken(raw[:len(raw) // 3])); expect("not a zip is reported", broken(b"<html>" + b" " * 50))
p = os.path.join(tempfile.mkdtemp(), "aisdk-2026-11-22.zip"); open(p, "wb").write(raw); out = p[:-4] + ".gpkg"; pts = p[:-4] + "_p.gpkg"
alg = ais_days.AisDays(); alg.initAlgorithm(); ctx, fb = QgsProcessingContext(), QgsProcessingFeedback()
ctx.setProject(QgsProject.instance())
from qgis.PyQt.QtCore import QDateTime, QDate, QTime
P = {"DATE": QDateTime(QDate(2026, 11, 22), QTime(0, 0)), "FILE": p, "OUTPUT": out, "POINTS": pts}
res, ok = alg.run(P, ctx, fb); expect("the algorithm runs", ok); del res
l = QgsVectorLayer(out, "l", "ogr"); fs = sorted(l.getFeatures(), key=lambda f: (f["mmsi"], f["line_no"])); expect("lines", [(f["mmsi"], f["line_no"], f["name"], f["positions"]) for f in fs], [(1, 1, "ANNA", 3), (1, 2, "ANNA", 2), (5, 1, "BÅD", 2)])
expect("time, destination, size", (fs[0]["start_time"].toString("yyyy-MM-dd HH:mm:ss"), fs[0]["destination"], fs[0]["length"]), ("2026-11-22 00:00:00", "A, B", 100.0)); expect("points", QgsVectorLayer(pts, "p", "ogr").featureCount(), 7); expect("length about right", abs(fs[0]["length_m"] - 334) < 2)
expect("wrong hours refused", alg.checkParameterValues({**P, "HOUR_FROM": 10, "HOUR_TO": 9}, ctx)[0], False)
if os.environ.get("AIS_FILE"):
    import time; t0 = time.time(); res, ok = alg.run({**P, "FILE": os.environ["AIS_FILE"], "OUTPUT": os.environ["AIS_OUT"], "POINTS": None}, ctx, fb); print(fb.textLog()[-700:], ok, round(time.time() - t0), "s")
if os.environ.get("AIS_NET"):
    import time; t0 = time.time(); res, ok = alg.run({**P, "FILE": None, "DATE": QDateTime(QDate(2024, 3, 2), QTime(0, 0)), "EXTENT": "12.3,12.9,55.8,56.2 [EPSG:4326]", "OUTPUT": out[:-5] + "_n.gpkg", "POINTS": None}, ctx, fb); print(fb.textLog()[-500:], ok, round(time.time() - t0), "s")
print("failures:", fails); app.exitQgis(); sys.exit(bool(fails))
