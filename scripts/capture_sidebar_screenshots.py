#!/usr/bin/env python3
"""Capture screenshots for every link in the shared sidebar."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests
import websocket
from PIL import Image


CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


@dataclass
class SidebarLink:
    section: str
    label: str
    href: str


class SidebarParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[SidebarLink] = []
        self.section = "Sidebar"
        self._capture_anchor: dict[str, str] | None = None
        self._capture_label = False
        self._capture_summary = False
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: v or "" for k, v in attrs}
        classes = attr.get("class", "")
        if tag == "a" and "sidebar-item" in classes:
            self._capture_anchor = attr
            self._text = []
        elif tag == "div" and "sidebar-section-label" in classes:
            self._capture_label = True
            self._text = []
        elif tag == "summary" and "sidebar-section-label" in classes:
            self._capture_summary = True
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_anchor is not None:
            label = clean_label(" ".join(self._text))
            href = self._capture_anchor.get("href", "")
            if href:
                self.links.append(SidebarLink(self.section, label, href))
            self._capture_anchor = None
            self._text = []
        elif tag == "div" and self._capture_label:
            label = clean_label(" ".join(self._text))
            if label:
                self.section = label
            self._capture_label = False
            self._text = []
        elif tag == "summary" and self._capture_summary:
            label = clean_label(" ".join(self._text))
            if label:
                self.section = label
            self._capture_summary = False
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._capture_anchor is not None or self._capture_label or self._capture_summary:
            self._text.append(data)


def clean_label(value: str) -> str:
    value = re.sub(r"\{[%{].*?[%}]\}", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "page"


def parse_sidebar(path: Path) -> list[SidebarLink]:
    parser = SidebarParser()
    parser.feed(path.read_text())
    return parser.links


def run_chrome(args: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    return subprocess.run(
        [str(CHROME), *args],
        check=False,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class CdpClient:
    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=10)
        self.next_id = 0

    def close(self) -> None:
        self.ws.close()

    def send(self, method: str, params: dict | None = None, timeout: float = 10) -> dict:
        self.next_id += 1
        message_id = self.next_id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            self.ws.settimeout(max(0.1, end - time.time()))
            data = json.loads(self.ws.recv())
            if data.get("id") == message_id:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result", {})
        raise TimeoutError(method)


def wait_for_debugger(port: int, timeout: float = 10) -> list[dict]:
    end = time.time() + timeout
    last = ""
    while time.time() < end:
        try:
            response = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=1)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - this is diagnostic glue.
            last = str(exc)
            time.sleep(0.2)
    raise TimeoutError(last or "Chrome debugger did not start")


def capture(link: SidebarLink, url: str, image_path: Path, width: int, height: int, wait_ms: int) -> tuple[bool, str]:
    profile = tempfile.mkdtemp(prefix="flex-sidebar-chrome-")
    port = free_port()
    proc = subprocess.Popen(
        [
            str(CHROME),
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            f"--window-size={width},{height}",
            "about:blank",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    client: CdpClient | None = None
    try:
        pages = wait_for_debugger(port)
        page = next((p for p in pages if p.get("type") == "page"), pages[0])
        client = CdpClient(page["webSocketDebuggerUrl"])
        client.send("Page.enable")
        client.send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )
        client.send("Page.navigate", {"url": url})
        time.sleep(wait_ms / 1000)
        shot = client.send(
            "Page.captureScreenshot",
            {
                "format": "png",
                "fromSurface": True,
                "captureBeyondViewport": False,
            },
            timeout=15,
        )
        image_path.write_bytes(base64.b64decode(shot["data"]))
        ok = image_path.exists() and image_path.stat().st_size > 0
        return ok, ""
    except Exception as exc:  # noqa: BLE001 - keep going across pages.
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def build_document(base_url: str, output_dir: Path, rows: list[tuple[int, SidebarLink, str, bool, str]]) -> Path:
    html_path = output_dir / "sidebar_screenshots.html"
    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts = [
        "<!doctype html>",
        "<html>",
        "<head>",
        '<meta charset="utf-8">',
        "<title>FLEX Sidebar UI Screenshots</title>",
        "<style>",
        "body{margin:0;background:#111827;color:#e5e7eb;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;}",
        "header{padding:28px 32px;border-bottom:1px solid #374151;background:#0b1220;position:sticky;top:0;z-index:1;}",
        "h1{font-size:24px;margin:0 0 8px;}p{margin:0;color:#9ca3af;}main{padding:24px 32px;}",
        "section{break-inside:avoid;margin:0 0 34px;padding-bottom:28px;border-bottom:1px solid #374151;}",
        "h2{font-size:18px;margin:0 0 6px;color:#f9fafb;} .meta{font-size:12px;color:#9ca3af;margin-bottom:14px;}",
        "img{max-width:100%;height:auto;border:1px solid #374151;background:#050505;display:block;}",
        ".failed{padding:18px;border:1px solid #7f1d1d;background:#2f1111;color:#fecaca;white-space:pre-wrap;}",
        "@media print{header{position:static}body{background:#fff;color:#111827}p,.meta{color:#4b5563}section{page-break-inside:avoid;border-bottom:1px solid #d1d5db}img{border-color:#d1d5db}}",
        "</style>",
        "</head>",
        "<body>",
        "<header>",
        "<h1>FLEX Sidebar UI Screenshots</h1>",
        f"<p>Generated {html.escape(generated)} from {html.escape(base_url)}</p>",
        "</header>",
        "<main>",
    ]
    for number, link, filename, ok, note in rows:
        title = f"{number:02d}. {link.section} - {link.label}"
        url = urljoin(base_url, link.href)
        parts.extend(
            [
                "<section>",
                f"<h2>{html.escape(title)}</h2>",
                f'<div class="meta">{html.escape(url)}</div>',
            ]
        )
        if ok:
            parts.append(f'<img src="{html.escape(filename)}" alt="{html.escape(title)}">')
        else:
            parts.append(f'<div class="failed">Capture failed\n{html.escape(note[-2000:])}</div>')
        parts.append("</section>")
    parts.extend(["</main>", "</body>", "</html>"])
    html_path.write_text("\n".join(parts))
    return html_path


def print_pdf(html_path: Path, output_dir: Path) -> Path | None:
    pdf_path = output_dir / "sidebar_screenshots.pdf"
    profile = tempfile.mkdtemp(prefix="flex-sidebar-print-")
    try:
        result = run_chrome(
        [
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            f"--user-data-dir={profile}",
            f"--print-to-pdf={pdf_path}",
            html_path.resolve().as_uri(),
        ],
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode == 0 and pdf_path.exists() and pdf_path.stat().st_size > 0:
        return pdf_path
    return None


def build_image_pdf(output_dir: Path, rows: list[tuple[int, SidebarLink, str, bool, str]]) -> Path | None:
    pdf_path = output_dir / "sidebar_screenshots.pdf"
    images: list[Image.Image] = []
    try:
        for _, _, filename, ok, _ in rows:
            if not ok:
                continue
            image = Image.open(output_dir / filename).convert("RGB")
            images.append(image)
        if not images:
            return None
        first, rest = images[0], images[1:]
        first.save(pdf_path, save_all=True, append_images=rest, resolution=110)
        return pdf_path if pdf_path.exists() and pdf_path.stat().st_size > 0 else None
    finally:
        for image in images:
            image.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5002")
    parser.add_argument("--sidebar", default="templates/partials/sidebar.html")
    parser.add_argument("--out-root", default="docs/ui-sidebar-screenshots")
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1400)
    parser.add_argument("--wait-ms", type=int, default=5000)
    args = parser.parse_args()

    if not CHROME.exists():
        print(f"Chrome not found: {CHROME}", file=sys.stderr)
        return 2

    links = parse_sidebar(Path(args.sidebar))
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.out_root) / stamp
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[int, SidebarLink, str, bool, str]] = []
    for index, link in enumerate(links, 1):
        filename = f"{index:02d}-{slugify(link.section)}-{slugify(link.label)}.png"
        url = urljoin(args.base_url, link.href)
        image_path = output_dir / filename
        print(f"[{index:02d}/{len(links):02d}] {link.label} -> {url}", flush=True)
        ok, note = capture(link, url, image_path, args.width, args.height, args.wait_ms)
        rows.append((index, link, filename, ok, note))

    html_path = build_document(args.base_url, output_dir, rows)
    pdf_path = print_pdf(html_path, output_dir)
    if not pdf_path:
        pdf_path = build_image_pdf(output_dir, rows)

    ok_count = sum(1 for _, _, _, ok, _ in rows if ok)
    print(f"\nCaptured {ok_count}/{len(rows)} screenshots")
    print(f"HTML: {html_path}")
    if pdf_path:
        print(f"PDF:  {pdf_path}")
    else:
        print("PDF:  not generated")
    return 0 if ok_count == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
