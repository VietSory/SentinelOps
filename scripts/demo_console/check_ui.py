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
    unavailable_oom = "The isolated OOM workload and reviewed runner are not configured."
    idle = {"configured": True, "configuration_error": None,
            "state": "idle", "returncode": None, "log_tail": ""}
    state = {"enabled": False, "actions": [a.public() for a in ACTIONS], "busy": False,
             "checked_at": datetime.now(timezone.utc).isoformat(),
             "services": {name: {"ok": True, "mode": "healthy", "delay_ms": 0,
                                 "observed_at": datetime.now(timezone.utc).isoformat(), "error": None}
                          for name in ("payment", "inventory")},
             "deployment": dict(idle),
             "oom": {**idle, "configured": False, "configuration_error": unavailable_oom},
             "terminal": {"entries": [], "logs": {"deployment": "", "oom": ""}}}
    assert len(state["actions"]) == 9 and any(a["id"] == "oom" for a in state["actions"])
    reads, posts, errors = [], [], []
    disconnected = False
    response_lost = False

    def journal(action, command, detail="HTTP 200 · latest read verified", result="verified"):
        state["terminal"]["entries"].append({
            "sequence": len(state["terminal"]["entries"]) + 1,
            "at": datetime.now(timezone.utc).isoformat(), "action_id": action.id,
            "command": command, "state": result, "detail": detail})

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
                for action in state["actions"]:
                    runner = state["oom"] if action["id"] == "oom" else state["deployment"] if action["id"] == "deploy-bad-image" else None
                    unavailable = [not state["services"][s]["ok"] for s in action["services"]]
                    target_unavailable = (all(unavailable) if action["id"] == "recover" else any(unavailable))
                    reason = ("Fault injection is off." if not state["enabled"] else
                              "Another scenario is running." if state["busy"] or any(state[r]["state"] == "running" for r in ("deployment", "oom")) else
                              runner["configuration_error"] if runner and not runner["configured"] else
                              "Service observation unavailable." if not runner and target_unavailable else None)
                    action.update(available=reason is None, unavailable_reason=reason)
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
                    journal(action, f'POST http://{service}-service:8000/internal/faults {action.payload()}')
            else:
                key = "oom" if action.id == "oom" else "deployment"
                state[key].update(state="running", log_tail="Offline fixture: script running")
                state["terminal"]["logs"][key] = "Offline fixture: script running"
                journal(action, f"bash /opt/demo/{key}.sh", "Process started; recovery unconfirmed", "running")
            if response_lost:
                route.abort("failed")
            else:
                route.fulfill(status=200 if action.mode else 202, json={"message": "Offline fixture accepted"})
        else:
            route.fulfill(status=404)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser_executable, headless=True,
                                             args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1100}, service_workers="block",
                                    color_scheme="dark", locale="vi-VN")
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            page.route("**/*", route_request)
            page.clock.install()
            page.goto(origin)
            expect(page.locator("html")).to_have_attribute("lang", "en")
            expect(page.locator("html")).to_have_css("color-scheme", "light")
            expect(page.locator("html")).to_have_css("background-color", "rgb(255, 255, 255)")
            expect(page.locator(".card")).to_have_count(9)
            expect(page.locator("#mode")).to_contain_text("Fault injection off")
            expect(page.get_by_role("heading", name="Commands and output", exact=True)).to_be_visible()
            expect(page.locator("#terminal-output")).to_contain_text("No commands have run")
            assert page.locator(".execution input, .execution textarea").count() == 0
            assert all(button.is_disabled() for button in page.locator(".card button").all())
            assert posts == []

            state["enabled"] = True
            page.get_by_role("button", name="Refresh", exact=True).click()
            oom_card = page.locator('.card[data-action-id="oom"]')
            oom_button = oom_card.get_by_role("button")
            expect(oom_button).to_be_disabled()
            expect(oom_card.locator(".match")).to_have_text(unavailable_oom)
            assert "payment:" not in oom_card.locator(".state").inner_text()
            payment_button = page.get_by_role("button", name="Payment · HTTP 503", exact=True)
            expect(payment_button).to_be_enabled()
            payment_button.click()
            payment_card = page.locator(".card").filter(has=payment_button)
            expect(payment_card.locator(".state")).to_contain_text("mode=error")
            expect(page.locator("#terminal-output")).to_contain_text("POST http://payment-service:8000/internal/faults")
            assert posts == ["payment-error"]

            recovery = page.get_by_role("button", name="Reset fault modes", exact=True)
            expect(recovery).to_be_enabled()
            recovery.click()
            expect(payment_card.locator(".state")).to_contain_text("mode=healthy")
            assert posts == ["payment-error", "recover"]

            state["services"]["inventory"].update(ok=False, mode=None, delay_ms=None,
                                                 observed_at=None, error="Offline fixture: port-forward unavailable")
            before = len(reads)
            page.clock.fast_forward(5000)
            inventory_button = page.get_by_role("button", name="Inventory · HTTP 503", exact=True)
            expect(page.locator(".card").filter(has=inventory_button).locator(".state")).to_contain_text("Unavailable")
            expect(inventory_button).to_be_disabled()
            # One reachable target can still be reset; UI honors server readiness.
            expect(recovery).to_be_enabled()
            assert len(reads) > before
            state["services"]["inventory"].update(ok=True, mode="healthy", delay_ms=0,
                                                 observed_at=datetime.now(timezone.utc).isoformat(), error=None)

            last_terminal = page.locator("#terminal-output").inner_text()
            disconnected = True
            page.get_by_role("button", name="Refresh", exact=True).click()
            expect(page.locator("#mode")).to_have_text("Console disconnected")
            expect(page.locator("#terminal-status")).to_contain_text("last observed snapshot")
            assert page.locator("#terminal-output").inner_text() == last_terminal
            assert all(button.is_disabled() for button in page.locator(".card button").all())
            assert page.locator(".card.active").count() == 0

            disconnected = False
            page.get_by_role("button", name="Refresh", exact=True).click()
            deploy = page.get_by_role("button", name="Deploy regression image", exact=True)
            expect(deploy).to_be_enabled()
            deploy.click()
            expect(page.locator("#deployment-status")).to_contain_text("the script is running")
            expect(page.locator("#terminal-output")).to_contain_text("bash /opt/demo/deployment.sh")
            assert all(button.is_disabled() for button in page.locator(".card button").all())
            assert posts == ["payment-error", "recover", "deploy-bad-image"]

            # Server text must stay inert, and a nonzero script exit must stay visible.
            payload = '<img src=x onerror="window.untrustedExecuted=true">'
            state["deployment"].update(state="exited", returncode=2)
            state["terminal"]["logs"]["deployment"] = "Offline fixture: failed\n" + payload
            page.get_by_role("button", name="Refresh", exact=True).click()
            expect(page.locator("#deployment-status")).to_contain_text("code 2")
            expect(page.locator("#terminal-output")).to_contain_text(payload)
            assert page.locator("#terminal-output img").count() == 0
            assert page.evaluate("window.untrustedExecuted") is None

            state["oom"].update(configured=True, configuration_error=None)
            page.get_by_role("button", name="Refresh", exact=True).click()
            expect(oom_button).to_be_enabled()
            oom_button.click()
            expect(oom_card.locator(".state")).to_contain_text("the script is running")
            expect(page.locator("#terminal-output")).to_contain_text("bash /opt/demo/oom.sh")
            assert all(button.is_disabled() for button in page.locator(".card button").all())
            assert posts[-1] == "oom"
            state["oom"].update(state="exited", returncode=0)
            state["terminal"]["logs"]["oom"] = "OOM trigger submitted. Await actual Kubernetes OOMKilled evidence."
            page.get_by_role("button", name="Refresh", exact=True).click()
            expect(page.locator("#oom-status")).to_contain_text("code 0")
            expect(page.locator("#terminal-output")).to_contain_text("Await actual Kubernetes OOMKilled evidence")

            response_lost = True
            expect(payment_button).to_be_enabled()
            payment_button.click()
            expect(page.locator("#result")).to_contain_text("Response lost")
            expect(payment_card.locator(".state")).to_contain_text("mode=error")
            assert not errors, errors

            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.locator("#terminal-output").focus()
            expect(page.locator("#terminal-output")).to_be_focused()
            if args.screenshot:
                page.screenshot(path=str(args.screenshot), full_page=True)
            print("PASS: 9 cards, scenario readiness, HTTP/reset clicks, both script locks, terminal commands/output/exit state, inert HTML, response loss, polling, service/backend failures and desktop/mobile; all requests intercepted, no backend or cluster calls.")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
