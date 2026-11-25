"""AIS days: choose a date, get the vessels' movements as lines (Beredskabsstyrelsen, aisdata.ais.dk).

The source has one file per day for many years: too many for the panel's tree, so the panel opens this tool instead.
"""
import datetime

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsFeature, QgsFeatureSink, QgsField, QgsFields, QgsGeometry, QgsLineString, QgsPoint, QgsProcessing,
    QgsProcessingAlgorithm, QgsProcessingException, QgsProcessingLayerPostProcessorInterface, QgsProcessingParameterDateTime,
    QgsProcessingParameterEnum, QgsProcessingParameterExtent, QgsProcessingParameterFeatureSink, QgsProcessingParameterFile,
    QgsProcessingParameterNumber, QgsProcessingParameterString, QgsWkbTypes,
)
from qgis.PyQt.QtCore import QDate, QDateTime, QTime

from . import ais_core as ais
from .compat import DOUBLE, INTEGER64, STRING, advanced

try:
    from qgis.PyQt.QtCore import QMetaType
    DATETIME = QMetaType.Type.QDateTime
    QgsField("probe", DATETIME)
except (AttributeError, TypeError):
    from qgis.PyQt.QtCore import QVariant
    DATETIME = QVariant.DateTime

SHIP_TYPES = ["Cargo", "Tanker", "Passenger", "Fishing", "HSC", "Pleasure", "Sailing", "Tug", "Towing", "Towing long/wide", "Pilot", "SAR", "Dredging", "Military",
              "Law enforcement", "Port tender", "Diving", "Anti-pollution", "Medical", "WIG", "Other", "Reserved", "Undefined"]
CLASSES = ["Class A (larger ships; required)", "Class B (smaller vessels; voluntary)", "Search and rescue"]
CLASS_VALUES = [{"Class A"}, {"Class B"}, {"SAR Airborne", "Search and Rescue Transponder"}]
LINE_FIELDS = [("mmsi", INTEGER64), ("line_no", INTEGER64), ("name", STRING), ("callsign", STRING), ("imo", STRING), ("ship_type", STRING), ("mobile_type", STRING),
               ("length", DOUBLE), ("width", DOUBLE), ("draught", DOUBLE), ("destination", STRING), ("start_time", DATETIME), ("end_time", DATETIME),
               ("positions", INTEGER64), ("length_m", DOUBLE), ("mean_knots", DOUBLE)]
POINT_FIELDS = [("mmsi", INTEGER64), ("line_no", INTEGER64), ("name", STRING), ("ship_type", STRING), ("time", DATETIME), ("knots", DOUBLE)]


def qt_time(seconds):
    # The source's clock, without a time zone (it states none).
    t = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(seconds))
    return QDateTime(QDate(t.year, t.month, t.day), QTime(t.hour, t.minute, t.second))


def number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


class Temporal(QgsProcessingLayerPostProcessorInterface):
    """Ready for the Temporal Controller."""
    kept = []

    def __init__(self, start, end=None):
        super().__init__()
        self.start, self.end = start, end
        Temporal.kept.append(self)

    def postProcessLayer(self, layer, context, feedback):
        from qgis.core import Qgis
        p = layer.temporalProperties()
        try:
            mode = Qgis.VectorTemporalMode
            p.setMode(mode.FeatureDateTimeStartAndEndFromFields if self.end else mode.FeatureDateTimeInstantFromField)
        except AttributeError:
            p.setMode(p.ModeFeatureDateTimeStartAndEndFromFields if self.end else p.ModeFeatureDateTimeInstantFromField)
        p.setStartField(self.start)
        if self.end:
            p.setEndField(self.end)
        p.setIsActive(True)


class AisDays(QgsProcessingAlgorithm):
    DATE, DAYS, EXTENT, HOUR_FROM, HOUR_TO, CLASS, TYPES, MMSI, GAP, KNOTS, SIMPLIFY, MINIMUM, FILE, OUTPUT, POINTS = (
        "DATE", "DAYS", "EXTENT", "HOUR_FROM", "HOUR_TO", "CLASSES", "SHIP_TYPES", "MMSI", "MAX_GAP", "MAX_KNOTS", "SIMPLIFY", "MIN_LENGTH", "FILE", "OUTPUT", "POINTS")

    def name(self): return "aisdays"
    def displayName(self): return "AIS days: vessel movements as lines"
    def group(self): return "Data access"
    def groupId(self): return "dataaccess"
    def createInstance(self): return AisDays()

    def shortHelpString(self):
        return (
            "Gets one or more days of AIS messages (Beredskabsstyrelsen, aisdata.ais.dk), cleans them and joins each vessel's positions into "
            "<b>lines</b>. Days from 1 March 2024; the newest are about three days old.<br><br>"
            "A day is a download of about <b>0.6 GB</b> and takes some minutes. It is read as it arrives and not stored; the filters below limit what is kept, not what is downloaded.<br><br>"
            "<b>Cleaning</b>: base stations and aids to navigation are left out; one position per vessel and second; impossible positions and jumps are dropped. "
            "A line ends where the vessel was silent for longer than the gap you choose, and at midnight.<br>"
            "The result is set up for the Temporal Controller (start_time, end_time). The source does not state its time zone.<br><br>"
            "Name, type, size, draught and destination are typed in by the crew; the value sent most often is used. Vessels without AIS are not seen.<br><br>"
            "<b>Persons</b>: Beredskabsstyrelsen's conditions say that the data must not be combined with other data so that persons become identifiable "
            "without permission from Datatilsynet. Looking up the owner of a private boat can fall under this.")

    def initAlgorithm(self, config=None):
        first = QDateTime(QDate(ais.FIRST_DAILY.year, ais.FIRST_DAILY.month, ais.FIRST_DAILY.day), QTime(0, 0))
        latest = QDateTime(QDate.currentDate().addDays(-3), QTime(0, 0))
        self.addParameter(QgsProcessingParameterDateTime(self.DATE, "Date", QgsProcessingParameterDateTime.Type.Date, latest, False, first, QDateTime(QDate.currentDate(), QTime(0, 0))))
        self.addParameter(QgsProcessingParameterNumber(self.DAYS, "Number of days (0.6 GB each)", QgsProcessingParameterNumber.Type.Integer, 1, False, 1, 14))
        self.addParameter(QgsProcessingParameterExtent(self.EXTENT, "Only inside this area", None, True))
        self.addParameter(QgsProcessingParameterEnum(self.CLASS, "Kind of sender", CLASSES, True, [0, 1]))
        self.addParameter(QgsProcessingParameterEnum(self.TYPES, "Only these ship types (none chosen = all)", SHIP_TYPES, True, None, True))
        self.addParameter(QgsProcessingParameterNumber(self.HOUR_FROM, "From hour", QgsProcessingParameterNumber.Type.Integer, 0, False, 0, 23))
        self.addParameter(QgsProcessingParameterNumber(self.HOUR_TO, "To hour", QgsProcessingParameterNumber.Type.Integer, 24, False, 1, 24))
        self.addParameter(advanced(QgsProcessingParameterString(self.MMSI, "Only these MMSI numbers (comma between)", "", False, True)))
        self.addParameter(advanced(QgsProcessingParameterNumber(self.GAP, "New line after a silence of (minutes)", QgsProcessingParameterNumber.Type.Integer, 30, False, 1, 1440)))
        self.addParameter(advanced(QgsProcessingParameterNumber(self.KNOTS, "Drop positions that need a speed above (knots)", QgsProcessingParameterNumber.Type.Double, 100, False, 1)))
        self.addParameter(advanced(QgsProcessingParameterNumber(self.SIMPLIFY, "Simplify lines by (metres, 0 = keep every position)", QgsProcessingParameterNumber.Type.Double, 5, False, 0)))
        self.addParameter(advanced(QgsProcessingParameterNumber(self.MINIMUM, "Leave out lines shorter than (metres)", QgsProcessingParameterNumber.Type.Double, 0, False, 0)))
        self.addParameter(advanced(QgsProcessingParameterFile(self.FILE, "Use a day already downloaded (zip or csv) instead", optional=True)))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Vessel lines", QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(self.POINTS, "Cleaned positions", QgsProcessing.TypeVectorPoint, None, True, False))

    def checkParameterValues(self, parameters, context):
        if self.parameterAsInt(parameters, self.HOUR_FROM, context) >= self.parameterAsInt(parameters, self.HOUR_TO, context):
            return False, "'From hour' must be before 'To hour'."
        return super().checkParameterValues(parameters, context)

    def _read(self, url, path, progress, feedback):
        stopped = feedback.isCanceled
        if path:
            chunks = ais.read_file(path, progress, stopped)
            return ais.lines_of(chunks if path.lower().endswith(".csv") else ais.unzip_stream(chunks))
        return ais.lines_of(ais.unzip_stream(ais.download(url, progress, stopped)))

    def processAlgorithm(self, parameters, context, feedback):
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        qdate = self.parameterAsDateTime(parameters, self.DATE, context).date()
        first = datetime.date(qdate.year(), qdate.month(), qdate.day())
        days = self.parameterAsInt(parameters, self.DAYS, context)
        path = self.parameterAsFile(parameters, self.FILE, context)
        extent = None
        if parameters.get(self.EXTENT):
            r = self.parameterAsExtent(parameters, self.EXTENT, context, wgs84)
            if not r.isNull() and not r.isEmpty():
                extent = (r.xMinimum(), r.yMinimum(), r.xMaximum(), r.yMaximum())
        chosen = self.parameterAsEnums(parameters, self.CLASS, context)
        classes = set().union(*(CLASS_VALUES[i] for i in chosen)) if chosen else None
        wanted = {SHIP_TYPES[i] for i in self.parameterAsEnums(parameters, self.TYPES, context)} if parameters.get(self.TYPES) not in (None, "", []) else set()   # QGIS gives the first type when none is chosen
        hours = (self.parameterAsInt(parameters, self.HOUR_FROM, context), self.parameterAsInt(parameters, self.HOUR_TO, context))
        mmsi = {m.strip() for m in self.parameterAsString(parameters, self.MMSI, context).split(",") if m.strip()} or None
        gap = self.parameterAsInt(parameters, self.GAP, context) * 60
        knots, tolerance, minimum = (self.parameterAsDouble(parameters, k, context) for k in (self.KNOTS, self.SIMPLIFY, self.MINIMUM))

        def fields_of(spec):
            f = QgsFields()
            for name, kind in spec:
                f.append(QgsField(name, kind))
            return f
        line_fields, point_fields = fields_of(LINE_FIELDS), fields_of(POINT_FIELDS)
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, line_fields, QgsWkbTypes.LineString, wgs84)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        points, points_dest = self.parameterAsSink(parameters, self.POINTS, context, point_fields, QgsWkbTypes.Point, wgs84)

        dates = [None] if path else [first + datetime.timedelta(days=i) for i in range(days)]
        total_lines = 0
        for number_, day in enumerate(dates):
            url = None if path else ais.day_url(day)
            feedback.pushInfo(f"Reading {path or url}")
            shown = [-1]

            def progress(got, total):
                part = int(90 * got / total) if total else 0
                if part != shown[0]:
                    shown[0] = part
                    feedback.setProgress((number_ * 100 + part) / len(dates))
            try:
                vessels, n = ais.collect(self._read(url, path, progress, feedback), extent, classes, None, None if hours == (0, 24) else hours, mmsi)
            except ais.AisError as error:
                if feedback.isCanceled():
                    break
                raise QgsProcessingException(str(error))
            except OSError as error:
                code = getattr(error, "code", None)
                if code in (403, 404):
                    raise QgsProcessingException(f"There is no file for {day} at the source (the newest days are about three days old).")
                raise QgsProcessingException(f"Could not read {path or url}: {error}")
            if feedback.isCanceled():
                break
            vessels = ais.by_ship_type(vessels, wanted)
            feedback.pushInfo(f"  {n['rows']:,} rows; left out: {n['not_vessels']:,} base stations and aids to navigation, {n['outside']:,} outside the filters, "
                              f"{n['impossible']:,} impossible positions, {n['repeated']:,} repeated, {n['unreadable']:,} unreadable. Kept {n['kept']:,} positions.")
            dropped = made = 0
            for count, vessel in enumerate(vessels.values()):
                if feedback.isCanceled():
                    break
                lines, d = ais.tracks(vessel, gap, knots)
                dropped += d
                facts = {k: ais.fact(vessel, k) for k, _ in ais.FACTS}
                line_no = 0
                for line in lines:
                    xy = [(vessel.x[i], vessel.y[i]) for i in line]
                    kept = ais.simplify(xy, tolerance)
                    length = sum(ais.metres(*xy[a], *xy[b]) for a, b in zip(kept, kept[1:]))
                    if length <= 0 or length < minimum:
                        continue
                    line_no += 1
                    speeds = [vessel.sog[i] for i in line if vessel.sog[i] >= 0]
                    f = QgsFeature(line_fields)
                    f.setGeometry(QgsGeometry(QgsLineString([xy[i][0] for i in kept], [xy[i][1] for i in kept])))
                    f.setAttributes([int(vessel.mmsi), line_no, facts["name"], facts["callsign"], facts["imo"], facts["ship_type"], facts["mobile_type"], number(facts["length"]), number(facts["width"]),
                                     number(facts["draught"]), facts["destination"], qt_time(vessel.t[line[0]]), qt_time(vessel.t[line[-1]]), len(line), round(length, 1),
                                     round(sum(speeds) / len(speeds), 2) if speeds else None])
                    sink.addFeature(f, QgsFeatureSink.Flag.FastInsert)
                    made += 1
                    if points is not None:
                        for i in line:
                            p = QgsFeature(point_fields)
                            p.setGeometry(QgsGeometry(QgsPoint(vessel.x[i], vessel.y[i])))
                            p.setAttributes([int(vessel.mmsi), line_no, facts["name"], facts["ship_type"], qt_time(vessel.t[i]), round(vessel.sog[i], 1) if vessel.sog[i] >= 0 else None])
                            points.addFeature(p, QgsFeatureSink.Flag.FastInsert)
                if count % 50 == 0:
                    feedback.setProgress((number_ * 100 + 90 + 10 * count / max(len(vessels), 1)) / len(dates))
            total_lines += made
            feedback.pushInfo(f"  {len(vessels):,} vessels, {made:,} lines; {dropped:,} positions dropped as impossible jumps.")
        if not total_lines and not feedback.isCanceled():
            feedback.reportError("No vessel lines for these choices.")
        for layer_id, times in ((dest, ("start_time", "end_time")), (points_dest, ("time",))):
            if layer_id and context.willLoadLayerOnCompletion(layer_id):
                context.layerToLoadOnCompletionDetails(layer_id).setPostProcessor(Temporal(*times))
        return {self.OUTPUT: dest, **({self.POINTS: points_dest} if points is not None else {})}
