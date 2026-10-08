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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok ", name)
