from __future__ import annotations

import re
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

from config import Config


def normalize_capture_url(url: str) -> str:
    raw_url = str(url or "").strip()
    if not raw_url:
        raise ValueError("URL is required")
    default_scheme = "http" if _looks_like_local_url(raw_url) else "https"
    if raw_url.startswith("//"):
        candidate = f"https:{raw_url}"
    elif raw_url.lower().startswith(("http://", "https://")):
        candidate = raw_url
    elif "://" in raw_url:
        raise ValueError("URL must use http:// or https://")
    elif re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw_url):
        maybe_host = raw_url.split(":", 1)[0]
        if "." not in maybe_host and maybe_host.lower() != "localhost":
            raise ValueError("URL must use http:// or https://")
        candidate = f"{default_scheme}://{raw_url}"
    else:
        candidate = f"{default_scheme}://{raw_url}"

    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must use http:// or https://")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def _looks_like_local_url(raw_url: str) -> bool:
    host = raw_url.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].strip("[]")
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    if ":" in host:
        host = host.split(":", 1)[0].strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        ip = ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


class PersonaCapture:
    def capture_url(self, url: str) -> dict:
        url = normalize_capture_url(url)
        return self._capture_with_playwright(url)

    def _capture_with_playwright(self, url: str) -> dict:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

        timeout = int(Config.PERSONA_PLAYWRIGHT_TIMEOUT_MS)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                response = None
                navigation_timed_out = False
                try:
                    response = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                except PlaywrightTimeoutError:
                    navigation_timed_out = True
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout, 3000))
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(500)
                screenshot = page.screenshot(full_page=True)
                return {
                    "url": url,
                    "status_code": response.status if response else None,
                    "content_type": response.headers.get("content-type") if response else None,
                    "title": page.title(),
                    "screenshot_base64": __import__("base64").b64encode(screenshot).decode("ascii"),
                    "screenshot_bytes": len(screenshot),
                    "capture_backend": "playwright",
                    "navigation_timed_out": navigation_timed_out,
                }
            finally:
                browser.close()

persona_capture = PersonaCapture()
