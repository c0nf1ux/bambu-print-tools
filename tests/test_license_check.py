import json
import os
from pathlib import Path

import pytest

from bambu_print_tools.license_check import (
    _check_myminifactory,
    _parse_thingiverse_page,
    _platform_of,
    classify_license,
    parse_printables_response,
)

FIXTURES = Path(__file__).parent / "fixtures" / "licenses"


def test_platform_of_matches_exact_host():
    assert _platform_of("https://makerworld.com/en/models/123") == "makerworld"


def test_platform_of_matches_subdomain():
    assert _platform_of("https://www.myminifactory.com/object/foo") == "myminifactory"


def test_platform_of_rejects_lookalike_host():
    """A naive `host in url` substring check would wrongly match this —
    the real host here is notmakerworld.com.example.net, not makerworld.com."""
    assert _platform_of("https://notmakerworld.com.example.net/en/models/123") is None


def test_platform_of_rejects_lookalike_host_as_path():
    """Same class of bug via the path instead of a subdomain trick."""
    assert _platform_of("https://example.net/makerworld.com/models/123") is None


def test_platform_of_rejects_non_http_scheme():
    assert _platform_of("file:///etc/passwd#makerworld.com") is None


def test_platform_of_returns_none_for_unsupported_host():
    assert _platform_of("https://cults3d.com/en/3d-model/game/foo") is None


def test_platform_of_matches_printables():
    assert _platform_of("https://www.printables.com/model/375009-the-jersey-devil") == "printables"


def test_platform_of_rejects_printables_lookalike():
    assert _platform_of("https://printables.com.example.net/model/1") is None


# classify_license: (commercial_ok, derivatives_ok, share_alike), or None if unrecognized.
# Every string below is a real value copied from a live Printables, Thingiverse,
# MyMiniFactory or MakerWorld page/API (see tests/fixtures/licenses).

@pytest.mark.parametrize("text,expected", [
    # Printables abbreviations
    ("CC0", (True, True, False)),
    ("CC-BY", (True, True, False)),
    ("CC-BY-SA", (True, True, True)),
    ("CC-BY-ND", (True, False, False)),
    ("CC-BY-NC", (False, True, False)),
    ("CC-BY-NC-SA", (False, True, True)),
    ("CC-BY-NC-ND", (False, False, False)),
    ("Standard Digital File", (False, False, False)),
    # Thingiverse API strings
    ("Creative Commons - Attribution", (True, True, False)),
    ("Creative Commons - Attribution - Non-Commercial", (False, True, False)),
    ("Creative Commons - Attribution - Share Alike", (True, True, True)),
    ("Creative Commons - Attribution - No Derivatives", (True, False, False)),
    ("Creative Commons - Attribution - Non-Commercial - No Derivatives", (False, False, False)),
    ("Creative Commons - Public Domain Dedication", (True, True, False)),
    # MyMiniFactory / Creative Commons URL variants
    ("CC by-nc-sa", (False, True, True)),
    ("CC by", (True, True, False)),
    ("CC by-nd", (True, False, False)),
    # MakerWorld
    ("Standard Digital File License", (False, False, False)),
])
def test_classify_license_known(text, expected):
    assert classify_license(text) == expected


@pytest.mark.parametrize("text", ["", "GPL 3.0", "BSD", "Apache-2.0", "Open Community License v1.1", "whatever"])
def test_classify_license_unrecognized_is_none(text):
    """Software/community licenses aren't classified: say unknown, never guess."""
    assert classify_license(text) is None


def test_nc_is_never_reported_as_commercial_ok():
    """Regression: the original check treated every CC variant containing 'by'
    (including by-nc-sa) as 'Likely repo-eligible'."""
    commercial, _, _ = classify_license("CC by-nc-sa")
    assert commercial is False


def test_printables_cc_by_real_response():
    data = json.loads((FIXTURES / "printables_jersey_cc_by.json").read_text())
    res = parse_printables_response(data)
    assert res.platform == "printables"
    assert res.status == "ok"
    assert res.license == "CC-BY"
    assert res.commercial_ok is True
    assert res.derivatives_ok is True


def test_printables_cc_by_nc_real_response():
    data = json.loads((FIXTURES / "printables_wendigo_cc_by_nc.json").read_text())
    res = parse_printables_response(data)
    assert res.license == "CC-BY-NC"
    assert res.commercial_ok is False
    assert "NOT commercial" in res.note


def test_printables_standard_license_real_response():
    data = json.loads((FIXTURES / "printables_franky_standard.json").read_text())
    res = parse_printables_response(data)
    assert res.commercial_ok is False


def test_printables_missing_model_is_dead():
    res = parse_printables_response({"data": {"print": None}})
    assert res.status == "dead"


def test_thingiverse_page_cc_by_sa_real_page():
    html = (FIXTURES / "thingiverse_wendigo_cc_by_sa.jsonld.html").read_text(encoding="utf-8")
    res = _parse_thingiverse_page(html)
    assert res.platform == "thingiverse"
    assert res.status == "ok"
    assert res.commercial_ok is True
    assert res.share_alike is True
    assert "same license" in res.note


def test_thingiverse_page_cc_by_nc_real_page():
    html = (FIXTURES / "thingiverse_wendigo_cc_by_nc.jsonld.html").read_text(encoding="utf-8")
    res = _parse_thingiverse_page(html)
    assert res.commercial_ok is False
    assert "NOT commercial" in res.note


def test_thingiverse_page_without_license_is_unknown():
    res = _parse_thingiverse_page("<html><body>nothing here</body></html>")
    assert res.status == "unknown"
    assert res.commercial_ok is None


def test_myminifactory_nc_not_reported_as_eligible():
    """Real anchor markup from a CC BY-NC-SA MyMiniFactory model page."""
    html = '<a href="https://creativecommons.org/licenses/by-nc-sa/4.0/" title="Creative Commons License" >'
    res = _check_myminifactory(html)
    assert res.commercial_ok is False
    assert "repo-eligible" not in (res.note or "")
    assert "NOT commercial" in res.note


def test_myminifactory_cc_by_is_commercial_ok():
    html = '<a href="https://creativecommons.org/licenses/by/4.0/" title="Creative Commons License" >'
    res = _check_myminifactory(html)
    assert res.commercial_ok is True


def test_myminifactory_nd_not_usable():
    html = '<a href="https://creativecommons.org/licenses/by-nc-nd/4.0/">'
    res = _check_myminifactory(html)
    assert res.derivatives_ok is False
    assert "NOT usable" in res.note


# Live checks against the real sites. Opt-in (RUN_NETWORK_TESTS=1) so CI is not
# flaky on third-party availability; every URL here was verified by hand on
# 2026-11-20.
network = pytest.mark.skipif(not os.environ.get("RUN_NETWORK_TESTS"), reason="set RUN_NETWORK_TESTS=1")


@network
def test_live_printables_jersey_devil():
    from bambu_print_tools.license_check import check_license
    res = check_license("https://www.printables.com/model/375009-the-jersey-devil")
    assert (res.status, res.license, res.commercial_ok) == ("ok", "CC-BY", True)


@network
def test_live_thingiverse_wendigo_without_token():
    from bambu_print_tools.license_check import check_license
    res = check_license("https://www.thingiverse.com/thing:6755519", thingiverse_token="")
    assert res.status == "ok" and res.commercial_ok is True and res.share_alike is True
