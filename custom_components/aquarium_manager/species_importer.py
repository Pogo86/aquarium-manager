#!/usr/bin/env python3
"""
Aquarium Manager fish-library importer.

Give this script a COMMON NAME such as:

    python fish_import.py "Neon Tetra"

It searches SeriouslyFish and FishBase independently, resolves the best matching
taxon, merges whichever source data is actually available, and adds/updates the
species in fish_library.json.

SeriouslyFish is preferred for aquarium-care values when both sites describe the
same taxon. FishBase fills missing values. The output stores one canonical value
per field plus field-level provenance and source-page references; it does not
duplicate both websites' full datasets.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup


SF_BASE = "https://www.seriouslyfish.com"
FB_BASE = "https://www.fishbase.se"

TIMEOUT_SECONDS = 25
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
REQUEST_DELAY_SECONDS = 0.35

USER_AGENT = (
    "AquariumManagerFishImporter/2.4.2 "
    "(single-species personal aquarium-library importer)"
)


class ImporterError(RuntimeError):
    """Friendly error shown without a traceback."""


@dataclass
class Candidate:
    """Potential species match."""

    scientific_name: str
    fishbase_url: str
    seriouslyfish_url: str
    fishbase_common_name: str = ""
    seriouslyfish_common_name: str = ""
    score: float = 0.0
    fishbase_html: str | None = None
    seriouslyfish_html: str | None = None


def norm_space(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def norm_name(value: str) -> str:
    """Normalize a common name for matching."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return norm_space(value)


def slugify_id(value: str) -> str:
    value = norm_name(value).replace(" ", "_")
    return value or "species"


def scientific_slug(scientific_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", scientific_name.casefold()).strip("-")


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = re.sub(r"/+$", "", parts.path) or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


class Fetcher:
    """Polite single-species HTTP fetcher."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-GB,en;q=0.9",
            }
        )
        self._last_request = 0.0

    def get(self, url: str, *, allow_404: bool = False) -> str | None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < REQUEST_DELAY_SECONDS:
            time.sleep(REQUEST_DELAY_SECONDS - elapsed)

        try:
            response = self.session.get(url, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise ImporterError(f"Could not fetch {url}: {exc}") from exc
        finally:
            self._last_request = time.monotonic()

        if allow_404 and response.status_code == 404:
            return None

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise ImporterError(
                f"{url} returned HTTP {response.status_code}."
            ) from exc

        raw = response.content
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ImporterError(f"Refusing to parse unexpectedly large page: {url}")

        content_type = response.headers.get("Content-Type", "").casefold()
        if "html" not in content_type:
            raise ImporterError(f"{url} did not return an HTML page.")

        return response.text


def soup_text(html: str) -> tuple[BeautifulSoup, str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    main = soup.find("main") or soup
    return soup, norm_space(main.get_text(" ", strip=True))


def parse_range(
    text: str,
    label_pattern: str,
    unit_pattern: str = "",
) -> dict[str, float] | None:
    dash = r"(?:\s*[–—-]\s*|\s+to\s+)"
    match = re.search(
        rf"{label_pattern}\s*[:;]?\s*"
        rf"(-?\d+(?:\.\d+)?)"
        rf"{dash}"
        rf"(-?\d+(?:\.\d+)?)"
        rf"\s*{unit_pattern}",
        text,
        re.I,
    )
    if not match:
        return None

    low, high = float(match.group(1)), float(match.group(2))
    if low > high:
        low, high = high, low
    return {"min": low, "max": high}



RANGE_DASH = r"(?:\s*[-–—−‐‑]\s*|\s+to\s+)"

def _range_from_patterns(text: str, patterns: tuple[str, ...]) -> dict[str, float] | None:
    """Try several range patterns and return a normalised min/max pair."""
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if not match:
            continue
        try:
            low = float(match.group(1))
            high = float(match.group(2))
        except (ValueError, IndexError):
            continue
        if low > high:
            low, high = high, low
        return {"min": low, "max": high}
    return None


def parse_temperature_any(text: str) -> dict[str, float] | None:
    """
    Parse Celsius ranges from both sites.

    Handles, for example:
      Temp 21–25°C
      Temperature 21 - 25 °C
      Tropical; 20°C - 26°C
      20 C to 26 C
    """
    d = RANGE_DASH
    return _range_from_patterns(
        text,
        (
            rf"(?:\bTemp(?:erature)?(?:\s+range)?\b\s*[:;]?\s*)"
            rf"(-?\d+(?:\.\d+)?)\s*(?:°\s*)?C?\s*{d}"
            rf"(-?\d+(?:\.\d+)?)\s*(?:°\s*)?C\b",
            rf"(?:\b(?:Tropical|Subtropical|Temperate)\b\s*;\s*)"
            rf"(-?\d+(?:\.\d+)?)\s*(?:°\s*)?C\s*{d}"
            rf"(-?\d+(?:\.\d+)?)\s*(?:°\s*)?C\b",
            rf"(-?\d+(?:\.\d+)?)\s*(?:°\s*)?C\s*{d}"
            rf"(-?\d+(?:\.\d+)?)\s*(?:°\s*)?C\b",
            # SeriouslyFish often prints the C only once after the upper bound.
            rf"(-?\d+(?:\.\d+)?)\s*{d}"
            rf"(-?\d+(?:\.\d+)?)\s*(?:°\s*)?C\b",
        ),
    )


def parse_ph_any(text: str) -> dict[str, float] | None:
    d = RANGE_DASH
    return _range_from_patterns(
        text,
        (
            rf"\bpH(?:\s+range)?\b\s*[:;]?\s*"
            rf"(\d+(?:\.\d+)?)\s*{d}(\d+(?:\.\d+)?)",
        ),
    )


def parse_hardness_any(text: str) -> dict[str, float] | None:
    """
    Parse the general-hardness field.

    SeriouslyFish labels it Hardness and normally reports dGH.
    FishBase labels it dH range. Aquarium Manager stores this as GH/reference
    general hardness; it is deliberately NOT treated as KH.
    """
    d = RANGE_DASH
    return _range_from_patterns(
        text,
        (
            rf"\bHardness\b\s*[:;]?\s*"
            rf"(\d+(?:\.\d+)?)\s*{d}(\d+(?:\.\d+)?)\s*(?:d?\s*GH|dH)?",
            rf"\bdH\s+range\b\s*[:;]?\s*"
            rf"(\d+(?:\.\d+)?)\s*{d}(\d+(?:\.\d+)?)",
            rf"\bGH(?:\s+range)?\b\s*[:;]?\s*"
            rf"(\d+(?:\.\d+)?)\s*{d}(\d+(?:\.\d+)?)",
        ),
    )


def heading_section(soup: BeautifulSoup, heading_prefix: str) -> str:
    """
    Extract the text belonging to an HTML heading.

    This is more robust than relying on the flattened page's exact heading
    wording (FishBase headings include extra navigation text).
    """
    prefix = norm_name(heading_prefix)
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        heading_text = norm_name(heading.get_text(" ", strip=True))
        if not heading_text.startswith(prefix):
            continue

        chunks: list[str] = []
        for node in heading.next_siblings:
            if getattr(node, "name", None) and re.fullmatch(r"h[1-6]", node.name, re.I):
                break
            if hasattr(node, "get_text"):
                value = norm_space(node.get_text(" ", strip=True))
            else:
                value = norm_space(str(node))
            if value:
                chunks.append(value)
        result = norm_space(" ".join(chunks))
        if result:
            return result
    return ""


def first_nonempty_text(*values: str) -> str:
    for value in values:
        if value and value.strip():
            return norm_space(value)
    return ""


def value_present(value: Any) -> bool:
    return value not in (None, "", [], {})


def fmt_range(value: Any, unit: str = "") -> str:
    if not isinstance(value, dict):
        return "MISSING"
    suffix = f" {unit}" if unit else ""
    return f"{value.get('min')}–{value.get('max')}{suffix}"


def print_source_coverage(
    sf: dict[str, Any] | None,
    fb: dict[str, Any] | None,
) -> None:
    """Show exactly what each source parser found before merging."""
    print("\nSource coverage:")
    rows = (
        ("Adult size", "adult_size_cm", "cm"),
        ("Temperature", "temperature", "°C"),
        ("pH", "ph", ""),
        ("GH / dH", "gh", "dGH"),
        ("Minimum group", "minimum_group", ""),
        ("Minimum tank", "minimum_tank", ""),
        ("Social type", "social_type", ""),
        ("Temperament", "temperament", ""),
    )

    for source_name, data in (("SeriouslyFish", sf), ("FishBase", fb)):
        print(f"\n  {source_name}")
        if not data:
            print("    Source page unavailable or could not be parsed.")
            continue

        for label, key, unit in rows:
            value = data.get(key)
            if key in {"temperature", "ph", "gh"}:
                display = fmt_range(value, unit)
            elif key == "minimum_tank" and isinstance(value, dict):
                parts = []
                if value.get("length_cm") is not None:
                    parts.append(f"{value['length_cm']} cm long")
                if value.get("width_cm") is not None:
                    parts.append(f"{value['width_cm']} cm wide")
                if value.get("height_cm") is not None:
                    parts.append(f"{value['height_cm']} cm high")
                display = " × ".join(parts) if parts else "MISSING"
            elif value_present(value):
                display = f"{value}{(' ' + unit) if unit and key == 'adult_size_cm' else ''}"
            else:
                display = "MISSING"

            mark = "✓" if display != "MISSING" else "✗"
            print(f"    {mark} {label:<16} {display}")

        if source_name == "FishBase":
            for extra in ("environment_type", "ecology", "climate", "distribution"):
                value = data.get(extra)
                mark = "✓" if value_present(value) else "✗"
                display = value if value_present(value) else "MISSING"
                print(f"    {mark} {extra.replace('_', ' ').title():<16} {display}")

    # Make disagreements explicit rather than hiding them.
    if sf and fb:
        comparisons = (
            ("temperature", "Temperature"),
            ("ph", "pH"),
            ("gh", "GH/dH"),
            ("adult_size_cm", "Adult size"),
            ("minimum_group", "Minimum group"),
        )
        differences = []
        for key, label in comparisons:
            a, b = sf.get(key), fb.get(key)
            if value_present(a) and value_present(b) and a != b:
                differences.append(label)
        if differences:
            print(
                "\n  Note: the sources disagree on "
                + ", ".join(differences)
                + ". SeriouslyFish remains the primary aquarium-care value; "
                  "FishBase is retained in reference_data."
            )



def extract_section(text: str, heading: str, next_headings: tuple[str, ...]) -> str:
    alternatives = "|".join(re.escape(h) for h in next_headings if h != heading)
    match = re.search(
        rf"\b{re.escape(heading)}\b\s+(.*?)(?=\s+(?:{alternatives})\b|$)",
        text,
        re.I | re.S,
    )
    return norm_space(match.group(1)) if match else ""


def first_group_number(text: str) -> int | None:
    patterns = (
        r"(?:group|shoal|school)\s+(?:of\s+)?at\s+least\s+(\d+)",
        r"at\s+least\s+(\d+)(?:\s*[–—-]\s*\d+)?\s+(?:specimens|individuals|fish)",
        r"(?:groups?|shoals?|schools?)\s+of\s+(\d+)\s+or\s+more",
        r"(?:groups?|shoals?|schools?)\s+of\s+(\d+)(?:\s*[–—-]\s*\d+)?",
        r"in\s+groups\s+of\s+(\d+)\s+or\s+more",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = int(match.group(1))
            if 1 <= value <= 999:
                return value
    return None


# ---------------------------------------------------------------------------
# FishBase resolution + parsing
# ---------------------------------------------------------------------------

BINOMIAL_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+\([A-Z][a-z]+\))?\s+[a-z][a-z0-9._-]+)\b"
)


def fishbase_search_url(common_name: str, exact: bool = True) -> str:
    operator = "equal" if exact else "CONTAINS"
    return (
        f"{FB_BASE}/ComNames/CommonNameSearchList.php?"
        f"CommonName={quote_plus(common_name)}&crit1_operator={operator}"
    )


FISHBASE_PLACEHOLDER_NAMES = {
    "please select",
    "select species",
    "select genus",
    "scientific name",
    "common name",
    "not available",
    "no results",
}


def valid_fishbase_scientific_name(value: str) -> bool:
    """Return True only for plausible FishBase binomial names."""
    value = norm_space(value)
    if not value:
        return False
    if value.casefold() in FISHBASE_PLACEHOLDER_NAMES:
        return False
    return bool(re.fullmatch(r"[A-Z][a-z]+ [a-z][a-z0-9._-]+", value))


def _fishbase_name_from_href(href: str) -> str | None:
    """Extract a scientific name from common FishBase result-link formats."""
    href = str(href or "")
    if not href:
        return None

    parts = urlsplit(urljoin(FB_BASE, href))
    path = parts.path

    # /summary/Paracheirodon-innesi or /Summary/Paracheirodon-innesi.html
    match = re.search(
        r"/summary/([A-Z][a-z]+)-([a-z][a-z0-9._-]+)(?:\.html)?/?$",
        path,
        re.I,
    )
    if match:
        candidate = f"{match.group(1).capitalize()} {match.group(2).lower()}"
        return candidate if valid_fishbase_scientific_name(candidate) else None

    # Country/species and other FishBase links often expose genus/species as query params.
    qs = parse_qs(parts.query)
    genus = (qs.get("genusname") or qs.get("GenusName") or [""])[0]
    species = (qs.get("speciesname") or qs.get("SpeciesName") or [""])[0]
    candidate = norm_space(f"{genus} {species}")
    if valid_fishbase_scientific_name(candidate):
        return candidate

    return None


def fishbase_candidates_from_search(html: str) -> list[str]:
    """
    Extract valid scientific names from a FishBase common-name result page.

    Handles visible binomials, form values, summary links, and links carrying
    genusname/speciesname query parameters. Placeholder values such as
    "Please select" are ignored.
    """
    soup, text = soup_text(html)
    names: list[str] = []

    for node in soup.find_all(["a", "input", "option"]):
        if node.name == "a":
            # For links, trust the FishBase URL/query parameters, not visible
            # link text. A label such as "Neon tetra" is a common name but is
            # syntactically indistinguishable from a binomial to a simple regex.
            linked = _fishbase_name_from_href(str(node.get("href") or ""))
            if linked:
                names.append(linked)
            continue

        if node.name == "input":
            value = str(node.get("value") or "")
        else:  # option
            value = str(node.get("value") or "") or node.get_text(" ", strip=True)

        value = norm_space(value)
        value = re.sub(r"\s+\([A-Z][a-z]+\)\s+", " ", value)
        if valid_fishbase_scientific_name(value):
            names.append(value)

    # Fallback only when FishBase explicitly labels a Scientific Name section.
    # Do not scan the entire page: common names such as "Neon tetra" can look
    # like binomials to a syntax-only regular expression.
    match = re.search(
        r"Scientific Name\s+(.*?)(?=\s+Family\b|\s+Type\b|$)",
        text,
        re.I | re.S,
    )
    if match:
        segment = match.group(1)
        for candidate in re.findall(
            r"\b[A-Z][a-z]+\s+[a-z][a-z0-9._-]+\b",
            segment,
        ):
            candidate = norm_space(candidate)
            if valid_fishbase_scientific_name(candidate):
                names.append(candidate)

    cleaned: list[str] = []
    for name in names:
        if name not in cleaned:
            cleaned.append(name)
    return cleaned


def fishbase_summary_url(scientific_name: str) -> str:
    parts = scientific_name.split()
    if len(parts) < 2:
        raise ImporterError(f"Invalid scientific name: {scientific_name}")
    return f"{FB_BASE}/summary/{parts[0]}-{parts[1]}.html"


def parse_fishbase(html: str, url: str) -> dict[str, Any]:
    """Parse structured aquarium-relevant facts from a FishBase summary page."""
    soup, text = soup_text(html)

    h1 = soup.find("h1")
    if not h1:
        raise ImporterError("Could not find the FishBase species heading.")

    heading = norm_space(h1.get_text(" ", strip=True))
    sci_match = re.match(r"([A-Z][a-z]+ [a-z][a-z0-9._-]+)", heading)
    scientific_name = sci_match.group(1) if sci_match else ""

    common_name = ""
    for string in h1.find_all_next(string=True, limit=30):
        value = norm_space(str(string))
        if not value or value.casefold().startswith(scientific_name.casefold()):
            continue
        if len(value) <= 80 and not value.startswith(
            ("Upload", "Picture by", "Classification", "Common names")
        ):
            common_name = value
            break

    # FishBase heading text changes over time and between mirrors, so prefer
    # DOM heading boundaries and fall back to flattened-text extraction.
    environment = first_nonempty_text(
        heading_section(soup, "Environment"),
        extract_section(
            text,
            "Environment",
            (
                "Environment",
                "Distribution",
                "Size / Weight / Age",
                "Biology",
                "Main reference",
                "IUCN Red List Status",
            ),
        ),
    )
    distribution = first_nonempty_text(
        heading_section(soup, "Distribution"),
        extract_section(
            text,
            "Distribution",
            (
                "Environment",
                "Distribution",
                "Size / Weight / Age",
                "Biology",
                "Main reference",
                "IUCN Red List Status",
            ),
        ),
    )
    size_section = first_nonempty_text(
        heading_section(soup, "Size / Weight / Age"),
        extract_section(
            text,
            "Size / Weight / Age",
            (
                "Environment",
                "Distribution",
                "Size / Weight / Age",
                "Biology",
                "Main reference",
                "IUCN Red List Status",
            ),
        ),
    )
    biology = first_nonempty_text(
        heading_section(soup, "Biology"),
        extract_section(
            text,
            "Biology",
            (
                "Environment",
                "Distribution",
                "Size / Weight / Age",
                "Biology",
                "Main reference",
                "IUCN Red List Status",
            ),
        ),
    )

    # Search the intended section first, then the whole document. This catches
    # both semantic-heading pages and FishBase's more table-like HTML variants.
    temperature = (
        parse_temperature_any(environment)
        or parse_temperature_any(text)
    )
    ph = parse_ph_any(environment) or parse_ph_any(text)
    gh = parse_hardness_any(environment) or parse_hardness_any(text)

    adult_size_cm = None
    for source_text in (size_section, text):
        size_match = re.search(
            r"(?:Max(?:imum)?(?:\.|\s)*length|Max\.?\s*size)\s*:\s*"
            r"(\d+(?:\.\d+)?)\s*cm\b",
            source_text,
            re.I,
        )
        if size_match:
            adult_size_cm = float(size_match.group(1))
            break

    minimum_group = first_group_number(biology) or first_group_number(text)

    minimum_tank = None
    tank_match = re.search(
        r"minimum aquarium size\s*[:;]?\s*(\d+(?:\.\d+)?)\s*cm\b",
        biology or text,
        re.I,
    )
    if not tank_match:
        tank_match = re.search(
            r"minimum aquarium size\s*[:;]?\s*(\d+(?:\.\d+)?)\s*cm\b",
            text,
            re.I,
        )
    if tank_match:
        minimum_tank = {
            "length_cm": float(tank_match.group(1)),
            "width_cm": None,
            "height_cm": None,
        }

    env_lower = (environment or text).casefold()
    environment_types = [
        label
        for token, label in (
            ("freshwater", "freshwater"),
            ("brackish", "brackish"),
            ("marine", "marine"),
        )
        if token in env_lower
    ]
    environment_type = ", ".join(environment_types)

    ecology = ""
    for token in (
        "benthopelagic",
        "demersal",
        "pelagic",
        "benthic",
        "reef-associated",
        "potamodromous",
    ):
        if re.search(rf"\b{re.escape(token)}\b", env_lower):
            ecology = token
            break

    climate = ""
    for token in ("tropical", "subtropical", "temperate", "polar"):
        if re.search(rf"\b{token}\b", env_lower):
            climate = token
            break

    family = ""
    # Field-guide variants expose "Family: X"; summary pages often expose
    # hierarchy text "Acestrorhamphidae (American tetras)".
    family_match = re.search(r"\bFamily\s*:\s*([A-Z][A-Za-z0-9_-]+)", text)
    if family_match:
        family = family_match.group(1)

    iucn_status = ""
    iucn_match = re.search(
        r"IUCN Red List Status\s*:?\s*"
        r"([A-Za-z ]+?)\s*\(([A-Z]{2,3})\)",
        text,
        re.I,
    )
    if iucn_match:
        iucn_status = f"{norm_space(iucn_match.group(1))} ({iucn_match.group(2).upper()})"

    threat_to_humans = ""
    threat_match = re.search(
        r"Threat to humans\s*:\s*([^.;]{2,80})",
        text,
        re.I,
    )
    if threat_match:
        threat_to_humans = norm_space(threat_match.group(1))

    return {
        "common_name": common_name,
        "scientific_name": scientific_name,
        "adult_size_cm": adult_size_cm,
        "temperature": temperature,
        "ph": ph,
        "gh": gh,
        "minimum_group": minimum_group,
        "minimum_tank": minimum_tank,
        "family": family,
        "environment_type": environment_type,
        "ecology": ecology,
        "climate": climate,
        "distribution": distribution[:500] if distribution else "",
        "iucn_status": iucn_status,
        "threat_to_humans": threat_to_humans,
        "source_url": canonical_url(url),
    }


# ---------------------------------------------------------------------------
# SeriouslyFish parsing
# ---------------------------------------------------------------------------

def seriouslyfish_url(scientific_name: str) -> str:
    return f"{SF_BASE}/species/{scientific_slug(scientific_name)}"


def sf_names(soup: BeautifulSoup) -> tuple[str, str]:
    h1 = soup.find("h1")
    if not h1:
        raise ImporterError("Could not find the SeriouslyFish species heading.")

    scientific = norm_space(h1.get_text(" ", strip=True))
    common = ""

    # Current page titles: "Paracheirodon innesi (Neon Tetra) · Seriously Fish"
    title_candidates: list[str] = []
    if soup.title and soup.title.string:
        title_candidates.append(norm_space(soup.title.string))
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        title_candidates.append(norm_space(str(og["content"])))

    for title in title_candidates:
        match = re.search(
            rf"^{re.escape(scientific)}\s*\((.+?)\)\s*(?:[·|\-]|$)",
            title,
            re.I,
        )
        if match:
            common = norm_space(match.group(1))
            break

    if not common:
        for string in h1.find_all_next(string=True, limit=15):
            value = norm_space(str(string))
            if not value or value.casefold() == scientific.casefold():
                continue
            if value.startswith("Formerly ") or len(value) > 80:
                continue
            if re.search(r"\b(?:18|19|20)\d{2}\b", value):
                continue
            common = value
            break

    return scientific, common or scientific


def extract_axis(text: str, label: str, next_label: str) -> str:
    match = re.search(
        rf"\b{re.escape(label)}\s+(.+?)\s+{re.escape(next_label)}\b",
        text,
        re.I,
    )
    if not match:
        return ""
    value = norm_space(match.group(1))
    return value if len(value) <= 100 else ""


def parse_seriouslyfish(html: str, url: str) -> dict[str, Any]:
    """Parse structured aquarium-care facts from a SeriouslyFish species page."""
    soup, text = soup_text(html)
    scientific_name, common_name = sf_names(soup)

    quick_facts = first_nonempty_text(
        heading_section(soup, "Quick facts"),
        text,
    )
    aquarium_section = first_nonempty_text(
        heading_section(soup, "Aquarium size"),
        text,
    )
    water_section = first_nonempty_text(
        heading_section(soup, "Water conditions"),
        text,
    )
    behaviour = first_nonempty_text(
        heading_section(soup, "Behaviour and compatibility"),
        heading_section(soup, "Behaviour and Compatibility"),
        extract_section(
            text,
            "Behaviour and compatibility",
            (
                "Behaviour and compatibility",
                "Sexual dimorphism",
                "Reproduction",
                "Notes",
                "References",
            ),
        ),
    )

    adult_size_cm = None
    for source_text in (quick_facts, aquarium_section, text):
        length_match = re.search(
            r"\b(?:Length|Standard length|Max(?:imum)? length)\s*[:;]?\s*"
            r"(\d+(?:\.\d+)?)\s*(mm|cm)\b",
            source_text,
            re.I,
        )
        if length_match:
            value = float(length_match.group(1))
            adult_size_cm = (
                value / 10.0
                if length_match.group(2).casefold() == "mm"
                else value
            )
            adult_size_cm = round(adult_size_cm, 2)
            break

    minimum_tank = None
    tank_pattern = (
        r"\b(?:Tank|Aquarium)\s+(?:base|footprint)\s*[:;]?\s*"
        r"(\d+(?:\.\d+)?)\s*[×xX*∗]\s*"
        r"(\d+(?:\.\d+)?)\s*cm\b"
    )
    for source_text in (quick_facts, aquarium_section, text):
        tank_match = re.search(tank_pattern, source_text, re.I)
        if tank_match:
            minimum_tank = {
                "length_cm": float(tank_match.group(1)),
                "width_cm": float(tank_match.group(2)),
                "height_cm": None,
            }
            break

    aquarium_volume_l = None
    volume_match = re.search(
        r"\bVolume\s*[:;]?\s*~?\s*(\d+(?:\.\d+)?)\s*(?:litres?|L)\b",
        aquarium_section,
        re.I,
    )
    if volume_match:
        aquarium_volume_l = float(volume_match.group(1))

    temperature = (
        parse_temperature_any(water_section)
        or parse_temperature_any(quick_facts)
        or parse_temperature_any(text)
    )
    ph = (
        parse_ph_any(water_section)
        or parse_ph_any(quick_facts)
        or parse_ph_any(text)
    )
    gh = (
        parse_hardness_any(water_section)
        or parse_hardness_any(quick_facts)
        or parse_hardness_any(text)
    )

    temperament = extract_axis(text, "Temperament", "Social")
    social_type = extract_axis(text, "Social", "Compatibility")
    compatibility = extract_axis(text, "Compatibility", "Hardiness")

    minimum_group = first_group_number(behaviour)

    difficulty = ""
    diff_match = re.search(
        r"\bDifficulty(?:\s+\d+\s+of\s+\d+\s+measures)?\s+"
        r"(Easy|Moderate|Difficult|Very difficult|Expert)\b",
        text,
        re.I,
    )
    if diff_match:
        difficulty = diff_match.group(1).casefold()

    diet_type = ""
    diet_section = heading_section(soup, "Diet")
    for token in ("omnivorous", "carnivorous", "herbivorous", "insectivorous"):
        if token in diet_section.casefold():
            diet_type = token
            break

    cautions: list[str] = []
    if minimum_group and any(
        word in social_type.casefold() for word in ("shoal", "school", "group")
    ):
        cautions.append(f"Keep at least {minimum_group} individuals.")
    if "not a community" in compatibility.casefold():
        cautions.append("Not suitable for a general community aquarium.")

    return {
        "common_name": common_name,
        "scientific_name": scientific_name,
        "adult_size_cm": adult_size_cm,
        "temperature": temperature,
        "ph": ph,
        "gh": gh,
        "kh": None,
        "minimum_group": minimum_group,
        "minimum_tank": minimum_tank,
        "minimum_tank_volume_l": aquarium_volume_l,
        "social_type": social_type,
        "temperament": temperament,
        "compatibility": compatibility,
        "difficulty": difficulty,
        "diet_type": diet_type,
        "swimming_zone": "",
        "cautions": cautions,
        "source_url": canonical_url(url),
    }


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def name_score(query: str, displayed_name: str) -> float:
    q = norm_name(query)
    d = norm_name(displayed_name)
    if not q or not d:
        return 0.0
    if q == d:
        return 100.0

    q_tokens = set(q.split())
    d_tokens = set(d.split())
    overlap = len(q_tokens & d_tokens) / max(1, len(q_tokens | d_tokens))

    similarity = SequenceMatcher(None, q, d).ratio()
    contains_bonus = 0.15 if q in d or d in q else 0.0

    return min(99.0, (similarity * 55.0) + (overlap * 35.0) + (contains_bonus * 100.0))


def resolve_candidates(common_name: str, fetcher: Fetcher) -> list[Candidate]:
    search_html = fetcher.get(fishbase_search_url(common_name, exact=True))
    assert search_html is not None
    scientific_names = fishbase_candidates_from_search(search_html)

    if not scientific_names:
        search_html = fetcher.get(fishbase_search_url(common_name, exact=False))
        assert search_html is not None
        scientific_names = fishbase_candidates_from_search(search_html)

    if not scientific_names:
        raise ImporterError(
            f"FishBase returned no species candidates for {common_name!r}."
        )

    # Common-name pages can be broad. Keep the interaction reasonable.
    scientific_names = scientific_names[:20]
    candidates: list[Candidate] = []

    for scientific_name in scientific_names:
        fb_url = fishbase_summary_url(scientific_name)
        sf_url = seriouslyfish_url(scientific_name)

        fb_html = fetcher.get(fb_url, allow_404=True)
        sf_html = fetcher.get(sf_url, allow_404=True)

        fb_common = ""
        sf_common = ""

        if fb_html:
            try:
                fb_common = parse_fishbase(fb_html, fb_url).get("common_name", "")
            except ImporterError:
                pass

        if sf_html:
            try:
                sf_common = parse_seriouslyfish(sf_html, sf_url).get("common_name", "")
            except ImporterError:
                pass

        fb_score = name_score(common_name, fb_common)
        sf_score = name_score(common_name, sf_common)

        # Strongly value an exact SeriouslyFish display-name match because it
        # describes the aquarium-trade name on that profile.
        total = max(fb_score, sf_score)
        if sf_score == 100:
            total += 25
        if fb_score == 100:
            total += 10

        candidates.append(
            Candidate(
                scientific_name=scientific_name,
                fishbase_url=fb_url,
                seriouslyfish_url=sf_url,
                fishbase_common_name=fb_common,
                seriouslyfish_common_name=sf_common,
                score=total,
                fishbase_html=fb_html,
                seriouslyfish_html=sf_html,
            )
        )

    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates


def choose_candidate(
    query: str,
    candidates: list[Candidate],
    *,
    assume_best: bool = False,
) -> Candidate:
    if not candidates:
        raise ImporterError("No candidate species were found.")

    if len(candidates) == 1:
        return candidates[0]

    best = candidates[0]
    second = candidates[1]

    # Auto-resolve a clearly exact result.
    if best.score >= 110 and best.score - second.score >= 15:
        return best

    if assume_best:
        return best

    print("\nPossible species matches:")
    for index, candidate in enumerate(candidates[:10], start=1):
        names = []
        if candidate.seriouslyfish_common_name:
            names.append(f"SeriouslyFish: {candidate.seriouslyfish_common_name}")
        if candidate.fishbase_common_name:
            names.append(f"FishBase: {candidate.fishbase_common_name}")
        suffix = " | ".join(names) if names else "no display name parsed"
        print(
            f"  {index}. {candidate.scientific_name} "
            f"({suffix}; match {candidate.score:.0f})"
        )

    while True:
        try:
            answer = input(
                f"\nChoose the species for {query!r} [1-{min(10, len(candidates))}, "
                "or q to cancel]: "
            ).strip()
        except EOFError:
            raise ImporterError(
                "Species match is ambiguous. Re-run interactively or use --best-match."
            )

        if answer.casefold() in {"q", "quit", "cancel"}:
            raise ImporterError("Cancelled.")

        try:
            number = int(answer)
        except ValueError:
            continue
        if 1 <= number <= min(10, len(candidates)):
            return candidates[number - 1]


# ---------------------------------------------------------------------------
# Merging + library writing
# ---------------------------------------------------------------------------

def first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return deepcopy(value)
    return None


def merge_sources(
    requested_common_name: str,
    sf: dict[str, Any] | None,
    fb: dict[str, Any] | None,
) -> dict[str, Any]:
    if not sf and not fb:
        raise ImporterError("Neither SeriouslyFish nor FishBase profile could be parsed.")

    scientific_name = first_nonempty(
        (sf or {}).get("scientific_name"),
        (fb or {}).get("scientific_name"),
    )
    common_name = first_nonempty(
        (sf or {}).get("common_name"),
        (fb or {}).get("common_name"),
        requested_common_name,
    )

    primary_source = None
    sources: list[dict[str, str]] = []
    if sf:
        sf_source = {"label": "Seriously Fish", "url": sf["source_url"]}
        sources.append(sf_source)
        primary_source = sf_source
    if fb:
        fb_source = {"label": "FishBase", "url": fb["source_url"]}
        sources.append(fb_source)
        primary_source = primary_source or fb_source

    # For aquarium-care parameters, SeriouslyFish is primary and FishBase only
    # fills a gap. FishBase values remain visible in reference_data below.
    species = {
        "id": slugify_id(scientific_name),
        "common_name": common_name,
        "scientific_name": scientific_name,
        "aliases": [],
        "adult_size_cm": first_nonempty(
            (sf or {}).get("adult_size_cm"),
            (fb or {}).get("adult_size_cm"),
        ),
        "temperature": first_nonempty(
            (sf or {}).get("temperature"),
            (fb or {}).get("temperature"),
        ),
        "ph": first_nonempty(
            (sf or {}).get("ph"),
            (fb or {}).get("ph"),
        ),
        "gh": first_nonempty(
            (sf or {}).get("gh"),
            (fb or {}).get("gh"),
        ),
        "kh": (sf or {}).get("kh"),
        "minimum_group": first_nonempty(
            (sf or {}).get("minimum_group"),
            (fb or {}).get("minimum_group"),
        ),
        "minimum_tank": first_nonempty(
            (sf or {}).get("minimum_tank"),
            (fb or {}).get("minimum_tank"),
        ),
        "social_type": (sf or {}).get("social_type", ""),
        "temperament": (sf or {}).get("temperament", ""),
        "swimming_zone": (sf or {}).get("swimming_zone", ""),
        "cautions": list((sf or {}).get("cautions", [])),
        "source": primary_source,
        "sources": sources,
        "reference_data": {
            "seriouslyfish": {
                key: deepcopy((sf or {}).get(key))
                for key in (
                    "adult_size_cm",
                    "temperature",
                    "ph",
                    "gh",
                    "minimum_group",
                    "minimum_tank",
                    "minimum_tank_volume_l",
                    "compatibility",
                    "difficulty",
                    "diet_type",
                )
            } if sf else None,
            "fishbase": {
                key: deepcopy((fb or {}).get(key))
                for key in (
                    "adult_size_cm",
                    "temperature",
                    "ph",
                    "gh",
                    "minimum_group",
                    "minimum_tank",
                    "family",
                    "environment_type",
                    "ecology",
                    "climate",
                    "distribution",
                    "iucn_status",
                    "threat_to_humans",
                )
            } if fb else None,
        },
    }

    # The entered common name is useful as an alias if it differs.
    if norm_name(requested_common_name) != norm_name(common_name):
        species["aliases"].append(requested_common_name)

    return species


def load_library(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": 1,
            "library_name": "Aquarium Manager Fish Library",
            "updated": date.today().isoformat(),
            "species": [],
        }

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ImporterError(
            f"{path} contains invalid JSON at line {exc.lineno}, column {exc.colno}."
        ) from exc

    if not isinstance(data, dict):
        raise ImporterError("fish_library.json must contain a JSON object.")
    if data.get("schema_version") != 1:
        raise ImporterError("Only fish-library schema_version 1 is supported.")
    if not isinstance(data.get("species"), list):
        raise ImporterError("fish_library.json must contain a species array.")
    return data


def validate_species_basic(species: dict[str, Any]) -> None:
    if not species.get("id") or not species.get("common_name"):
        raise ImporterError("Species record needs id and common_name.")

    for key in ("temperature", "ph", "gh", "kh"):
        value = species.get(key)
        if value is None:
            continue
        if not isinstance(value, dict) or "min" not in value or "max" not in value:
            raise ImporterError(f"{key} must be null or a min/max object.")
        if float(value["min"]) > float(value["max"]):
            raise ImporterError(f"{key}: min is greater than max.")


def validate_schema(library: dict[str, Any], schema_path: Path | None) -> None:
    if not schema_path or not schema_path.exists():
        return
    try:
        import jsonschema
    except ImportError:
        print(
            "Note: schema.json exists but jsonschema is not installed; "
            "basic validation only.",
            file=sys.stderr,
        )
        return

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(library, schema)
    except Exception as exc:
        raise ImporterError(f"Schema validation failed: {exc}") from exc


def find_existing_index(
    species_list: list[dict[str, Any]],
    incoming: dict[str, Any],
) -> tuple[int | None, str | None]:
    sci = norm_name(str(incoming.get("scientific_name", "")))
    incoming_sources = {
        canonical_url(str(item.get("url")))
        for item in incoming.get("sources", [])
        if isinstance(item, dict) and item.get("url")
    }

    for index, item in enumerate(species_list):
        existing_sci = norm_name(str(item.get("scientific_name", "")))
        if sci and existing_sci == sci:
            return index, "scientific name"

        existing_sources = set()
        source = item.get("source")
        if isinstance(source, dict) and source.get("url"):
            existing_sources.add(canonical_url(str(source["url"])))
        for source in item.get("sources", []):
            if isinstance(source, dict) and source.get("url"):
                existing_sources.add(canonical_url(str(source["url"])))

        if incoming_sources & existing_sources:
            return index, "source URL"

    return None, None


def update_existing(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """
    Refresh source-derived fields while preserving manual-only fields.

    Existing aliases, KH, swimming zone, cautions and any unknown/custom fields
    are retained. Source data and primary care parameters are refreshed.
    """
    result = deepcopy(existing)

    for key in (
        "common_name",
        "scientific_name",
        "adult_size_cm",
        "temperature",
        "ph",
        "gh",
        "minimum_group",
        "minimum_tank",
        "social_type",
        "temperament",
        "source",
        "sources",
        "reference_data",
    ):
        value = incoming.get(key)
        if value not in (None, "", [], {}):
            result[key] = deepcopy(value)

    result.setdefault("id", incoming["id"])

    aliases = result.get("aliases")
    if not isinstance(aliases, list):
        aliases = []
    for alias in incoming.get("aliases", []):
        if alias and alias not in aliases:
            aliases.append(alias)
    result["aliases"] = aliases

    # Preserve explicitly user-managed fields if present.
    result.setdefault("kh", incoming.get("kh"))
    result.setdefault("swimming_zone", incoming.get("swimming_zone", ""))

    old_cautions = result.get("cautions")
    if not isinstance(old_cautions, list):
        old_cautions = []
    for caution in incoming.get("cautions", []):
        if caution and caution not in old_cautions:
            old_cautions.append(caution)
    result["cautions"] = old_cautions

    return result


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}")
    shutil.copy2(path, backup)
    return backup


def atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def yes_no(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N]: ").strip().casefold() in {"y", "yes"}
    except EOFError:
        return False




# ---------------------------------------------------------------------------
# v2.4 generic common/scientific-name resolution
# ---------------------------------------------------------------------------

SCIENTIFIC_QUERY_RE = re.compile(
    r"""^[A-Z][A-Za-z.-]+\s+(?:
        [a-z][a-z0-9._-]+
        |
        sp\.\s*['"][^'"]+['"]
        |
        sp\.\s+[A-Za-z0-9._'-]+
        |
        cf\.\s+[a-z][a-z0-9._-]+
        |
        aff\.\s+[a-z][a-z0-9._-]+
    )$""",
    re.X,
)


def looks_like_scientific_name(value: str) -> bool:
    """Detect ordinary binomials and aquarium sp./cf./aff. designations."""
    return bool(SCIENTIFIC_QUERY_RE.fullmatch(norm_space(value)))


def parse_search_term(value: str) -> tuple[str, str, str]:
    """
    Return (mode, common_query, scientific_query).

    Bare two-word input such as "Cardinal tetra" can look exactly like a Latin
    binomial from capitalisation alone. Such queries are therefore classified
    as "auto": try an exact scientific-name lookup first, and only accept that
    interpretation when a source confirms the exact taxon. Otherwise retry the
    same text as a common name.

    Examples:
      Neon Tetra                              -> common
      Cardinal tetra                         -> auto
      Paracheirodon innesi                   -> auto
      Bristlenose Pleco                      -> common
      Bristlenose Pleco (Ancistrus sp. '3')  -> mixed
    """
    value = norm_space(value)

    common, hint = split_query(value)
    if hint and looks_like_scientific_name(hint):
        return "mixed", common, hint

    if looks_like_scientific_name(value):
        return "auto", value, value

    return "common", value, ""


def ordinary_binomial(value: str) -> bool:
    """True for Genus species names that both sites can address directly."""
    return bool(re.fullmatch(r"[A-Z][a-z]+ [a-z][a-z0-9._-]+", norm_space(value)))


def direct_seriouslyfish_match(
    scientific_name: str,
    fetcher: Fetcher,
) -> dict[str, Any] | None:
    """Try the predictable SeriouslyFish species URL for a normal binomial."""
    if not ordinary_binomial(scientific_name):
        return None

    url = seriouslyfish_url(scientific_name)
    try:
        html = fetcher.get(url, allow_404=True)
    except ImporterError:
        return None
    if not html:
        return None

    try:
        data = parse_seriouslyfish(html, url)
    except ImporterError:
        return None

    if norm_name(data.get("scientific_name", "")) != norm_name(scientific_name):
        return None

    return {
        "data": data,
        "html": html,
        "url": url,
        "score": 1000.0,
    }


def direct_fishbase_match(
    scientific_name: str,
    fetcher: Fetcher,
) -> dict[str, Any] | None:
    """Fetch a FishBase summary directly when given a normal binomial."""
    if not ordinary_binomial(scientific_name):
        return None

    url = fishbase_summary_url(scientific_name)
    try:
        html = fetcher.get(url, allow_404=True)
    except ImporterError:
        return None
    if not html:
        return None

    try:
        data = parse_fishbase(html, url)
    except ImporterError:
        return None

    if norm_name(data.get("scientific_name", "")) != norm_name(scientific_name):
        return None

    return {
        "data": data,
        "html": html,
        "url": url,
        "score": 1000.0,
    }


def _sf_card_text(anchor: Any) -> str:
    """
    Get the smallest useful species-card text around a SeriouslyFish link.
    Current directory pages put most card content inside the species link itself,
    but this also tolerates article/list wrappers.
    """
    anchor_text = norm_space(anchor.get_text(" ", strip=True))
    if len(anchor_text) >= 20:
        return anchor_text

    for parent_name in ("article", "li"):
        container = anchor.find_parent(parent_name)
        if container is not None:
            value = norm_space(container.get_text(" ", strip=True))
            if 10 <= len(value) <= 1800:
                return value

    parent = anchor.parent
    if parent is not None:
        value = norm_space(parent.get_text(" ", strip=True))
        if 10 <= len(value) <= 1800:
            return value

    return anchor_text


def _sf_index_cache_path(library_path: Path) -> Path:
    return library_path.parent / ".seriouslyfish_species_index.json"


def load_sf_index_cache(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        return []
    entries = []
    for item in payload["entries"]:
        if isinstance(item, dict) and item.get("url") and item.get("text"):
            entries.append({"url": str(item["url"]), "text": str(item["text"])})
    return entries


def save_sf_index_cache(path: Path, entries: list[dict[str, str]]) -> None:
    payload = {
        "built": date.today().isoformat(),
        "entries": entries,
    }
    atomic_write(path, payload)


def build_seriouslyfish_index(
    fetcher: Fetcher,
    cache_path: Path,
    *,
    max_pages: int = 120,
) -> list[dict[str, str]]:
    """
    Build a lightweight index of SeriouslyFish's paginated Every Species list.

    This is only needed when a common name could not first be resolved to a
    scientific name. The cache is then reused by future imports.
    """
    cached = load_sf_index_cache(cache_path)
    if cached:
        return cached

    print(
        "  Building SeriouslyFish species-name index (first common-name fallback only)..."
    )

    entries_by_url: dict[str, str] = {}
    empty_pages = 0

    for page in range(1, max_pages + 1):
        url = f"{SF_BASE}/species/" if page == 1 else f"{SF_BASE}/species?page={page}"
        try:
            html = fetcher.get(url, allow_404=True)
        except ImporterError as exc:
            print(f"  SeriouslyFish index warning on page {page}: {exc}", file=sys.stderr)
            break

        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")
        page_new = 0

        for anchor in soup.find_all("a", href=True):
            absolute = canonical_url(urljoin(SF_BASE, str(anchor.get("href"))))
            if not _species_link(absolute):
                continue

            card_text = _sf_card_text(anchor)
            if not card_text:
                continue

            if absolute not in entries_by_url:
                entries_by_url[absolute] = card_text
                page_new += 1
            elif len(card_text) > len(entries_by_url[absolute]):
                entries_by_url[absolute] = card_text

        if page_new == 0:
            empty_pages += 1
        else:
            empty_pages = 0

        # Past the end, pages stop introducing species.
        if empty_pages >= 2:
            break

    entries = [
        {"url": url, "text": value}
        for url, value in entries_by_url.items()
    ]

    if entries:
        save_sf_index_cache(cache_path, entries)

    return entries


def seriouslyfish_index_candidates(
    query: str,
    scientific_hint: str,
    fetcher: Fetcher,
    cache_path: Path,
) -> list[dict[str, Any]]:
    """Resolve common names or unusual scientific designations via the full SF index."""
    entries = build_seriouslyfish_index(fetcher, cache_path)
    if not entries:
        return []

    qn = norm_name(query)
    hn = norm_name(scientific_hint)
    scored: list[tuple[float, dict[str, str]]] = []

    for item in entries:
        text_n = norm_name(item["text"])
        score = 0.0

        if qn:
            if qn in text_n:
                score += 200
            score += name_score(query, item["text"])

        if hn:
            if hn in text_n:
                score += 500
            else:
                score += name_score(scientific_hint, item["text"])

        if score > 0:
            scored.append((score, item))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    parsed: list[dict[str, Any]] = []
    for score, item in scored[:10]:
        try:
            html = fetcher.get(item["url"], allow_404=True)
        except ImporterError:
            continue
        if not html:
            continue
        try:
            data = parse_seriouslyfish(html, item["url"])
        except ImporterError:
            continue

        final_score = score
        if query:
            final_score += name_score(query, data.get("common_name", ""))
        if scientific_hint:
            if norm_name(data.get("scientific_name", "")) == hn:
                final_score += 1000
            else:
                final_score -= 300

        parsed.append(
            {
                "data": data,
                "html": html,
                "url": item["url"],
                "score": final_score,
            }
        )

    return sorted(parsed, key=lambda item: item["score"], reverse=True)


def resolve_scientific_query(
    scientific_name: str,
    fetcher: Fetcher,
    library_path: Path,
    *,
    common_alias: str = "",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Resolve the same scientific taxon independently on both sites."""
    sf_match = direct_seriouslyfish_match(scientific_name, fetcher)
    fb_match = direct_fishbase_match(scientific_name, fetcher)

    # Unusual SF designations such as Ancistrus sp. '3' do not necessarily have
    # predictable profile slugs. Use the full cached SF index as a fallback.
    if sf_match is None:
        sf_candidates = seriouslyfish_index_candidates(
            common_alias,
            scientific_name,
            fetcher,
            _sf_index_cache_path(library_path),
        )
        sf_match = choose_source_match(
            "SeriouslyFish",
            common_alias,
            scientific_name,
            sf_candidates,
            assume_best=True,
        )

    return sf_match, fb_match


def resolve_auto_query(
    query: str,
    fetcher: Fetcher,
    library_path: Path,
    *,
    assume_best: bool,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """
    Resolve an ambiguous binomial-shaped query safely.

    First try the text as an exact scientific name. That interpretation is kept
    only if at least one source returns the exact same scientific name.
    Otherwise the same text is retried as a common name.
    """
    sf_direct, fb_direct = resolve_scientific_query(
        query,
        fetcher,
        library_path,
    )

    query_norm = norm_name(query)
    confirmed = False

    for match in (sf_direct, fb_direct):
        if not match:
            continue
        resolved_name = norm_name(match["data"].get("scientific_name", ""))
        if resolved_name == query_norm:
            confirmed = True
            break

    if confirmed:
        return "scientific", sf_direct, fb_direct

    sf_common, fb_common = resolve_common_query(
        query,
        fetcher,
        library_path,
        assume_best=assume_best,
    )
    return "common", sf_common, fb_common


def resolve_common_query(
    common_name: str,
    fetcher: Fetcher,
    library_path: Path,
    *,
    assume_best: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """
    Resolve a common name.

    First use FishBase's common-name result if possible because that immediately
    supplies a scientific name. If FishBase cannot resolve it, search the full
    cached SeriouslyFish species index. Whichever source resolves the taxon then
    drives a direct lookup on the other source.
    """
    fb_candidates = fishbase_independent_candidates(common_name, fetcher)
    fb_match = choose_source_match(
        "FishBase",
        common_name,
        "",
        fb_candidates,
        assume_best=assume_best,
    )

    if fb_match:
        scientific = fb_match["data"].get("scientific_name", "")
        sf_match = direct_seriouslyfish_match(scientific, fetcher)
        if sf_match:
            # Prefer the SF profile only when its common name also reasonably
            # resembles the user query. Otherwise preserve ambiguity.
            if name_score(common_name, sf_match["data"].get("common_name", "")) >= 70:
                return sf_match, fb_match

    # FishBase did not resolve the common name cleanly. Search all SF species,
    # using a local cache so this expensive fallback is paid only once.
    sf_candidates = seriouslyfish_index_candidates(
        common_name,
        "",
        fetcher,
        _sf_index_cache_path(library_path),
    )
    sf_match = choose_source_match(
        "SeriouslyFish",
        common_name,
        "",
        sf_candidates,
        assume_best=assume_best,
    )

    if sf_match:
        scientific = sf_match["data"].get("scientific_name", "")
        exact_fb = direct_fishbase_match(scientific, fetcher)
        return sf_match, exact_fb

    # SF had nothing either; FishBase-only is still valid.
    return None, fb_match

# ---------------------------------------------------------------------------
# v2.2 independent source resolution + compact provenance
# ---------------------------------------------------------------------------

def split_query(value: str) -> tuple[str, str]:
    """
    Split a user query into common-name text and an optional scientific hint.

    Example:
        "Bristlenose Pleco (Ancistrus sp. '3')"
        -> ("Bristlenose Pleco", "Ancistrus sp. '3'")
    """
    value = norm_space(value)
    match = re.search(r"\(([^()]+)\)\s*$", value)
    if not match:
        return value, ""

    hint = norm_space(match.group(1))
    common = norm_space(value[: match.start()])
    if re.match(r"^[A-Z][A-Za-z.-]+\s+.+$", hint):
        return common or value, hint
    return value, ""


def _species_link(url: str) -> bool:
    path = urlsplit(url).path
    return bool(re.fullmatch(r"/species/[^/]+/?", path))


def seriouslyfish_directory_candidates(
    common_query: str,
    scientific_hint: str,
    fetcher: Fetcher,
) -> list[dict[str, Any]]:
    """
    Search SeriouslyFish independently via its Every Species directory.

    The directory contains all current species profiles. We shortlist cards by
    their visible text, then open only the best few pages and parse the actual
    profile names before choosing anything.
    """
    directory_url = f"{SF_BASE}/species/"
    html = fetcher.get(directory_url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    seen: dict[str, dict[str, Any]] = {}

    q = norm_name(common_query)
    hint_n = norm_name(scientific_hint)

    for anchor in soup.find_all("a", href=True):
        absolute = canonical_url(urljoin(SF_BASE, str(anchor.get("href"))))
        if not _species_link(absolute):
            continue

        # Grab enough surrounding card text to include scientific + common name,
        # but avoid swallowing the whole directory when markup changes.
        container = anchor.find_parent(["article", "li"])
        if container is None:
            container = anchor.parent
        card_text = norm_space(
            container.get_text(" ", strip=True) if container else anchor.get_text(" ", strip=True)
        )
        if len(card_text) > 1200:
            card_text = norm_space(anchor.get_text(" ", strip=True))

        card_n = norm_name(card_text)
        score = 0.0

        if q:
            score += name_score(common_query, card_text)
            q_tokens = set(q.split())
            c_tokens = set(card_n.split())
            if q_tokens:
                score += 60.0 * (len(q_tokens & c_tokens) / len(q_tokens))

        if hint_n:
            if hint_n == card_n:
                score += 350
            elif hint_n in card_n:
                score += 300
            else:
                hint_tokens = set(hint_n.split())
                if hint_tokens:
                    score += 100.0 * (len(hint_tokens & set(card_n.split())) / len(hint_tokens))

        if score <= 0:
            continue

        current = seen.get(absolute)
        if current is None or score > current["score"]:
            seen[absolute] = {
                "url": absolute,
                "directory_text": card_text,
                "score": score,
            }

    shortlisted = sorted(
        seen.values(),
        key=lambda item: item["score"],
        reverse=True,
    )[:8]

    parsed: list[dict[str, Any]] = []
    for item in shortlisted:
        profile_html = fetcher.get(item["url"], allow_404=True)
        if not profile_html:
            continue
        try:
            data = parse_seriouslyfish(profile_html, item["url"])
        except ImporterError:
            continue

        score = item["score"]
        if common_query:
            score += name_score(common_query, data.get("common_name", ""))
        if scientific_hint:
            if norm_name(data.get("scientific_name", "")) == hint_n:
                score += 1000
            else:
                score -= 250

        parsed.append(
            {
                "data": data,
                "html": profile_html,
                "url": item["url"],
                "score": score,
            }
        )

    return sorted(parsed, key=lambda item: item["score"], reverse=True)


def fishbase_independent_candidates(
    common_query: str,
    fetcher: Fetcher,
) -> list[dict[str, Any]]:
    """
    Search FishBase independently without allowing one bad candidate to abort.

    FishBase search forms can expose placeholder/stale candidate values.
    Invalid names are filtered before URL generation, and HTTP failures on a
    single candidate are skipped.
    """
    scientific_names: list[str] = []

    for exact in (True, False):
        try:
            html = fetcher.get(fishbase_search_url(common_query, exact=exact))
        except ImporterError as exc:
            print(f"  FishBase search warning: {exc}", file=sys.stderr)
            html = None

        if html:
            scientific_names = [
                name
                for name in fishbase_candidates_from_search(html)
                if valid_fishbase_scientific_name(name)
            ]

        if scientific_names:
            break

    results: list[dict[str, Any]] = []

    for scientific_name in scientific_names[:12]:
        if not valid_fishbase_scientific_name(scientific_name):
            continue

        url = fishbase_summary_url(scientific_name)

        try:
            html = fetcher.get(url, allow_404=True)
        except ImporterError as exc:
            print(
                f"  FishBase candidate skipped: {scientific_name} ({exc})",
                file=sys.stderr,
            )
            continue

        if not html:
            continue

        try:
            data = parse_fishbase(html, url)
        except ImporterError as exc:
            print(
                f"  FishBase candidate could not be parsed: "
                f"{scientific_name} ({exc})",
                file=sys.stderr,
            )
            continue

        parsed_scientific = norm_space(str(data.get("scientific_name", "")))

        if (
            parsed_scientific
            and norm_name(parsed_scientific) != norm_name(scientific_name)
        ):
            print(
                f"  FishBase candidate skipped: requested {scientific_name}, "
                f"page resolved to {parsed_scientific}",
                file=sys.stderr,
            )
            continue

        results.append(
            {
                "data": data,
                "html": html,
                "url": url,
                "score": name_score(
                    common_query,
                    data.get("common_name", ""),
                ),
            }
        )

    return sorted(results, key=lambda item: item["score"], reverse=True)


def choose_source_match(
    label: str,
    common_query: str,
    scientific_hint: str,
    candidates: list[dict[str, Any]],
    *,
    assume_best: bool,
) -> dict[str, Any] | None:
    """Choose one source result without silently changing taxa."""
    if not candidates:
        return None

    hint_n = norm_name(scientific_hint)

    if hint_n:
        exact = [
            item
            for item in candidates
            if norm_name(item["data"].get("scientific_name", "")) == hint_n
        ]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            return exact[0]
        # Critical safety rule: if the user supplied a taxon hint and this
        # source does not have that exact taxon, do not substitute a related fish.
        return None

    if len(candidates) == 1:
        return candidates[0]

    best = candidates[0]
    second = candidates[1]
    if best["score"] >= 95 and best["score"] - second["score"] >= 18:
        return best
    if assume_best:
        return best

    print(f"\nPossible {label} matches:")
    for index, item in enumerate(candidates[:8], start=1):
        data = item["data"]
        print(
            f"  {index}. {data.get('common_name') or '?'} — "
            f"{data.get('scientific_name') or '?'} "
            f"(match {item['score']:.0f})"
        )

    while True:
        try:
            answer = input(
                f"Choose the {label} match [1-{min(8, len(candidates))}, "
                "0 = no match]: "
            ).strip()
        except EOFError:
            return None

        try:
            number = int(answer)
        except ValueError:
            continue

        if number == 0:
            return None
        if 1 <= number <= min(8, len(candidates)):
            return candidates[number - 1]


def sources_same_taxon(
    sf_data: dict[str, Any] | None,
    fb_data: dict[str, Any] | None,
) -> bool:
    if not sf_data or not fb_data:
        return True
    return (
        norm_name(sf_data.get("scientific_name", ""))
        == norm_name(fb_data.get("scientific_name", ""))
    )


def values_differ(a: Any, b: Any) -> bool:
    if a in (None, "", [], {}) or b in (None, "", [], {}):
        return False
    return a != b


def merge_sources_compact(
    requested_common_name: str,
    sf: dict[str, Any] | None,
    fb: dict[str, Any] | None,
    *,
    fishbase_status: str = "not_found",
    fishbase_related_name: str = "",
) -> dict[str, Any]:
    """
    Store one canonical value per parameter plus provenance.

    SeriouslyFish has priority for aquarium-care fields when both describe the
    same taxon. FishBase fills missing values. We do not store duplicate raw
    per-site data blocks.
    """
    if not sf and not fb:
        raise ImporterError("Neither SeriouslyFish nor FishBase produced a usable profile.")

    if sf and fb and not sources_same_taxon(sf, fb):
        raise ImporterError(
            "SeriouslyFish and FishBase resolved to different scientific names; "
            "refusing to merge them."
        )

    today = date.today().isoformat()
    scientific_name = first_nonempty(
        (sf or {}).get("scientific_name"),
        (fb or {}).get("scientific_name"),
    )
    common_name = first_nonempty(
        (sf or {}).get("common_name"),
        (fb or {}).get("common_name"),
        requested_common_name,
    )

    fields = (
        "adult_size_cm",
        "temperature",
        "ph",
        "gh",
        "kh",
        "minimum_group",
        "minimum_tank",
        "minimum_tank_volume_l",
        "social_type",
        "temperament",
        "compatibility",
        "difficulty",
        "diet_type",
        "family",
        "environment_type",
        "ecology",
        "climate",
        "distribution",
        "iucn_status",
        "threat_to_humans",
    )

    result: dict[str, Any] = {
        "id": slugify_id(scientific_name),
        "common_name": common_name,
        "scientific_name": scientific_name,
        "aliases": [],
        "kh": None,
        "swimming_zone": "",
        "cautions": [],
        "field_sources": {},
        "sources": {},
        "source_notes": [],
    }

    for field in fields:
        sf_value = (sf or {}).get(field)
        fb_value = (fb or {}).get(field)

        # Aquarium-care priority: SF first, then FB. This same rule is harmless
        # for taxonomic/ecology extras because SF usually leaves them blank.
        value = first_nonempty(sf_value, fb_value)
        if value not in (None, "", [], {}):
            result[field] = value
            result["field_sources"][field] = (
                "seriouslyfish"
                if sf_value not in (None, "", [], {})
                else "fishbase"
            )

        if values_differ(sf_value, fb_value):
            result["source_notes"].append(
                f"{field} differs between sources; SeriouslyFish value used."
            )

    # Preserve the user-entered common name as an alias when the profile uses
    # a different shop/trade name.
    if norm_name(requested_common_name) != norm_name(common_name):
        result["aliases"].append(requested_common_name)

    if sf:
        result["sources"]["seriouslyfish"] = {
            "status": "found",
            "url": sf["source_url"],
            "checked": today,
        }
        result["cautions"] = list(sf.get("cautions", []))
    else:
        result["sources"]["seriouslyfish"] = {
            "status": "not_found",
            "checked": today,
        }

    if fb:
        result["sources"]["fishbase"] = {
            "status": "found",
            "url": fb["source_url"],
            "checked": today,
        }
    else:
        fb_source: dict[str, Any] = {
            "status": fishbase_status,
            "checked": today,
        }
        if fishbase_related_name:
            fb_source["related_candidate"] = fishbase_related_name
        result["sources"]["fishbase"] = fb_source

    return result


def find_existing_index_v22(
    species_list: list[dict[str, Any]],
    incoming: dict[str, Any],
) -> tuple[int | None, str | None]:
    sci = norm_name(str(incoming.get("scientific_name", "")))

    incoming_urls: set[str] = set()
    sources = incoming.get("sources", {})
    if isinstance(sources, dict):
        for source in sources.values():
            if isinstance(source, dict) and source.get("url"):
                incoming_urls.add(canonical_url(str(source["url"])))

    for index, item in enumerate(species_list):
        existing_sci = norm_name(str(item.get("scientific_name", "")))
        if sci and existing_sci == sci:
            return index, "scientific name"

        existing_urls: set[str] = set()
        old_sources = item.get("sources", {})
        if isinstance(old_sources, dict):
            for source in old_sources.values():
                if isinstance(source, dict) and source.get("url"):
                    existing_urls.add(canonical_url(str(source["url"])))
        elif isinstance(old_sources, list):
            for source in old_sources:
                if isinstance(source, dict) and source.get("url"):
                    existing_urls.add(canonical_url(str(source["url"])))

        source = item.get("source")
        if isinstance(source, dict) and source.get("url"):
            existing_urls.add(canonical_url(str(source["url"])))

        if incoming_urls & existing_urls:
            return index, "source URL"

    return None, None


def update_existing_v22(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Update source-derived fields while preserving explicit local-only fields."""
    result = deepcopy(existing)

    replace_fields = (
        "common_name",
        "scientific_name",
        "adult_size_cm",
        "temperature",
        "ph",
        "gh",
        "minimum_group",
        "minimum_tank",
        "minimum_tank_volume_l",
        "social_type",
        "temperament",
        "compatibility",
        "difficulty",
        "diet_type",
        "family",
        "environment_type",
        "ecology",
        "climate",
        "distribution",
        "iucn_status",
        "threat_to_humans",
        "field_sources",
        "sources",
        "source_notes",
    )
    for key in replace_fields:
        if key in incoming:
            result[key] = deepcopy(incoming[key])

    result.setdefault("id", incoming["id"])

    aliases = result.get("aliases")
    if not isinstance(aliases, list):
        aliases = []
    for alias in incoming.get("aliases", []):
        if alias and alias not in aliases:
            aliases.append(alias)
    result["aliases"] = aliases

    # KH/swimming zone may be manually maintained because neither site reliably
    # provides them in the standard profile data we scrape.
    if "kh" not in result:
        result["kh"] = incoming.get("kh")
    result.setdefault("swimming_zone", incoming.get("swimming_zone", ""))

    old_cautions = result.get("cautions")
    if not isinstance(old_cautions, list):
        old_cautions = []
    for caution in incoming.get("cautions", []):
        if caution and caution not in old_cautions:
            old_cautions.append(caution)
    result["cautions"] = old_cautions

    # Remove the bulky v2.1 duplicated source data when updating an old record.
    result.pop("reference_data", None)
    result.pop("source", None)

    return result


def print_compact_coverage(
    sf: dict[str, Any] | None,
    fb: dict[str, Any] | None,
) -> None:
    print("\nSource result:")
    if sf:
        print(
            f"  ✓ SeriouslyFish: {sf.get('common_name')} — "
            f"{sf.get('scientific_name')}"
        )
    else:
        print("  ✗ SeriouslyFish: no matching profile")

    if fb:
        print(
            f"  ✓ FishBase:      {fb.get('common_name')} — "
            f"{fb.get('scientific_name')}"
        )
    else:
        print("  ✗ FishBase:      no exact matching taxon")


def parser_v24() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Search by common name or scientific name, retrieve matching "
            "SeriouslyFish/FishBase data, and add/update fish_library.json."
        )
    )
    p.add_argument(
        "query",
        nargs="?",
        help=(
            "Fish common name, scientific name, or common name with scientific "
            "hint. Examples: 'Neon Tetra', 'Paracheirodon innesi', "
            "\"Bristlenose Pleco (Ancistrus sp. '3')\"."
        ),
    )
    p.add_argument(
        "--library",
        type=Path,
        default=Path("fish_library.json"),
        help="Path to fish_library.json (default: ./fish_library.json).",
    )
    p.add_argument(
        "--schema",
        type=Path,
        default=None,
        help="Optional schema.json path. Defaults to schema.json beside the library.",
    )
    p.add_argument("--alias", action="append", default=[], help="Add a search alias.")
    p.add_argument("--dry-run", action="store_true", help="Do not modify the library.")
    p.add_argument("--yes", action="store_true", help="Skip final confirmation.")
    p.add_argument(
        "--best-match",
        action="store_true",
        help="Choose the top common-name match instead of prompting when ambiguous.",
    )
    p.add_argument("--sort", action="store_true", help="Sort by common name after import.")
    p.add_argument(
        "--rebuild-sf-index",
        action="store_true",
        help="Delete the cached SeriouslyFish species-name index before searching.",
    )
    return p


def main() -> int:
    args = parser_v24().parse_args()
    user_query = args.query

    if not user_query:
        try:
            user_query = input("Fish common or scientific name: ").strip()
        except EOFError:
            user_query = ""

    if not user_query:
        print("No fish name supplied.", file=sys.stderr)
        return 2

    mode, common_query, scientific_query = parse_search_term(user_query)
    fetcher = Fetcher()

    cache_path = _sf_index_cache_path(args.library)
    if args.rebuild_sf_index and cache_path.exists():
        cache_path.unlink()

    try:
        print(f"Looking up: {user_query}")
        print(f"Search mode: {mode}")

        if mode == "auto":
            print("Input could be a common or scientific name; testing both.")
            resolved_mode, sf_match, fb_match = resolve_auto_query(
                common_query,
                fetcher,
                args.library,
                assume_best=args.best_match,
            )
            print(f"Resolved as: {resolved_mode} name")

            if resolved_mode == "scientific":
                record_label = (
                    (sf_match or fb_match)["data"].get("common_name", scientific_query)
                    if (sf_match or fb_match)
                    else scientific_query
                )
            else:
                record_label = common_query

        elif mode == "mixed":
            print(f"Common name: {common_query}")
            print(f"Scientific hint: {scientific_query}")
            sf_match, fb_match = resolve_scientific_query(
                scientific_query,
                fetcher,
                args.library,
                common_alias=common_query,
            )
            record_label = common_query

        else:
            print(f"Common name: {common_query}")
            sf_match, fb_match = resolve_common_query(
                common_query,
                fetcher,
                args.library,
                assume_best=args.best_match,
            )
            record_label = common_query

        sf_data = sf_match["data"] if sf_match else None
        fb_data = fb_match["data"] if fb_match else None

        # Never merge two different scientific taxa.
        if sf_data and fb_data and not sources_same_taxon(sf_data, fb_data):
            print_compact_coverage(sf_data, fb_data)
            raise ImporterError(
                "The two sources resolved to different scientific names. "
                "Re-run with the scientific name you want."
            )

        print_compact_coverage(sf_data, fb_data)

        if not sf_data and not fb_data:
            raise ImporterError(
                "No matching species profile was found on either site."
            )

        related_fb = ""
        fb_status = "found" if fb_data else "not_found"

        record = merge_sources_compact(
            record_label,
            sf_data,
            fb_data,
            fishbase_status=fb_status,
            fishbase_related_name=related_fb,
        )

        # Search text is useful as an alias when it is not the canonical name.
        if mode in {"common", "mixed"}:
            alias_value = common_query
        elif mode == "auto" and resolved_mode == "common":
            alias_value = common_query
        else:
            alias_value = ""

        if (
            alias_value
            and norm_name(alias_value) != norm_name(record["common_name"])
            and alias_value not in record["aliases"]
        ):
            record["aliases"].append(alias_value)

        for alias in args.alias:
            alias = alias.strip()
            if alias and alias not in record["aliases"]:
                record["aliases"].append(alias)

        validate_species_basic(record)

        print("\nCanonical library record:")
        print(json.dumps(record, indent=2, ensure_ascii=False))

        library = load_library(args.library)
        existing_index, matched_by = find_existing_index_v22(
            library["species"],
            record,
        )

        if existing_index is None:
            action = "ADD"
            final_record = record
        else:
            action = f"UPDATE (matched by {matched_by})"
            final_record = update_existing_v22(
                library["species"][existing_index],
                record,
            )

        print(f"\nPlanned action: {action}")
        print(f"Library: {args.library}")

        if args.dry_run:
            print("Dry run complete. No files changed.")
            return 0

        if not args.yes and not yes_no("Write this record to the fish library?"):
            print("Cancelled. No files changed.")
            return 0

        new_library = deepcopy(library)
        if existing_index is None:
            new_library["species"].append(final_record)
        else:
            new_library["species"][existing_index] = final_record

        if args.sort:
            new_library["species"].sort(
                key=lambda item: norm_name(str(item.get("common_name", "")))
            )

        new_library["updated"] = date.today().isoformat()

        schema_path = args.schema
        if schema_path is None:
            candidate = args.library.parent / "schema.json"
            schema_path = candidate if candidate.exists() else None

        for item in new_library["species"]:
            if isinstance(item, dict):
                validate_species_basic(item)

        validate_schema(new_library, schema_path)

        backup = backup_file(args.library)
        atomic_write(args.library, new_library)

        print(f"\nSaved: {args.library}")
        if backup:
            print(f"Backup: {backup}")
        print(f"Species in library: {len(new_library['species'])}")
        return 0

    except ImporterError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Home Assistant wrapper
# ---------------------------------------------------------------------------

def lookup_species_for_ha(query: str, cache_anchor: str) -> dict[str, Any]:
    """Resolve one fish query and return a canonical library record."""
    user_query = norm_space(query)
    if not user_query:
        raise ImporterError("Enter a fish common name or scientific name.")

    mode, common_query, scientific_query = parse_search_term(user_query)
    fetcher = Fetcher()
    library_path = Path(cache_anchor)
    resolved_mode = mode

    if mode == "auto":
        resolved_mode, sf_match, fb_match = resolve_auto_query(
            common_query, fetcher, library_path, assume_best=True
        )
        record_label = (
            (sf_match or fb_match)["data"].get("common_name", scientific_query)
            if resolved_mode == "scientific" and (sf_match or fb_match)
            else common_query
        )
    elif mode == "mixed":
        sf_match, fb_match = resolve_scientific_query(
            scientific_query, fetcher, library_path, common_alias=common_query
        )
        resolved_mode = "scientific"
        record_label = common_query
    else:
        sf_match, fb_match = resolve_common_query(
            common_query, fetcher, library_path, assume_best=True
        )
        resolved_mode = "common"
        record_label = common_query

    sf_data = sf_match["data"] if sf_match else None
    fb_data = fb_match["data"] if fb_match else None

    if sf_data and fb_data and not sources_same_taxon(sf_data, fb_data):
        raise ImporterError(
            "SeriouslyFish and FishBase resolved to different scientific names. "
            "Search using the scientific name you want."
        )
    if not sf_data and not fb_data:
        raise ImporterError("No matching species profile was found on either website.")

    record = merge_sources_compact(
        record_label,
        sf_data,
        fb_data,
        fishbase_status="found" if fb_data else "not_found",
    )

    # Keep the exact user's common-name wording as a useful alias when relevant.
    if resolved_mode == "common":
        alias = common_query.strip()
        if alias and norm_name(alias) != norm_name(str(record.get("common_name", ""))):
            aliases = record.setdefault("aliases", [])
            if alias not in aliases:
                aliases.append(alias)

    return {
        "query": user_query,
        "resolved_mode": resolved_mode,
        "record": record,
        "source_status": {
            "seriouslyfish": "found" if sf_data else "not_found",
            "fishbase": "found" if fb_data else "not_found",
        },
    }
