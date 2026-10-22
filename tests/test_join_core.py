"""Matching core of the auto-join; plain Python, no QGIS or network needed.

  python3 tests/test_join_core.py      (or: pytest tests/test_join_core.py)
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from semanticgis.join import KeyIndex, RefUnit, detect, norm_id, norm_name


class Source:
    def __init__(self, label): self.label = label


def lau():
    return KeyIndex(Source("LAU"), [RefUnit("DK_101", "København", "DK"), RefUnit("DK_671", "Struer", "DK"),
                                    RefUnit("SE_0180", "Stockholm", "SE")], country="DK")


def nuts():
    return KeyIndex(Source("NUTS"), [RefUnit("DK011", "Byen København", "DK", 3), RefUnit("DK01", "Hovedstaden", "DK", 2),
                                     RefUnit("DK05", "Nordjylland", "DK", 2), RefUnit("DK050", "Nordjylland", "DK", 3)])


def test_norm_id():
    assert norm_id("0101") == norm_id(101) == norm_id("101.0") == norm_id("101 København") == "101"
    assert norm_id("dk011") == "DK011" and norm_id(" DK_671 ") == "DK_671"
    assert norm_id("") is None and norm_id(None) is None


def test_norm_name():
    assert norm_name("Struer Kommune") == "struer"
    assert norm_name("Region Hovedstaden") == "hovedstaden"


def test_national_codes_are_scoped_to_the_country():
    index = lau()
    assert index.lookup("0671", "national").id == "DK_671"
    assert index.lookup("180", "national") is None


def test_names_shared_across_levels_need_a_level():
    index = nuts()
    assert index.lookup("Nordjylland", "name") is None
    assert index.lookup("Nordjylland", "name", 2).id == "DK05"


def test_detect_ranks_ids_and_reports_unmatched():
    candidates = detect({"kode": ["101", "0671", "999"], "nuts": ["DK011", "DK01"]}, [lau(), nuts()])
    assert candidates[0].field == "nuts" and candidates[0].mode == "id" and candidates[0].rate == 1.0
    kode = next(c for c in candidates if c.field == "kode")
    assert kode.mode == "national" and kode.matched == 2 and kode.unmatched == ["999"]


def test_detect_ignores_weak_matches():
    assert detect({"x": ["101", "a", "b", "c", "d"]}, [lau()]) == []




def kk():
    """Københavns Kommune's levels: codes are disjoint across levels (as in Statistikbanken)."""
    bydel = KeyIndex(Source("Bydel"), [RefUnit(str(1000 + i), f"Bydel {i}") for i in range(1, 11)])
    udvalg = KeyIndex(Source("Lokaludvalg"), [RefUnit(str(2000 + i), f"Udvalg {i}") for i in range(1, 13)])
    kvarter = KeyIndex(Source("Kvarter"), [RefUnit(str(20100 + i), f"Kvarter {i}") for i in range(1, 68)])
    rode = KeyIndex(Source("Rode"), [RefUnit(f"{i:03d}", f"Rode {i}") for i in range(1, 401)])
    return [bydel, udvalg, kvarter, rode]


def test_mixed_levels_are_all_offered():
    # KKBEF8: the city total, 10 districts, 12 committees and 67 quarters in one column
    codes = ["1000"] + [str(1000 + i) for i in range(1, 11)] + [str(2000 + i) for i in range(1, 13)] + [str(20100 + i) for i in range(1, 68)]
    found = {c.source.label: c for c in detect({"kode": codes}, kk())}
    assert set(found) == {"Kvarter", "Lokaludvalg", "Bydel"}
    assert (found["Bydel"].matched, found["Lokaludvalg"].matched, found["Kvarter"].matched) == (10, 12, 67)
    assert "mixes levels" in found["Bydel"].describe() and "only the Bydel rows" in found["Bydel"].describe()


def test_count_columns_are_not_offered_as_codes():
    # KKBOL4: rode codes 001-400 plus counts between 0 and 400 in the value columns
    codes = ["1000"] + [f"{i:03d}" for i in range(1, 401)]
    counts = [str(v) for v in (0, 12, 38, 207, 119, 55, 0, 76871, 401, 14, 87)]
    found = detect({"code": codes, "Ejerbolig": counts}, kk())
    assert [(c.field, c.source.label) for c in found] == [("code", "Rode")]


def test_single_level_tables_are_unchanged():
    found = detect({"code": [f"{i:03d}" for i in range(1, 401)]}, kk())
    assert len(found) == 1 and not found[0].mixed and found[0].matched == 400


def test_numbering_coincidences_are_not_mixtures():
    # Rode codes 001-400 overlap Danish municipality codes (101 København, 147 Frederiksberg, …):
    # a rode table must offer roder only, and its count columns nothing.
    municipalities = KeyIndex(Source("LAU"), [RefUnit(f"DK_{k}", f"Kommune {k}", "DK") for k in (101, 147, 151, 153, 155, 157, 159, 161, 163, 165, 167, 169, 173, 175, 183, 185, 187, 190, 201, 210, 217, 219, 223, 230, 240, 250, 253, 259, 260, 265, 269, 270, 306, 316, 320, 326, 329, 330, 336, 340, 350, 360, 370, 376, 390, 400)], country="DK")
    codes = ["1000", "999"] + [f"{i:03d}" for i in range(1, 401)]
    counts = [str(v) for v in (0, 12, 38, 207, 119, 55, 401, 1330, 76871, 718, 416, 523, 980, 14, 87, 101, 147)]   # as in KKBOL4
    found = detect({"code": codes, "Ejerbolig": counts}, kk() + [municipalities])
    assert [(c.field, c.source.label, bool(c.mixed)) for c in found] == [("code", "Rode", False)]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok ", name)
