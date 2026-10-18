"""Golden Downloader: retrieve directly downloadable files and publicly exposed document previews.

This intentionally uses only files/pages exposed without logging in or bypassing access controls.
"""
import html
import os
import re
import tempfile
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from PIL import Image
from io import BytesIO

MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_PAGES = 60
ALLOWED_PAGE_HOSTS = {"scribd.com", "www.scribd.com", "studocu.com", "www.studocu.com", "pdfcoffee.com", "www.pdfcoffee.com"}
ALLOWED_ASSET_SUFFIXES = ("scribdassets.com", "studocu.com", "studocu.net", "pdfcoffee.com")

class _Assets(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images = []
        self.pdfs = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for key in ("src", "data-src", "data-original", "href"):
            val = a.get(key)
            if not val:
                continue
            val = html.unescape(val.strip())
            if re.search(r"\.pdf(?:[?#]|$)", val, re.I):
                self.pdfs.append(val)
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

def _request(url, referer=None, limit=MAX_HTML_BYTES):
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=18) as response:
        final_url = response.geturl()
        if urllib.parse.urlparse(final_url).scheme != "https":
            raise ValueError("Only HTTPS downloads are accepted.")
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("The source file is too large.")
        return final_url, response.headers.get("Content-Type", ""), data

def _safe_name(value, default="document"):
    value = re.sub(r"[^\w. -]+", "_", value or "", flags=re.UNICODE).strip(" ._")
    return (value[:90] or default)

def download_public_document(url):
    """Return a local file path for a public direct file or publicly exposed preview, else raise."""
    parsed = urllib.parse.urlparse((url or "").strip())
    if parsed.scheme != "https" or _host(url) not in ALLOWED_PAGE_HOSTS:
        raise ValueError("Please send an HTTPS link from Studocu, Scribd, or PDFCoffee.")
    final_url, content_type, body = _request(url)
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
        # Prefer a directly linked PDF when the public page exposes one.
        for raw in parser.pdfs:
            candidate = urllib.parse.urljoin(final_url, raw)
            if _allowed_asset(candidate) or _host(candidate) in ALLOWED_PAGE_HOSTS:
                try:
                    _, ctype, data = _request(candidate, referer=final_url, limit=MAX_FILE_BYTES)
                    if "application/pdf" in ctype.lower() or data.startswith(b"%PDF-"):
                        out = os.path.join(tempdir, "document.pdf")
                        with open(out, "wb") as f:
                            f.write(data)
                        return out
                except Exception:
                    continue

        # Publicly rendered page images only; no login, token guessing, or hidden endpoint calls.
        candidates = []
        for raw in parser.images:
            candidate = urllib.parse.urljoin(final_url, raw)
            if _allowed_asset(candidate) and candidate not in candidates:
                candidates.append(candidate)
        images = []
        for candidate in candidates[:MAX_PAGES]:
            try:
                _, ctype, data = _request(candidate, referer=final_url, limit=8 * 1024 * 1024)
                if not ctype.lower().startswith("image/"):
                    continue
                im = Image.open(BytesIO(data)).convert("RGB")
                if im.width >= 250 and im.height >= 250:
                    images.append(im)
            except Exception:
                continue
        if images:
            out = os.path.join(tempdir, _safe_name("golden_document") + ".pdf")
            images[0].save(out, "PDF", save_all=True, append_images=images[1:])
            return out
        raise ValueError("No public downloadable file or preview pages were exposed by this link. If the site requires access, open/download it on the source site and send the file here.")
    except Exception:
        # Keep successful output available to the caller; clean failed temporary work.
        import shutil
        shutil.rmtree(tempdir, ignore_errors=True)
        raise
