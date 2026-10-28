from bambu_print_tools.license_check import _platform_of


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
    assert _platform_of("https://printables.com/model/123") is None
