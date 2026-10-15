"""Authenticated search and download against Copernicus Data Space.

Verified live facts (Phase 0 + Phase 2, see DECISIONS.md D1/D7):
*   Anonymous search works against
    ``{odata_base}/Products`` with OData filter syntax::
        Collection/Name eq 'SENTINEL-1'
        OData.CSC.Intersects(area=geography'SRID=4326;POLYGON((lon lat, ...))')
        ContentDate/Start gt <ISO> / ContentDate/Start lt <ISO>
*   ``$expand=Attributes`` returns product attributes including
    ``relativeOrbitNumber``, ``orbitDirection``, ``sliceNumber``,
    ``productType``, ``platformSerialIdentifier`` (used for same-track
    pairing, never filename guessing).
*   Sentinel-2: L2A products carry ``productType == 'S2MSI2A'`` and a
    ``cloudCover`` attribute (verified on live products).
*   Downloads (``Products({Id})/$value``) require a Bearer token from
    ``auth_token_url``; UNTESTED until credentials are present in .env
    (KNOWN_ISSUES K2).
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ..config import cdse_credentials

logger = logging.getLogger(__name__)

USER_AGENT = "FloodSight/0.1 (hackathon research prototype)"


class CDSEError(Exception):
    """Copernicus Data Space request failed."""


class CDSEAuthError(CDSEError):
    """Authentication failed or credentials are missing."""


def _bbox_polygon_wkt(bbox: tuple[float, float, float, float]) -> str:
    """WKT polygon for the bbox in (lon_min, lat_min, lon_max, lat_max) order.

    Longitude first everywhere in FloodSight; the OData Intersects function
    expects 'lon lat' pairs in the WKT (verified against the live API).
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    coords = (
        f"{lon_min} {lat_min}, {lon_max} {lat_min}, "
        f"{lon_max} {lat_max}, {lon_min} {lat_max}, {lon_min} {lat_min}"
    )
    return f"POLYGON(({coords}))"


def _iso_z(d: date, end_of_day: bool = False) -> str:
    stamp = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc) if end_of_day \
        else datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _retry_get(session: requests.Session, url: str, params: dict, cfg: dict[str, Any]) -> dict:
    """GET with config-driven retries and backoff; returns parsed JSON."""
    attempts = int(cfg.get("max_retries", 4))
    backoff = float(cfg.get("retry_backoff_s", 2.0))
    timeout = int(cfg.get("request_timeout_s", 60))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise CDSEError(f"transient HTTP {resp.status_code} from catalogue")
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, CDSEError) as exc:
            last_error = exc
            if attempt < attempts:
                wait = backoff * (2 ** (attempt - 1))
                logger.warning("CDSE request failed (attempt %s/%s): %s; retrying in %.1fs",
                               attempt, attempts, exc, wait)
                time.sleep(wait)
    raise CDSEError(f"CDSE request failed after {attempts} attempts: {last_error}") from last_error


def search_products(
    collection: str,
    bbox: tuple[float, float, float, float],
    date_start: date,
    date_end: date,
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Search products in a collection intersecting the bbox and date window.

    Returns the raw OData product dicts (Id, Name, ContentDate, GeoFootprint,
    Attributes, ...). Anonymous; verified working. Pagination follows
    ``@odata.nextLink`` up to a safety cap so a bad query cannot loop forever.
    """
    cdse_cfg = cfg["acquisition"]["cdse"]
    base = cdse_cfg["odata_base"].rstrip("/")
    wkt = _bbox_polygon_wkt(bbox)
    odata_filter = (
        f"Collection/Name eq '{collection}' and "
        f"OData.CSC.Intersects(area=geography'SRID=4326;{wkt}') and "
        f"ContentDate/Start gt {_iso_z(date_start)} and "
        f"ContentDate/Start lt {_iso_z(date_end, end_of_day=True)}"
    )
    params = {"$filter": odata_filter, "$top": str(cdse_cfg.get("page_size", 1000)),
              "$expand": "Attributes"}
    products: list[dict[str, Any]] = []
    url: str | None = base + "/Products"
    max_pages = 20
    page = 0
    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        while url is not None:
            if page >= max_pages:
                raise CDSEError(
                    f"Search exceeded {max_pages} pages; narrow the area or date window"
                )
            # nextLink already carries its own query string
            payload = _retry_get(session, url, params if page == 0 else {}, cdse_cfg)
            products.extend(payload.get("value", []))
            url = payload.get("@odata.nextLink")
            page += 1
    logger.info("CDSE search %s bbox=%s %s..%s -> %s products",
                collection, bbox, date_start, date_end, len(products))
    return products


def attribute_map(product: dict[str, Any]) -> dict[str, Any]:
    """Product attributes as a plain {name: value} dict."""
    return {a["Name"]: a["Value"] for a in product.get("Attributes", [])}


def get_auth_token(cfg: dict[str, Any]) -> str:
    """Obtain a CDSE access token with the credentials from .env.

    Raises CDSEAuthError with a clear message when credentials are missing
    or rejected. Token endpoint verified reachable; full flow is UNTESTED
    until credentials exist (KNOWN_ISSUES K2).
    """
    username, password = cdse_credentials()
    if not username or not password:
        raise CDSEAuthError(
            "Copernicus Data Space credentials are missing. Put CDSE_USERNAME "
            "and CDSE_PASSWORD in .env (see .env.example). Downloads require "
            "them; search works anonymously."
        )
    cdse_cfg = cfg["acquisition"]["cdse"]
    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        resp = session.post(
            cdse_cfg["auth_token_url"],
            data={
                "client_id": "cdse-public",
                "username": username,
                "password": password,
                "grant_type": "password",
            },
            timeout=int(cdse_cfg.get("request_timeout_s", 60)),
        )
    if resp.status_code != 200:
        # Surface only Keycloak's safe diagnostic fields; never echo input
        # or tokens. "Invalid user credentials" also appears when the
        # account has 2FA enabled and no totp was supplied (CDSE docs
        # show the 2FA variant of this same request).
        detail = ""
        try:
            body = resp.json()
            detail = f" ({body.get('error')}: {body.get('error_description')})"
        except ValueError:
            pass
        raise CDSEAuthError(
            f"CDSE authentication failed (HTTP {resp.status_code}){detail}. "
            "Check CDSE_USERNAME/CDSE_PASSWORD in .env; if the account has "
            "two-factor authentication enabled, the password grant also "
            "needs a totp code (CDSE docs), or use an account without 2FA."
        )
    token = resp.json().get("access_token")
    if not token:
        raise CDSEAuthError("CDSE authentication returned no access_token")
    return token


def download_product(
    product: dict[str, Any],
    dest_dir: Path,
    cfg: dict[str, Any],
    token: str | None = None,
) -> Path:
    """Download one product file to dest_dir, streaming to disk.

    Verified live (2026-10-08): ``Products({Id})/$value`` on the catalogue
    host answers 301 to ``download.dataspace.copernicus.eu``, which accepts
    the same Bearer token directly (requests would drop the Authorization
    header across hosts, so the redirect is followed manually). The server
    does not advertise Range support (Accept-Ranges absent), so retries
    restart the file; a 206 response would still be appended correctly if
    Range support appears later.
    """
    if token is None:
        token = get_auth_token(cfg)
    cdse_cfg = cfg["acquisition"]["cdse"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{product['Name']}.zip"
    expected = product.get("ContentLength")
    if dest.exists():
        size = dest.stat().st_size
        # A partial download must never pass as complete (verified:
        # interrupted runs left 1 MB-208 MB stubs for 1.73 GB products,
        # which the earlier existence check silently accepted)
        if expected is None or size == int(expected):
            logger.info("Product already downloaded: %s (%s bytes)",
                        dest.name, size)
            return dest
        logger.warning(
            "Partial download found: %s (%s of %s bytes); restarting",
            dest.name, size, expected)
        dest.unlink()
    base = cdse_cfg["odata_base"].rstrip("/")
    url = f"{base}/Products({product['Id']})/$value"
    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}

    attempts = int(cdse_cfg.get("max_retries", 4))
    backoff = float(cdse_cfg.get("retry_backoff_s", 2.0))
    timeout = int(cdse_cfg.get("request_timeout_s", 60))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with requests.Session() as session:
                resp = session.get(url, headers=headers, allow_redirects=False,
                                   timeout=timeout)
                if resp.status_code in (301, 302, 303, 307, 308):
                    # follow manually so the Authorization header survives
                    # the cross-host redirect (verified: the download host
                    # accepts the same Bearer token)
                    url = resp.headers["Location"]
                resp.close()
                resp = session.get(url, headers=headers, stream=True,
                                   timeout=timeout)
                if resp.status_code == 206:
                    mode = "ab"  # server honored a range request
                elif resp.status_code == 200:
                    mode = "wb"  # full body; no resume support (verified)
                else:
                    raise CDSEError(
                        f"Download failed: HTTP {resp.status_code} for {product['Name']}"
                    )
                with open(dest, mode) as fh:
                    fh.writelines(resp.iter_content(chunk_size=1 << 20))
            logger.info("Downloaded %s (%s bytes)", dest.name, dest.stat().st_size)
            return dest
        except (requests.RequestException, CDSEError) as exc:
            last_error = exc
            if dest.exists():
                # restart cleanly on retry; Range is not supported
                dest.unlink()
            if attempt < attempts:
                wait = backoff * (2 ** (attempt - 1))
                logger.warning("Download attempt %s/%s failed: %s; retrying in %.1fs",
                               attempt, attempts, exc, wait)
                time.sleep(wait)
    raise CDSEError(
        f"Download failed after {attempts} attempts for {product['Name']}: {last_error}"
    ) from last_error
