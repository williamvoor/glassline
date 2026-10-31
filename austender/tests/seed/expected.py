"""Expected results for the seed releases in this directory, worked out by hand (not by the code under test).

Entities
  Agencies   A1 Department of Fictional Affairs (ABN 14100200301)
             A2 ABN 84100200302, published as "Bureau of Imaginary Works", renamed in the latest notice
                (CN9000016) to "Imaginary Works Agency": the display name is the latest one everywhere.
  Suppliers  S1 Acme Widgets (ABN 76200300401; also published as "ACME WIDGETS PTY. LTD." once)
             S2 Borealis Consulting (ABN 57200300402)      S3 Cobalt & Sons (ABN 38200300403)
             S4 No Abn Trading P/L (no ABN)                S5 Bad Abn Co (ABN 12345678901 fails the checksum)
             S6 Globex GmbH (no ABN, Germany)              S7 Delta Dynamics (ABN 51200300407)
  S1's majority spelling wins even though its latest notice (CN9000018) uses the odd one. S7 is published
  three ways, once each ("... Limited" C5, "... Ltd" C8, "... Pty Ltd" C16): the tie goes to the latest notice.

Contracts (value = current value after amendments; publish date in Australia/Canberra)
  CN9000001 2024-08-15 FY24-25 A1 S2        100,000  plain, open
  CN9000002 2024-09-10 FY24-25 A1 S1        500,000  CANCELLED notice, excluded from everything but mart.contract
  CN9000003 2024-10-01 FY24-25 A2 S1+S3     200,000  two suppliers, 100,000 each; limited tender AD
  CN9000004 2024-11-05 FY24-25 A1 S1+S2+S3  300,000  three suppliers, 100,000 each; selective
  CN9000005 2025-01-01 FY24-25 A2 S7        50,000 -> A1 80,000 -> A2 120,000 (published 2024-12-31T13:30Z = 01 Jan, AEDT)
  CN9000006 2025-01-20 FY24-25 A1 S2        70,000 -> A1 40,000 (value lowered) -> A2 no value; current 40,000
  CN9000007 2025-02-10 FY24-25 A1 S3        60,000 -> A1 80,000 -> A2 200,000 CANCELLED -> A3 90,000; current 90,000
  (CN9000005 A1/A2 and CN9000007 A1/A3 have their release ids swapped, so release-id order is not k order.)
  CN9000008 2025-02-20 FY24-25 A1 S7        10,000 -> A1 15,000 -> A2 99,000 CANCELLED (highest k cancelled); current 15,000
  CN9000009 2025-03-03 FY24-25 A1 S4        25,000  supplier without ABN
  CN9000010 2025-03-04 FY24-25 A2 S5        35,000  supplier with invalid ABN
  CN9000011 2025-04-01 FY24-25 A1 S1        45,000  S1 under a second spelling
  CN9000012 2025-04-15 FY24-25 A2 S1        55,000
  CN9000013 2025-07-01 FY25-26 A1 S2        75,000  published 2025-06-30T23:30Z = 1 Jul 09:30 AEST: next FY
  CN9000014 2025-06-30 FY24-25 A1 S2        25,000  published 2025-06-30T13:59:59Z = 23:59:59 AEST: still FY24-25
  CN9000015 2025-08-12 FY25-26 A2 S6        15,000  overseas; S6 listed on two supplier parties (7,500 each)
  CN9000016 2025-09-01 FY25-26 A2 S7        20,000  limited tender TR; carries the new agency name
  CN9000017 2025-10-01 FY25-26 A2 S1+S2+S3  100,000  uneven split: 33,333.333... each, unrounded
  CN9000018 2025-10-02 FY25-26 A2 S1+S2+S3      200  66.666... each; with C17 each supplier gets exactly 33,400
  CN9000019 2025-10-03 FY25-26 A1 (none)      5,000  no supplier: counted for the agency, absent from supplier tables
  CN9000020 2025-10-04 FY25-26 A1 S4         (none)  no value: only in mart.contract
Other seed files: a byte-identical duplicate of CN9000001 (skipped), a release with CN9000009's release id
and a different value (recorded in ops.rejected_release, stored row unchanged), and an amendment whose
parent is not in the data set (stored in raw, absent from core).
"""

from decimal import Decimal as D

A1 = "14100200301"
A2 = "84100200302"
A1_NAME = "Department of Fictional Affairs"
A2_NAME = "Imaginary Works Agency"
S1, S2, S3, S7 = "76200300401", "57200300402", "38200300403", "51200300407"
S4 = "name:no abn trading"
S5 = "name:bad abn co"
S6 = "name:globex gmbh"

SUPPLIER_NAMES = {
    S1: "Acme Widgets Pty Ltd",
    S2: "Borealis Consulting Pty Ltd",
    S3: "Cobalt & Sons Pty Ltd",
    S4: "No Abn Trading P/L",
    S5: "Bad Abn Co Pty Ltd",
    S6: "Globex GmbH",
    S7: "Delta Dynamics Pty Ltd",
}
# ABN stored in mart.supplier (None when absent or invalid).
SUPPLIER_ABN = {S1: S1, S2: S2, S3: S3, S4: None, S5: None, S6: None, S7: S7}

RAW_RELEASES = 30  # 20 parents + 9 amendments + 1 orphan amendment
REJECTED = 1
CORE_AMENDMENTS = 9

# notice_id: (publish_date, FY, agency, original, current, amendments, cancelled amendments, suppliers, cancelled)
CONTRACTS = {
    "CN9000001": ("2024-08-15", "2024-25", A1, D("100000"), D("100000"), 0, 0, 1, False),
    "CN9000002": ("2024-09-10", "2024-25", A1, D("500000"), D("500000"), 0, 0, 1, True),
    "CN9000003": ("2024-10-01", "2024-25", A2, D("200000"), D("200000"), 0, 0, 2, False),
    "CN9000004": ("2024-11-05", "2024-25", A1, D("300000"), D("300000"), 0, 0, 3, False),
    "CN9000005": ("2025-01-01", "2024-25", A2, D("50000"), D("120000"), 2, 0, 1, False),
    "CN9000006": ("2025-01-20", "2024-25", A1, D("70000"), D("40000"), 2, 0, 1, False),
    "CN9000007": ("2025-02-10", "2024-25", A1, D("60000"), D("90000"), 3, 1, 1, False),
    "CN9000008": ("2025-02-20", "2024-25", A1, D("10000"), D("15000"), 2, 1, 1, False),
    "CN9000009": ("2025-03-03", "2024-25", A1, D("25000"), D("25000"), 0, 0, 1, False),
    "CN9000010": ("2025-03-04", "2024-25", A2, D("35000"), D("35000"), 0, 0, 1, False),
    "CN9000011": ("2025-04-01", "2024-25", A1, D("45000"), D("45000"), 0, 0, 1, False),
    "CN9000012": ("2025-04-15", "2024-25", A2, D("55000"), D("55000"), 0, 0, 1, False),
    "CN9000013": ("2025-07-01", "2025-26", A1, D("75000"), D("75000"), 0, 0, 1, False),
    "CN9000014": ("2025-06-30", "2024-25", A1, D("25000"), D("25000"), 0, 0, 1, False),
    "CN9000015": ("2025-08-12", "2025-26", A2, D("15000"), D("15000"), 0, 0, 2, False),
    "CN9000016": ("2025-09-01", "2025-26", A2, D("20000"), D("20000"), 0, 0, 1, False),
    "CN9000017": ("2025-10-01", "2025-26", A2, D("100000"), D("100000"), 0, 0, 3, False),
    "CN9000018": ("2025-10-02", "2025-26", A2, D("200"), D("200"), 0, 0, 3, False),
    "CN9000019": ("2025-10-03", "2025-26", A1, D("5000"), D("5000"), 0, 0, 0, False),
    "CN9000020": ("2025-10-04", "2025-26", A1, None, None, 0, 0, 1, False),
}

# core.amendment: (notice_id, seq) -> (value, value_delta or None if cancelled, cancelled)
AMENDMENTS = {
    ("CN9000005", 1): (D("80000"), D("30000"), False),
    ("CN9000005", 2): (D("120000"), D("40000"), False),
    ("CN9000006", 1): (D("40000"), D("-30000"), False),
    ("CN9000006", 2): (None, None, False),  # no value: does not replace A1's 40,000 as the current value
    ("CN9000007", 1): (D("80000"), D("20000"), False),
    ("CN9000007", 2): (D("200000"), None, True),
    ("CN9000007", 3): (D("90000"), D("10000"), False),  # against A1 (the previous non-cancelled), not the cancelled A2
    ("CN9000008", 1): (D("15000"), D("5000"), False),
    ("CN9000008", 2): (D("99000"), None, True),
}

# mart.contract period_start: start timestamps at or after 13:00/14:00Z fall on the next Canberra day
PERIOD_START = {
    "CN9000004": "2024-07-02",  # 2024-07-01T14:00Z = 00:00 AEST
    "CN9000005": "2025-01-01",  # 2024-12-31T13:00Z = 00:00 AEDT
}

# mart.contract period_end: from the highest-k non-cancelled version (dates in Canberra time)
PERIOD_END = {
    "CN9000001": "2025-08-15",
    "CN9000005": "2027-06-30",
    "CN9000007": "2026-12-31",  # A3; the cancelled A2 ends 2031 and must not count
    "CN9000006": "2026-01-01",
}

# mart.supplier_fy: (supplier_key, FY) -> (contracts, value)
SUPPLIER_FY = {
    (S1, "2024-25"): (4, D("300000")),   # C3 100k + C4 100k + C11 45k + C12 55k (C2 cancelled, excluded)
    (S2, "2024-25"): (4, D("265000")),   # C1 100k + C4 100k + C6 40k + C14 25k
    (S3, "2024-25"): (3, D("290000")),   # C3 100k + C4 100k + C7 90k
    (S4, "2024-25"): (1, D("25000")),
    (S5, "2024-25"): (1, D("35000")),
    (S7, "2024-25"): (2, D("135000")),   # C5 120k + C8 15k
    (S1, "2025-26"): (2, D("33400")),    # C17 33,333.33... + C18 66.66...
    (S2, "2025-26"): (3, D("108400")),   # C13 75k + C17 + C18
    (S3, "2025-26"): (2, D("33400")),
    (S6, "2025-26"): (1, D("15000")),    # two supplier rows on C15, one contract
    (S7, "2025-26"): (1, D("20000")),
}

# mart.agency_fy: (agency_key, FY) -> (contracts, value)
AGENCY_FY = {
    (A1, "2024-25"): (8, D("640000")),   # C1 100 + C4 300 + C6 40 + C7 90 + C8 15 + C9 25 + C11 45 + C14 25 (thousand)
    (A2, "2024-25"): (4, D("410000")),   # C3 200 + C5 120 + C10 35 + C12 55
    (A1, "2025-26"): (2, D("80000")),    # C13 75k + C19 5k (no supplier); C20 has no value
    (A2, "2025-26"): (4, D("135200")),   # C15 15k + C16 20k + C17 100k + C18 200
}

# mart.supplier_agency_fy: (supplier_key, agency_key, FY) -> (contracts, value)
SUPPLIER_AGENCY_FY = {
    (S1, A1, "2024-25"): (2, D("145000")),  # C4 100k + C11 45k
    (S1, A2, "2024-25"): (2, D("155000")),  # C3 100k + C12 55k
    (S2, A1, "2024-25"): (4, D("265000")),
    (S3, A1, "2024-25"): (2, D("190000")),  # C4 100k + C7 90k
    (S3, A2, "2024-25"): (1, D("100000")),  # C3
    (S4, A1, "2024-25"): (1, D("25000")),
    (S5, A2, "2024-25"): (1, D("35000")),
    (S7, A2, "2024-25"): (1, D("120000")),  # C5
    (S7, A1, "2024-25"): (1, D("15000")),   # C8
    (S2, A1, "2025-26"): (1, D("75000")),
    (S1, A2, "2025-26"): (2, D("33400")),
    (S2, A2, "2025-26"): (2, D("33400")),
    (S3, A2, "2025-26"): (2, D("33400")),
    (S6, A2, "2025-26"): (1, D("15000")),
    (S7, A2, "2025-26"): (1, D("20000")),
}

# Spend by FY over the supplier tables (contract_supplier, supplier_fy, supplier_agency_fy)
FY_TOTALS = {"2024-25": D("1050000"), "2025-26": D("210200")}
# The agency tables also count contracts with no supplier (C19, 5,000 in FY2025-26)
AGENCY_FY_TOTALS = {"2024-25": D("1050000"), "2025-26": D("215200")}

# mart.contract_supplier: every supplier row of the non-cancelled contracts that have a value:
# 19 for C1-C16 (C3 has two, C4 three, C15 two), 3 each for C17 and C18
CONTRACT_SUPPLIER_ROWS = 25

# Whole-database counts
MART_ROWS = {
    "contract": 20,
    "contract_supplier": 25,
    "supplier": 7,
    "agency": 2,
    "supplier_fy": 11,
    "agency_fy": 4,
    "supplier_agency_fy": 15,
}

# Top 10 suppliers by value in FY2024-25 and their split across agencies (the acceptance question)
TOP_SUPPLIERS_FY2425 = [S1, S3, S2, S7, S5, S4]
