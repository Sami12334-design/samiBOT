"""Golden Downloader: retrieve directly downloadable files and publicly exposed document previews.

This intentionally uses only files/pages exposed without logging in or bypassing access controls.
"""
import html
import ipaddress
import json
import os
import re
import shutil
import socket
import tempfile
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from PIL import Image
from io import BytesIO

MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_PAGES = 60
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000     # reject huge images before decoding them
MAX_PAGE_SIDE_PX = 2000           # downscale page images to this before embedding
PAGE_WIDTH_PT = 612               # width of generated PDF pages (US Letter, points)
MAX_PDF_CANDIDATES = 5
REQUEST_TIMEOUT = 18              # per-request socket timeout (seconds)
TOTAL_TIME_BUDGET = 90            # overall limit for one download_public_document() call
ALLOWED_PAGE_HOSTS = {"scribd.com", "www.scribd.com", "studocu.com", "www.studocu.com", "pdfcoffee.com", "www.pdfcoffee.com"}
ALLOWED_ASSET_SUFFIXES = ("scribdassets.com", "studocu.com", "studocu.net", "pdfcoffee.com")

_STRONG_PDF_TAGS = {"embed", "iframe", "object", "source"}


class _OutOfTime(Exception):
    """The overall time budget for this download was used up."""


class _Assets(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images = []
        # (url, strong): strong = the page itself embeds/declares the PDF
        # (embed/iframe/object, <link type=pdf>, <a download>). Plain <a href>
        # links are weak: they may point to *other* documents (related items).
        self.pdf_links = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        keys = ["src", "data-src", "data-original", "href"] + (["data"] if tag == "object" else [])
        for key in keys:
            val = a.get(key)
            if not val:
                continue
            val = html.unescape(val.strip())
            if re.search(r"\.pdf(?:[?#]|$)", val, re.I):
                strong = (
                    tag in _STRONG_PDF_TAGS
                    or (tag == "link" and "pdf" in (a.get("type") or "").lower())
                    or (tag == "a" and "download" in a)
                )
                self.pdf_links.append((val, strong))
            if key != "href" and (tag == "img" or "image" in (a.get("type") or "").lower()):
                self.images.append(val)
        srcset = a.get("srcset", "")
        for item in srcset.split(","):
            val = item.strip().split(" ")[0]
            if val:
                self.images.append(val)


def _host(url):
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _allowed_asset(url):
    host = _host(url)
    return any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_ASSET_SUFFIXES)


def _allowed_source_host(url):
    host = _host(url)
    return host in ALLOWED_PAGE_HOSTS or any(host == d or host.endswith("." + d) for d in ALLOWED_PAGE_HOSTS) or _allowed_asset(url)


def _check_source_url(url):
    """Redirect/request guard for Studocu/Scribd/PDFCoffee traffic: HTTPS and known hosts only."""
    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError("Only HTTPS downloads are accepted.")
    if not _allowed_source_host(url):
        raise ValueError("The source redirected to an unsupported host.")


def _check_public_https(url):
    """Guard for configured third-party providers: HTTPS and public internet addresses only."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Only HTTPS downloads are accepted.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ValueError("Could not resolve the download host.") from exc
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError("Downloads from private or local addresses are not allowed.")


def _make_opener(check_url):
    """urllib opener that validates every redirect hop with ``check_url``."""
    class _Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            check_url(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    return urllib.request.build_opener(_Redirect)


def _remaining(deadline, cap=None):
    """Seconds left before ``deadline`` (optionally capped); raises when time is up."""
    if deadline is None:
        return cap
    left = deadline - time.monotonic()
    if left <= 0:
        raise _OutOfTime("The source took too long to respond.")
    return min(left, cap) if cap else left


def _request(url, referer=None, limit=MAX_HTML_BYTES, deadline=None, strict_hosts=True):
    check = _check_source_url if strict_hosts else _check_public_https
    check(url)
    timeout = _remaining(deadline, REQUEST_TIMEOUT)
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with _make_opener(check).open(req, timeout=timeout) as response:
        final_url = response.geturl()
        if urllib.parse.urlparse(final_url).scheme != "https":
            raise ValueError("Only HTTPS downloads are accepted.")
        chunks, total = [], 0
        while True:
            _remaining(deadline)  # a slow-drip server must not outlast the overall budget
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ValueError("The source file is too large.")
            chunks.append(chunk)
        return final_url, response.headers.get("Content-Type", ""), b"".join(chunks)


def _safe_name(value, default="document"):
    value = re.sub(r"[^\w. -]+", "_", value or "", flags=re.UNICODE).strip(" ._")
    return (value[:90] or default)


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _plausibly_same_document(page_url, pdf_url):
    """Weak PDF links are only trusted when their file name matches the page's own slug."""
    stem = _slug(Path(urllib.parse.urlparse(pdf_url).path).stem)
    page_path = urllib.parse.urlparse(page_url).path
    page_slug = _slug(page_path)
    last_segment = _slug(page_path.rstrip("/").rsplit("/", 1)[-1])
    if len(stem) >= 4 and stem in page_slug:
        return True
    return len(last_segment) >= 4 and last_segment in stem


def _save_temp_pdf(prefix, filename, data):
    """Write ``data`` into a fresh temp dir; the dir is removed again if writing fails."""
    tempdir = tempfile.mkdtemp(prefix=prefix)
    try:
        out = os.path.join(tempdir, filename)
        with open(out, "wb") as handle:
            handle.write(data)
        return out
    except Exception:
        shutil.rmtree(tempdir, ignore_errors=True)
        raise


def _prepare_page_image(data):
    """Validate, downscale and JPEG-encode one page image. Returns (jpeg, w, h) or None."""
    with Image.open(BytesIO(data)) as im:  # opening is lazy: size is known before decoding
        width, height = im.size
        if width < 250 or height < 250 or width * height > MAX_IMAGE_PIXELS:
            return None
        rgb = im.convert("RGB")
    rgb.thumbnail((MAX_PAGE_SIDE_PX, MAX_PAGE_SIDE_PX))
    buf = BytesIO()
    rgb.save(buf, "JPEG", quality=85, optimize=True)
    return buf.getvalue(), rgb.width, rgb.height


def _images_to_pdf(candidates, referer, tempdir, deadline):
    """Download page images one at a time and append them to a PDF.

    Only one decoded image is in memory at a time; the PDF keeps compressed JPEG data.
    """
    import pymupdf
    doc = pymupdf.open()
    total_bytes = 0
    try:
        for candidate in candidates[:MAX_PAGES]:
            try:
                _, ctype, data = _request(candidate, referer=referer, limit=MAX_IMAGE_BYTES, deadline=deadline)
                if not ctype.lower().startswith("image/"):
                    continue
                prepared = _prepare_page_image(data)
            except _OutOfTime:
                raise
            except Exception:
                continue
            if prepared is None:
                continue
            jpeg, width, height = prepared
            total_bytes += len(jpeg)
            if total_bytes > MAX_FILE_BYTES:
                break
            page = doc.new_page(width=PAGE_WIDTH_PT, height=PAGE_WIDTH_PT * height / width)
            page.insert_image(page.rect, stream=jpeg)
        if not doc.page_count:
            return None
        out = os.path.join(tempdir, _safe_name("golden_document") + ".pdf")
        doc.save(out, garbage=3, deflate=True)
        return out
    finally:
        doc.close()


def _download_public_document(url, deadline=None):
    """Return a local file path for a public direct file or publicly exposed preview, else raise."""
    parsed = urllib.parse.urlparse((url or "").strip())
    if parsed.scheme != "https" or _host(url) not in ALLOWED_PAGE_HOSTS:
        raise ValueError("Please send an HTTPS link from Studocu, Scribd, or PDFCoffee.")
    final_url, content_type, body = _request(url, deadline=deadline)
    if not any(_host(final_url) == d or _host(final_url).endswith("." + d) for d in ALLOWED_PAGE_HOSTS):
        # Source pages can redirect within the source domain only.
        if not _allowed_asset(final_url):
            raise ValueError("The source redirected to an unsupported host.")

    tempdir = tempfile.mkdtemp(prefix="golden_dl_")
    try:
        if "application/pdf" in content_type.lower() or body.startswith(b"%PDF-"):
            name = _safe_name(Path(urllib.parse.urlparse(final_url).path).stem, "document") + ".pdf"
            out = os.path.join(tempdir, name)
            with open(out, "wb") as f:
                f.write(body)
            return out

        if "text/html" not in content_type.lower() and not body.lstrip().startswith((b"<!DOCTYPE", b"<html", b"<?xml")):
            raise ValueError("This link did not return a downloadable PDF or document page.")

        page_html = body.decode("utf-8", errors="replace")
        parser = _Assets()
        parser.feed(page_html)

        # Prefer a PDF the page itself embeds/declares; plain links are only trusted
        # when they clearly belong to this document (never "related document" links).
        pdf_candidates = []
        for raw, strong in parser.pdf_links:
            candidate = urllib.parse.urljoin(final_url, raw)
            if not (_allowed_asset(candidate) or _host(candidate) in ALLOWED_PAGE_HOSTS):
                continue
            if (strong or _plausibly_same_document(final_url, candidate)) and candidate not in pdf_candidates:
                pdf_candidates.append(candidate)
        for candidate in pdf_candidates[:MAX_PDF_CANDIDATES]:
            try:
                _, ctype, data = _request(candidate, referer=final_url, limit=MAX_FILE_BYTES, deadline=deadline)
                if "application/pdf" in ctype.lower() or data.startswith(b"%PDF-"):
                    out = os.path.join(tempdir, "document.pdf")
                    with open(out, "wb") as f:
                        f.write(data)
                    return out
            except _OutOfTime:
                raise
            except Exception:
                continue

        # Publicly rendered page images only; no login, token guessing, or hidden endpoint calls.
        candidates = []
        for raw in parser.images:
            candidate = urllib.parse.urljoin(final_url, raw)
            if _allowed_asset(candidate) and candidate not in candidates:
                candidates.append(candidate)
        out = _images_to_pdf(candidates, final_url, tempdir, deadline)
        if out:
            return out
        raise ValueError("No public downloadable file or preview pages were exposed by this link. If the site requires access, open/download it on the source site and send the file here.")
    except Exception:
        # Keep successful output available to the caller; clean failed temporary work.
        shutil.rmtree(tempdir, ignore_errors=True)
        raise


def _configured_endpoints():
    return [
        item.strip()
        for item in os.environ.get("GOLDEN_DOWNLOADER_API_URLS", "").split(",")
        if item.strip()
    ]


def _download_via_configured_api(source_url, deadline=None):
    """Try an explicitly configured third-party API endpoint.

    Configure GOLDEN_DOWNLOADER_API_URLS as a comma-separated list of HTTPS
    endpoint templates, each containing {url}, for example:
    https://provider.example/api/download?url={url}
    An endpoint may return a PDF directly or JSON containing download_url/url.
    Provider endpoints must be documented/authorized by their operator.
    """
    endpoints = _configured_endpoints()
    errors = []
    for template in endpoints:
        try:
            if not template.startswith("https://") or "{url}" not in template:
                errors.append("invalid provider endpoint configuration")
                continue
            endpoint = template.replace("{url}", urllib.parse.quote(source_url, safe=""))
            final_url, content_type, body = _request(endpoint, limit=MAX_FILE_BYTES, deadline=deadline, strict_hosts=False)
            if "application/pdf" in content_type.lower() or body.startswith(b"%PDF-"):
                return _save_temp_pdf("golden_api_", "document.pdf", body)
            try:
                payload = json.loads(body.decode("utf-8", errors="replace"))
            except Exception:
                errors.append("provider returned neither PDF nor JSON")
                continue
            download_url = payload.get("download_url") or payload.get("url")
            if not isinstance(download_url, str) or not download_url.startswith("https://"):
                errors.append("provider returned no HTTPS download_url")
                continue
            _, file_type, file_body = _request(download_url, referer=final_url, limit=MAX_FILE_BYTES, deadline=deadline, strict_hosts=False)
            if "application/pdf" not in file_type.lower() and not file_body.startswith(b"%PDF-"):
                errors.append("provider link did not return a PDF")
                continue
            return _save_temp_pdf("golden_api_", _safe_name(payload.get("filename"), "document") + ".pdf", file_body)
        except _OutOfTime:
            raise
        except Exception as exc:
            errors.append(str(exc)[:160])
            continue
    raise ValueError("All configured external downloader providers failed. " + "; ".join(errors[-3:]))


def download_public_document(url):
    """Try public-source retrieval first, then configured external API providers.

    The whole call (both stages) is limited to TOTAL_TIME_BUDGET seconds.
    """
    deadline = time.monotonic() + TOTAL_TIME_BUDGET
    errors = []
    try:
        return _download_public_document(url, deadline=deadline)
    except Exception as exc:
        errors.append("public source: " + str(exc)[:200])
    if _configured_endpoints():  # skip the fallback (and its misleading error) when none is set up
        try:
            return _download_via_configured_api(url, deadline=deadline)
        except Exception as exc:
            errors.append("external API fallback: " + str(exc)[:220])
    raise ValueError(
        "Golden Downloader tried its available methods but could not retrieve this file. "
        + " | ".join(errors)
    )
