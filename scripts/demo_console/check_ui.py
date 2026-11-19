"""Offline browser smoke: intercept every request; never start the backend."""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .app import ACTIONS, HERE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable", default=os.getenv("SENTINELOPS_CHROME"))
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    if shutil.which("node"):
        os.environ.setdefault("PLAYWRIGHT_NODEJS_PATH", shutil.which("node"))
    from playwright.sync_api import expect, sync_playwright

    origin = "http://127.0.0.1:8765"
    state = {"enabled": False, "actions": [a.public() for a in ACTIONS], "busy": False,
             "checked_at": datetime.now(timezone.utc).isoformat(),
             "services": {name: {"ok": True, "mode": "healthy", "delay_ms": 0,
                                 "observed_at": datetime.now(timezone.utc).isoformat(), "error": None}
                          for name in ("payment", "inventory")},
             "deployment": {"configured": True, "configuration_error": None,
                            "state": "idle", "returncode": None, "log_tail": ""}}
    reads = []
    posts = []
    errors = []
    disconnected = False

    def route_request(route):
        request = route.request
        assert request.url.startswith(origin + "/"), "Unexpected external browser request"
        path = urlsplit(request.url).path
        assets = {"/": ("index.html", "text/html"),
                  "/assets/app.js": ("app.js", "text/javascript"),
                  "/assets/styles.css": ("styles.css", "text/css")}
        if path in assets:
            name, content_type = assets[path]
            route.fulfill(path=HERE / "static" / name, content_type=content_type)
        elif path == "/api/status":
            reads.append(path)
            if disconnected:
                route.abort("failed")
            else:
                route.fulfill(json=state)
        elif path.startswith("/api/actions/"):
            assert request.method == "POST"
            assert request.headers["origin"] == origin
            assert request.headers["x-console-action"] == "apply"
            action = next(a for a in ACTIONS if a.id == path.rsplit("/", 1)[1])
            posts.append(action.id)
            if action.mode:
                for service in action.services:
                    state["services"][service].update(action.payload())
            else:
                state["deployment"].update(state="running", log_tail="Offline fixture: script running")
            route.fulfill(status=200 if action.mode else 202, json={"message": "Offline fixture accepted"})
        else:
            route.fulfill(status=404)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser_executable, headless=True,
                                             args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1100}, service_workers="block")
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            page.route("**/*", route_request)
            page.clock.install()
            page.goto(origin)
            expect(page.locator(".card")).to_have_count(8)
            expect(page.locator("#mode")).to_contain_text("Bơm lỗi TẮT")
            assert all(button.is_disabled() for button in page.locator(".card button").all())
            assert posts == []

            state["enabled"] = True
            page.get_by_role("button", name="Làm mới").click()
            payment_button = page.get_by_role("button", name="Payment · lỗi 503", exact=True)
            expect(payment_button).to_be_enabled()
            payment_button.click()
            payment_card = page.locator(".card").filter(has=payment_button)
            expect(payment_card.locator(".state")).to_contain_text("mode=error")
            assert posts == ["payment-error"]

            recovery = page.get_by_role("button", name="KHÔI PHỤC", exact=True)
            expect(recovery).to_be_enabled()
            recovery.click()
            expect(payment_card.locator(".state")).to_contain_text("mode=healthy")
            assert posts == ["payment-error", "recover"]

            state["services"]["inventory"].update(ok=False, mode=None, delay_ms=None,
                                                 observed_at=None, error="Offline fixture: port-forward unavailable")
            before = len(reads)
            page.clock.fast_forward(5000)
            expect(page.locator(".card").filter(has=page.get_by_role("button", name="Inventory · lỗi 503", exact=True)).locator(".state")).to_contain_text("KHÔNG ĐỌC ĐƯỢC")
            assert len(reads) > before

            disconnected = True
            page.get_by_role("button", name="Làm mới").click()
            expect(page.locator("#mode")).to_have_text("MẤT KẾT NỐI CONSOLE")
            assert all(button.is_disabled() for button in page.locator(".card button").all())
            assert page.locator(".card.active").count() == 0

            disconnected = False
            page.get_by_role("button", name="Làm mới").click()
            deploy = page.get_by_role("button", name="DEPLOY IMAGE HỎNG", exact=True)
            expect(deploy).to_be_enabled()
            deploy.click()
            expect(page.locator("#deployment-status")).to_contain_text("Script đang chạy")
            assert all(button.is_disabled() for button in page.locator(".card button").all())
            assert posts == ["payment-error", "recover", "deploy-bad-image"]
            assert not errors, errors

            if args.screenshot:
                page.screenshot(path=str(args.screenshot), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            print("PASS: 8 cards, read-only default, fault/recovery clicks, 5s polling, service/backend failures, deploy lock, desktop/mobile; all requests intercepted, no backend or cluster calls.")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
