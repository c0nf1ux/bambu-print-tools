"""Check the real license on a sourced 3D-model page before printing/
redistributing it. Ported from versus-chess's scripts/check-license.sh
(2026-09-07) into Python so it lives alongside the rest of this toolkit
instead of as a one-off bash script duplicated per project.

Confirmed working (by direct testing, not assumed) for MakerWorld and
MyMiniFactory — both server-render real license data into the initial
HTML, fetchable with a plain browser-UA request, no auth needed. Also
Thingiverse, via their official API (needs a free App Token, see below).
Does NOT work for Printables/Cults3D — genuine Cloudflare JS challenge,
no known workaround short of a real logged-in browser session.

Real finding from using this (versus-chess, 2026-09-07): many "free"
downloads on MakerWorld/CGTrader/Cults3D carry no redistribution rights at
all. "Free" and "repo-eligible" are different questions.

Thingiverse setup (one-time, free): create/log into a Thingiverse account,
visit thingiverse.com/apps/create, platform "Desktop App", copy the "App
Token" field (not Client ID/Secret), export it as THINGIVERSE_TOKEN. Never
commit this token.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


@dataclass
class LicenseResult:
    platform: str
    status: str  # "ok" | "blocked" | "dead" | "unknown" | "unsupported"
    license: str | None = None
    note: str | None = None


def _platform_of(url: str) -> str | None:
    for host, name in (
        ("makerworld.com", "makerworld"),
        ("myminifactory.com", "myminifactory"),
        ("thingiverse.com", "thingiverse"),
    ):
        if host in url:
            return name
    return None


def check_license(url: str, thingiverse_token: str | None = None) -> LicenseResult:
    platform = _platform_of(url)
    if platform is None:
        return LicenseResult(
            "unknown", "unsupported",
            note="Printables and Cults3D block automated access entirely (Cloudflare JS "
                 "challenge) — check manually in a browser. Other platforms untested.",
        )

    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    if resp.status_code == 403 and "Just a moment" in resp.text:
        return LicenseResult(platform, "blocked", note=f"Cloudflare JS challenge at {url}")
    if resp.status_code == 404:
        return LicenseResult(platform, "dead", note=f"404 at {url}")

    if platform == "makerworld":
        return _check_makerworld(url, resp.text)
    if platform == "myminifactory":
        return _check_myminifactory(resp.text)
    return _check_thingiverse(url, thingiverse_token or os.environ.get("THINGIVERSE_TOKEN"))


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
    note = None
    if "Standard Digital File License" in license_str or "Exclusive" in license_str:
        note = "NOT repo-eligible (personal print only, no redistribution/remix)"
    return LicenseResult("makerworld", "ok", license=license_str, note=note)


def _check_myminifactory(html: str) -> LicenseResult:
    m = re.search(r"creativecommons\.org/licenses/([a-z-]+)", html)
    if not m:
        return LicenseResult("myminifactory", "unknown", note="no Creative Commons license link found (may be paid/restricted)")
    variant = m.group(1)
    note = None
    if "nd" in variant:
        note = "NOT usable at all — No-Derivatives forbids resizing, even for personal use"
    elif "nc" in variant or "by" in variant:
        note = "Likely repo-eligible (verify no other restriction on the page)"
    return LicenseResult("myminifactory", "ok", license=f"CC {variant}", note=note)


def _check_thingiverse(url: str, token: str | None) -> LicenseResult:
    m = re.search(r"thing:(\d+)", url)
    if not m:
        return LicenseResult("thingiverse", "unknown", note=f"could not parse thing id from {url}")
    thing_id = m.group(1)
    if not token:
        return LicenseResult(
            "thingiverse", "unknown",
            note="NEEDS TOKEN: set THINGIVERSE_TOKEN (see module docstring for setup)",
        )
    resp = requests.get(f"https://api.thingiverse.com/things/{thing_id}", params={"access_token": token}, timeout=15)
    license_str = resp.json().get("license")
    if not license_str:
        return LicenseResult("thingiverse", "unknown", note=f"no license field returned for thing:{thing_id}")
    note = (
        "NOT usable at all — No-Derivatives forbids resizing, even for personal use"
        if "No Derivatives" in license_str
        else "Likely repo-eligible (verify no other restriction on the page)"
    )
    return LicenseResult("thingiverse", "ok", license=license_str, note=note)
