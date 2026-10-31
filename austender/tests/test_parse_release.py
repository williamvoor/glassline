import json
from datetime import date
from decimal import Decimal

from austender.normalise import canberra_date, financial_year, parse_amount, parse_release
from austender.testing import SEED_DIR


def seed(name):
    return json.loads((SEED_DIR / f"{name}.json").read_text())


def test_canberra_date_crosses_midnight():
    assert canberra_date("2026-06-30T14:00:00Z") == date(2026, 7, 1)
    assert canberra_date("2026-06-30T13:59:59Z") == date(2026, 6, 30)
    # daylight saving: AEDT is UTC+11
    assert canberra_date("2024-12-31T13:30:00Z") == date(2025, 1, 1)
    assert canberra_date(None) is None
    assert canberra_date("not a date") is None


def test_financial_year_boundaries():
    assert financial_year(date(2025, 6, 30)) == "2024-25"
    assert financial_year(date(2025, 7, 1)) == "2025-26"
    assert financial_year(date(1999, 12, 31)) == "1999-00"


def test_amount_is_parsed_as_decimal_not_float():
    assert parse_amount("405433.60") == Decimal("405433.60")
    assert parse_amount("0.1") == Decimal("0.10")
    assert parse_amount("12345678901234.56") == Decimal("12345678901234.56")
    assert parse_amount("") is None
    assert parse_amount("n/a") is None
    assert parse_amount(None) is None


def test_parent_release():
    p = parse_release(seed("CN9000003"))
    assert p.kind == "contract"
    assert p.notice_id == "CN9000003"
    assert p.publish_date == date(2024, 10, 1)
    assert p.financial_year == "2024-25"
    assert p.value == Decimal("200000.00")
    assert p.unspsc == "80100000"
    assert p.procurement_method == "limited"
    assert p.limited_tender_exempt is False
    assert p.limited_tender_reason_code == "AD"
    assert [s.supplier_key for s in p.suppliers] == ["76200300401", "38200300403"]
    assert [s.ordinal for s in p.suppliers] == [1, 2]
    assert p.agency_key == "84100200302"


def test_amendment_release_points_at_parent_and_has_its_own_id():
    p = parse_release(seed("CN9000007-A3"))
    assert p.kind == "amendment"
    assert p.notice_id == "CN9000007"  # contracts[0].id is the parent
    assert p.amendment_id == "CN9000007-A3"
    assert p.amendment_seq == 3
    assert p.value == Decimal("90000.00")
    assert p.publish_date is None  # the release date is a re-publication stamp, never an amendment date


def test_supplier_abn_and_keys():
    bad = parse_release(seed("CN9000010")).suppliers[0]
    assert bad.abn_raw == "12345678901" and bad.abn is None and bad.supplier_key == "name:bad abn co"
    none = parse_release(seed("CN9000009")).suppliers[0]
    assert none.abn_raw is None and none.abn is None and none.supplier_key == "name:no abn trading"
    overseas = parse_release(seed("CN9000015")).suppliers[0]
    assert overseas.is_overseas and overseas.country == "GERMANY"
    assert not parse_release(seed("CN9000001")).suppliers[0].is_overseas


def test_unplaceable_releases_return_none():
    assert parse_release({"id": "x", "date": "2025-01-01T00:00:00Z", "contracts": []}) is None
    assert parse_release({"id": "x", "contracts": [{"id": "CN1"}]}) is None
    amend_without_id = {"id": "x", "date": "2025-01-01T00:00:00Z", "tag": ["contractAmendment"], "contracts": [{"id": "CN1"}]}
    assert parse_release(amend_without_id) is None


def test_withheld_supplier_names_get_a_key_per_notice():
    """"Name Withheld" is a placeholder, not a supplier: two such notices must not merge into one supplier."""
    a, b = seed("CN9000009"), seed("CN9000010")  # S4 (no ABN) and S5 (invalid ABN)
    for d, name in ((a, "Name Withheld"), (b, "Name withheld")):
        next(p for p in d["parties"] if "supplier" in p["roles"])["name"] = name
    assert parse_release(a).suppliers[0].supplier_key == "withheld:CN9000009"
    assert parse_release(b).suppliers[0].supplier_key == "withheld:CN9000010"


def test_withheld_supplier_name_with_a_valid_abn_keeps_the_abn_key():
    d = seed("CN9000001")
    next(p for p in d["parties"] if "supplier" in p["roles"])["name"] = "Name Withheld"
    assert parse_release(d).suppliers[0].supplier_key == "57200300402"
