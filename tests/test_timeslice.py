"""Periods, versions and 'as it was on a date' without QGIS.   python3 tests/test_timeslice.py"""
import datetime as dt
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "semanticgis"))
import timeslice as T

D = dt.date
fails = 0
def expect(label, got, want=True):
    global fails
    ok = got == want; fails += not ok; print(("ok   " if ok else "FAIL ") + label + ("" if ok else f": {got!r} != {want!r}"))

expect("year", T.period_range("2025"), (D(2025, 1, 1), D(2026, 1, 1)))
expect("quarter, Danmarks Statistik", T.period_range("2025K4"), (D(2025, 10, 1), D(2026, 1, 1)))
expect("quarter, Eurostat", T.period_range("2025-Q2"), (D(2025, 4, 1), D(2025, 7, 1)))
expect("month", T.period_range("2025M03"), (D(2025, 3, 1), D(2025, 4, 1)))
expect("month, ISO", T.period_range("2025-12"), (D(2025, 12, 1), D(2026, 1, 1)))
expect("day", T.period_range("2025-03-05"), (D(2025, 3, 5), D(2025, 3, 6)))
expect("half year", T.period_range("2025H2"), (D(2025, 7, 1), D(2026, 1, 1)))
expect("week", T.period_range("2025U02")[0], D(2025, 1, 6))
expect("school year", T.period_range("2024/2025"), (D(2024, 1, 1), D(2026, 1, 1)))
expect("a year read as a number", T.period_range("2020.0"), (D(2020, 1, 1), D(2021, 1, 1)))
expect("not periods", [T.period_range(x) for x in ("101", "København", "", None, "12345")], [None] * 5)
expect("the time column of a StatBank table (code preferred)",
       T.time_field({"SOGN_kode": ["7080"], "Tid_kode": ["2020K1", "2021K1"], "Tid": ["2020K1", "2021K1"], "INDHOLD": ["2020", "2021"]}), "Tid_kode")
expect("a column of years not named as time is not taken", T.time_field({"INDHOLD": ["2020", "2021"]}), None)
expect("latest period in a wide table's names", T.latest_period_start(["OMRAADE_kode", "Kvinder_2025K4", "Kvinder_2026K3", "Maend_2026K3"]), D(2026, 7, 1))
expect("DAGI stamps are Danish midnight in UTC", (T.local_date("2018-01-21T23:00:00.000000Z"), T.local_date("2024-06-30T22:00:00Z")), (D(2018, 1, 22), D(2024, 7, 1)))

vs = T.versions_from([
    {"sognekode": "7080", "navn": "Islands Brygges", "virkningFra": "2018-01-21T23:00:00Z", "virkningTil": "2020-05-01T22:00:00Z"},
    {"sognekode": "7080", "navn": "Islands Brygges", "virkningFra": "2020-05-01T22:00:00Z", "virkningTil": "2024-11-30T23:00:00Z"},
    {"sognekode": "9345", "navn": "Islands Brygges", "virkningFra": "2024-11-30T23:00:00Z", "virkningTil": None}], "sognekode")
expect("the version valid on a date", T.pick(vs, D(2021, 1, 1))["7080"].valid_from, D(2020, 5, 2))
expect("an ended unit is not valid after", set(T.pick(vs, D(2025, 1, 1))), {"9345"})
expect("the day a version ends belongs to the next", T.pick(vs, D(2024, 12, 1)).keys() == {"9345"})
expect("before the history: the first units", set(T.pick(vs, D(2010, 1, 1), begins=D(2018, 1, 22))), {"7080"})
expect("without a beginning nothing before", T.pick(vs, D(2010, 1, 1)), {})

# 'as it was on a date' in a register: BBR's buildings (column map from the catalogue's time_layers)
TL = {"bbr-bygning": [{"layer": "bbr_v001:bygning_hist", "form": "history", "status_list": "bbr-livscyklus",
                       "columns": {"valid-time-from": "virkningFra", "valid-time-to": "virkningTil", "registration-time-from": "registreringFra",
                                   "registration-time-to": "registreringTil", "lifecycle-status": "status", "register-local-id": "id_lokalId"}}]}
tf = T.time_fields(["id_lokalId", "virkningFra", "virkningTil", "registreringFra", "registreringTil", "status", "byg026Opførelsesår"], TL, "bbr_v001:bygning_hist")
expect("time fields from the column map", (tf.form, tf.status, tf.identity, tf.registered_to, tf.status_list), ("history", "status", "id_lokalId", "registreringTil", "bbr-livscyklus"))
expect("time fields guessed without the catalogue", (lambda g: (g.valid_from, g.identity, g.form))(T.time_fields(["VirkningFra", "virkningTil", "id_lokalId"], None, "bbr_v001:bygning_current")), ("VirkningFra", "id_lokalId", "current"))
expect("the layer of a WFS source", T.layer_key("pagingEnabled='true' typename='bbr_v001:bygning_hist' url='https://wfs.datafordeler.dk/BBR/BBR_WFS/1.0.0/WFS'"), "bbr_v001:bygning_hist")
SR = {"lists": {"bbr-livscyklus": {"codes": {"2": "planned", "3": "under-construction", "6": "exists", "7": "exists", "10": "ended"}}}}
ex = T.existing_codes(SR, "bbr-livscyklus")
expect("codes that count as existing", ex, {"6", "7"})
expect("with under construction", T.existing_codes(SR, "bbr-livscyklus", ("exists", "under-construction")), {"3", "6", "7"})
row = lambda vf, vt, st, rt=None: {"virkningFra": vf, "virkningTil": vt, "registreringFra": "2009-01-01T00:00:00Z", "registreringTil": rt, "status": st}
t = T.day_instant(D(2015, 1, 1))
expect("valid on the date", T.valid_on(row("2010-01-01T00:00:00.0000000+01:00", None, "6"), tf, t, existing=ex))
expect("not yet valid", T.valid_on(row("2016-01-01T00:00:00+01:00", None, "6"), tf, t, existing=ex), False)
expect("ended before", T.valid_on(row("2010-01-01T00:00:00+01:00", "2014-01-01T00:00:00+01:00", "6"), tf, t, existing=ex), False)
expect("a status that does not count", T.valid_on(row("2010-01-01T00:00:00+01:00", None, "2"), tf, t, existing=ex), False)
expect("a registration since replaced is not what is known now", T.valid_on(row("2010-01-01T00:00:00+01:00", None, "6", "2012-01-01T00:00:00Z"), tf, t, existing=ex), False)
expect("... but it is what was known in 2011", T.valid_on(row("2010-01-01T00:00:00+01:00", None, "6", "2012-01-01T00:00:00Z"), tf, t, known=T.day_instant(D(2011, 1, 1)), existing=ex))
print("FAILS:", fails); sys.exit(1 if fails else 0)
