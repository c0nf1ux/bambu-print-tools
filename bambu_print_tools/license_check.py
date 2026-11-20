"""Check the real license on a sourced 3D-model page before printing or
redistributing it. Ported from versus-chess's scripts/check-license.sh
(2026-09-07) into Python so it lives alongside the rest of this toolkit
instead of as a one-off bash script duplicated per project.

Platform support (each confirmed by direct testing, not assumed):

- Printables: public GraphQL API (api.printables.com/graphql/). The model
  pages sit behind a Cloudflare JS challenge, but the API the site itself
  uses does not. No token needed.
- Thingiverse: the model page embeds schema.org JSON-LD with a license URL,
  so no token is needed. If you do pass a free App Token (see below) the
  official API is used instead.
- MyMiniFactory: server-rendered creativecommons.org link in the page HTML.
- MakerWorld: was scriptable on 2026-09-07; returned a Cloudflare challenge
  on 2026-10-28, so expect "blocked".
- Cults3D: Cloudflare JS challenge, unsupported.

Every result carries commercial_ok / derivatives_ok / share_alike. Those are
the questions that matter for a product: "free to download" does not mean
"free to sell", and a Non-Commercial (NC) license rules a model out of
anything you might charge for. None means "could not classify, read the
page yourself". It never means yes.

Thingiverse API setup (optional, free): thingiverse.com/apps/create,
platform "Desktop App", copy the "App Token" field, export it as
THINGIVERSE_TOKEN. Never commit this token.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

PRINTABLES_GRAPHQL = "https://api.printables.com/graphql/"


@dataclass
class LicenseResult:
    platform: str
    status: str  # "ok" | "blocked" | "dead" | "unknown" | "unsupported"
    license: str | None = None
    note: str | None = None
    commercial_ok: bool | None = None   # None = could not classify
    derivatives_ok: bool | None = None  # False = No-Derivatives (no resizing)
    share_alike: bool = False


def _platform_of(url: str) -> str | None:
    """Exact-host or proper-subdomain match only - a naive `host in url`
    substring check would also match an attacker-shaped URL like
    "https://notmakerworld.com.example.net/..." and then actually fetch
    that host while treating it as MakerWorld."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    hostname = (parts.hostname or "").lower()
    for host, name in (
        ("makerworld.com", "makerworld"),
        ("myminifactory.com", "myminifactory"),
        ("thingiverse.com", "thingiverse"),
        ("printables.com", "printables"),
    ):
        if hostname == host or hostname.endswith("." + host):
            return name
    return None


def classify_license(text: str) -> tuple[bool, bool, bool] | None:
    """Map a license string to (commercial_ok, derivatives_ok, share_alike).

    Accepts every spelling seen in the wild: Printables abbreviations
    ("CC-BY-NC-SA"), Thingiverse names ("Creative Commons - Attribution -
    Non-Commercial"), CC URL variants ("by-nc-sa") and the platform
    proprietary licenses. Returns None for anything it does not recognise
    (GPL, BSD, community licenses, empty): unknown is reported as unknown,
    never guessed.
    """
    s = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    if not s:
        return None
    if "standard digital file" in s or "exclusive" in s:
        return (False, False, False)
    if re.search(r"\bcc0\b|public domain|publicdomain|zero", s):
        return (True, True, False)
    if not re.search(r"\bby\b|attribution|creative commons", s):
        return None
    nc = bool(re.search(r"\bnc\b|non ?commercial", s))
    nd = bool(re.search(r"\bnd\b|no ?deriv", s))
    sa = bool(re.search(r"\bsa\b|share ?alike", s))
    return (not nc, not nd, sa)


def _describe(license_str: str, flags: tuple[bool, bool, bool] | None) -> tuple[str | None, bool | None, bool | None, bool]:
    """Turn classification flags into (note, commercial_ok, derivatives_ok, share_alike)."""
    if flags is None:
        return ("Unrecognised license: read the model page before relying on it", None, None, False)
    commercial, derivs, sa = flags
    if not derivs:
        note = ("NOT usable: license forbids modifying or reselling "
                "(No-Derivatives or personal-use-only), so no resizing or re-skinning")
    elif not commercial:
        note = "NOT commercial-safe (Non-Commercial): personal printing only, cannot be used in a product"
    elif sa:
        note = ("Commercial use OK with attribution. Share-alike: any modified or rescaled "
                "version you release must use the same license")
    else:
        note = "Commercial use OK (credit the creator unless the license is CC0)"
    return (note, commercial, derivs, sa)


def _result(platform: str, license_str: str) -> LicenseResult:
    note, commercial, derivs, sa = _describe(license_str, classify_license(license_str))
    return LicenseResult(platform, "ok", license=license_str, note=note,
                         commercial_ok=commercial, derivatives_ok=derivs, share_alike=sa)


def check_license(url: str, thingiverse_token: str | None = None) -> LicenseResult:
    platform = _platform_of(url)
    if platform is None:
        return LicenseResult(
            "unknown", "unsupported",
            note="Cults3D blocks automated access entirely (Cloudflare JS challenge) - "
                 "check manually in a browser. Other platforms untested.",
        )

    if platform == "printables":
        # The model page is Cloudflare-blocked; the public GraphQL API is not.
        return _check_printables(url)

    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    if resp.status_code == 403 and "Just a moment" in resp.text:
        return LicenseResult(platform, "blocked", note=f"Cloudflare JS challenge at {url}")
    if resp.status_code == 404:
        return LicenseResult(platform, "dead", note=f"404 at {url}")

    if platform == "makerworld":
        return _check_makerworld(url, resp.text)
    if platform == "myminifactory":
        return _check_myminifactory(resp.text)
    token = thingiverse_token if thingiverse_token is not None else os.environ.get("THINGIVERSE_TOKEN")
    if token:
        return _check_thingiverse_api(url, token)
    return _parse_thingiverse_page(resp.text)


def _check_printables(url: str) -> LicenseResult:
    m = re.search(r"/model/(\d+)", urlsplit(url).path)
    if not m:
        return LicenseResult("printables", "unknown", note=f"could not parse model id from {url}")
    # m.group(1) is digits only (\d+), so interpolating it cannot inject GraphQL.
    query = (
        f'{{ print(id: "{m.group(1)}") '
        "{ id name license { id name abbreviation disallowRemixing } } }"
    )
    resp = requests.post(
        PRINTABLES_GRAPHQL, json={"query": query},
        headers={"User-Agent": USER_AGENT}, timeout=15,
    )
    if resp.status_code != 200:
        return LicenseResult("printables", "unknown", note=f"Printables API returned HTTP {resp.status_code}")
    return parse_printables_response(resp.json())


def parse_printables_response(data: dict) -> LicenseResult:
    """Parse the JSON body returned by the Printables GraphQL `print` query."""
    model = (data.get("data") or {}).get("print")
    if not model:
        return LicenseResult("printables", "dead", note="Printables API returned no model for this id")
    lic = model.get("license") or {}
    abbreviation = lic.get("abbreviation")
    if not abbreviation:
        return LicenseResult("printables", "unknown", note="no license field on this model")
    result = _result("printables", abbreviation)
    if lic.get("disallowRemixing") and result.derivatives_ok is None:
        result.derivatives_ok = False
    return result


def _check_makerworld(url: str, html: str) -> LicenseResult:
    m = re.search(r"/models/(\d+)", url)
    if not m:
        return LicenseResult("makerworld", "unknown", note=f"could not parse model id from {url}")
    model_id = m.group(1)
    design_start = html.find(f'"design":{{"id":{model_id}')
    if design_start == -1:
        return LicenseResult("makerworld", "unknown", note=f"no design block found for model id {model_id}")
    license_m = re.search(r'"license":"[^"]*"', html[design_start:design_start + 80_000])
    if not license_m:
        return LicenseResult("makerworld", "unknown", note=f"no license field found for model id {model_id}")
    license_str = license_m.group(0).split(":", 1)[1].strip('"')
    return _result("makerworld", license_str)


def _check_myminifactory(html: str) -> LicenseResult:
    m = re.search(r"creativecommons\.org/licenses/([a-z-]+)", html)
    if not m:
        return LicenseResult("myminifactory", "unknown", note="no Creative Commons license link found (may be paid/restricted)")
    return _result("myminifactory", f"CC {m.group(1)}")


def _find_key(obj, key: str):
    """First value stored under `key` anywhere in nested dicts/lists, else None."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = _find_key(child, key)
        if found is not None:
            return found
    return None


def _parse_thingiverse_page(html: str) -> LicenseResult:
    """Read the license URL out of the page's schema.org JSON-LD block."""
    for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            doc = json.loads(block)
        except ValueError:
            continue
        # Thingiverse nests it at mainEntityOfPage.license; search the whole
        # structure so a layout shuffle does not silently break this.
        license_url = _find_key(doc, "license")
        if not isinstance(license_url, str):
            continue
        m = re.search(r"creativecommons\.org/(?:licenses/([a-z-]+)|publicdomain/zero)", license_url)
        if not m:
            return LicenseResult("thingiverse", "unknown", license=license_url,
                                 note="license URL is not a Creative Commons URL: read the page")
        return _result("thingiverse", f"CC {m.group(1)}" if m.group(1) else "CC0")
    return LicenseResult("thingiverse", "unknown", note="no license found in the page's JSON-LD (page layout may have changed)")


def _check_thingiverse_api(url: str, token: str) -> LicenseResult:
    m = re.search(r"thing:(\d+)", url)
    if not m:
        return LicenseResult("thingiverse", "unknown", note=f"could not parse thing id from {url}")
    thing_id = m.group(1)
    # Token goes in the Authorization header, not a query param - a query
    # param is more likely to end up in a server access log, a proxy log,
    # or an exception message that includes the request URL.
    resp = requests.get(
        f"https://api.thingiverse.com/things/{thing_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    license_str = resp.json().get("license")
    if not license_str:
        return LicenseResult("thingiverse", "unknown", note=f"no license field returned for thing:{thing_id}")
    return _result("thingiverse", license_str)
