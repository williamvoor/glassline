"""Headless render of compiled v0.9 messages with `@a2ui/lit` (validator layer 2).

Serves the built review page (`review/dist`, `render.html`) from 127.0.0.1 and drives a local
headless Chromium through Playwright. Nothing leaves the machine. Build the page first:
`cd review && npm ci && npm run build`.
"""

from __future__ import annotations

import functools
import threading
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .seeds import ROOT

DIST = ROOT / "review" / "dist"
WIDTHS = (375, 1280)  # a phone and a desktop
HEIGHT = 900


class RenderError(Exception):
    pass


@dataclass
class RenderReport:
    errors: list[str] = field(default_factory=list)  # renderer exceptions and console errors
    empty: dict[int, list[str]] = field(default_factory=dict)  # width -> container tags with nothing visible
    overflow: dict[int, int] = field(default_factory=dict)  # width -> px past the right edge

    def reasons(self) -> list[str]:
        out = list(self.errors)
        for w, tags in self.empty.items():
            if tags:
                out.append(f"empty container at {w}px: {', '.join(sorted(set(tags)))}")
        for w, px in self.overflow.items():
            if px > 0:
                out.append(f"overflow at {w}px: {px}px")
        return out


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # noqa: D102 - keep test output clean
        pass


class Renderer:
    """Context manager: one browser and one page, reused for every check."""

    def __init__(self, dist: Path = DIST, widths: tuple[int, ...] = WIDTHS):
        if not (dist / "render.html").exists():
            raise RenderError(f"{dist / 'render.html'} is missing; build review/ (npm run build)")
        self.dist, self.widths = dist, widths

    def __enter__(self) -> "Renderer":
        from playwright.sync_api import sync_playwright

        handler = functools.partial(_Quiet, directory=str(self.dist))
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        self._page = self._browser.new_page(viewport={"width": self.widths[0], "height": HEIGHT})
        self._console: list[str] = []
        self._page.on("console", lambda m: self._console.append(m.text) if m.type == "error" else None)
        self._page.on("pageerror", lambda e: self._console.append(f"page error: {e}"))
        # Any request off this machine is refused, so a surface's image URL can't reach the network.
        host = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(host) else route.abort())
        self._page.goto(f"{host}/render.html")
        self._page.wait_for_function("window.a2uiReady === true")
        return self

    def __exit__(self, *exc: Any) -> None:
        self._browser.close()
        self._pw.stop()
        self._server.shutdown()

    def check(self, messages: list[dict[str, Any]], screenshot: Path | None = None) -> RenderReport:
        """Renders at each width. `screenshot` (`x.png`) saves `x.<width>.png` per width."""
        report = RenderReport()
        for w in self.widths:
            self._page.set_viewport_size({"width": w, "height": HEIGHT})
            self._console.clear()
            out = self._page.evaluate("m => window.a2uiRender(m)", messages)
            # Images that couldn't load are expected here (no network); anything else is an error.
            errs = [c for c in self._console if "Failed to load resource" not in c and "ERR_FAILED" not in c]
            if out["error"]:
                errs.insert(0, out["error"])
            report.errors += [f"{w}px: {e}" for e in errs]
            report.empty[w] = out["empty"]
            report.overflow[w] = out["overflow"]
            if screenshot is not None:
                self._page.screenshot(path=str(screenshot.with_suffix(f".{w}.png")), full_page=True)
        return report
