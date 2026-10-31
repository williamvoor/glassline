import pytest

from austender.abn import clean_abn, name_norm, valid_abn


def test_known_valid_abn():
    assert valid_abn("51 824 753 556") == "51824753556"
    assert valid_abn("51824753556") == "51824753556"


@pytest.mark.parametrize("raw", ["51824753557", "12345678901", "5182475355", "abc", "", None, "518247535560"])
def test_invalid_abn(raw):
    assert valid_abn(raw) is None


def test_clean_abn_requires_eleven_digits():
    assert clean_abn(" 12 345 678 901 ") == "12345678901"
    assert clean_abn("1234") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme Widgets Pty Ltd", "acme widgets"),
        ("ACME WIDGETS PTY. LTD.", "acme widgets"),
        ("Cobalt & Sons Pty Ltd", "cobalt and sons"),
        ("Cobalt and Sons P/L", "cobalt and sons"),
        ("No Abn Trading P/L", "no abn trading"),
        ("Café Société Limited", "cafe societe"),
        ("O'Brien Inc", "obrien"),
        ("Smith-Jones Pty Limited", "smith jones"),
        ("Xyz Pty Ltd as trustee for the Xyz Family Trust", "xyz"),
        ("Xyz Pty Ltd ATF Xyz Trust", "xyz"),
        ("The Trustee for the Foo Trust", "the trustee for the foo trust"),
        ("Public Trustee of Queensland", "public trustee of queensland"),
        ("Public Trustee of NSW", "public trustee of nsw"),
        ("ATF Services Pty Ltd", "atf services"),
        ("Ltd", "ltd"),
        ("  Multiple   Spaces  Pty  Ltd ", "multiple spaces"),
        ("Globex GmbH", "globex gmbh"),
        (None, ""),
    ],
)
def test_name_norm(raw, expected):
    assert name_norm(raw) == expected
