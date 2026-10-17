#!/usr/bin/env python3
"""Browser walkthrough of FocusGuard (Playwright, Chromium) on desktop 1366 px and mobile 390 px.

    pip install -r requirements-dev.txt
    python -m playwright install chromium      # browser binary (once)
    python tools/ui_walkthrough.py [--out DIR] [--base-url URL]

Without --base-url it starts tools/ui_demo_server.py on a free port with a
temporary database (seeded through the real domain layer), runs every check,
then stops it. Checks per page: unexpected JS errors, unexpected failed
requests, horizontal overflow, native alert/confirm dialogs, stored-XSS
execution; plus the teacher, admin, student and parent flows. Prints
WALKTHROUGH: N/N checks PASS; exit 1 on any failure. Screenshots and
walkthrough.json go to --out (default: a temporary folder).
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = OUT = None

VIEWPORTS = {"desktop": {"width": 1366, "height": 900}, "mobile": {"width": 390, "height": 844}}
OVERFLOW_JS = """() => {
  const w = document.documentElement.clientWidth;
  const off = [...document.querySelectorAll('body *')].filter(e => {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    if (!r.width || s.visibility === 'hidden' || s.display === 'none') return false;
    for (let p = e.parentElement; p; p = p.parentElement) {
      const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') return false; }
    return r.right > w + 1 || r.left < -1; }).slice(0, 5).map(e => e.tagName + '#' + e.id + '.' + e.className);
  return {scrollWidth: document.documentElement.scrollWidth, clientWidth: w, offenders: off};
}"""
OTHER_STUDENTS = ("Trần Thị Bình", "Lê Hoàng Giang", "Phạm Minh Hải", "Vũ Tiến Khoa")
results = []


def check(vp, step, ok, detail=""):
    results.append({"viewport": vp, "step": step, "ok": bool(ok), "detail": detail})
    print(f"[{vp}] {'PASS' if ok else 'FAIL'} {step} {detail}", flush=True)


def page_checks(page, vp, step, shot=None):
    o = page.evaluate(OVERFLOW_JS)
    check(vp, f"{step}: no horizontal overflow", o["scrollWidth"] <= o["clientWidth"] and not o["offenders"],
          json.dumps(o, ensure_ascii=False) if o["offenders"] else f"{o['scrollWidth']}/{o['clientWidth']}")
    xss = page.evaluate("() => window.__xss || null")
    check(vp, f"{step}: stored XSS payload not executed", xss is None, f"window.__xss={xss}")
    if shot:
        page.screenshot(path=os.path.join(OUT, f"{vp}-{shot}.png"), full_page=True)


def login(ctx, user, expected=()):
    """New page with error/dialog listeners. ``expected``: (status, url-suffix) pairs a step provokes on purpose."""
    page = ctx.new_page()
    errs, dialogs = [], []
    page.on("console", lambda m: m.type == "error" and errs.append("console: " + m.text))
    page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))

    def on_response(r):
        if r.status >= 400 and not any(r.status == st and r.url.split("?")[0].endswith(sfx) for st, sfx in expected):
            errs.append(f"http {r.status} {r.url}")
    page.on("response", on_response)

    def on_dialog(d):
        dialogs.append(f"{d.type}: {d.message}")
        d.dismiss()
    page.on("dialog", on_dialog)
    page.goto(BASE + "/login")
    page.fill("input[name=username]", user)
    page.fill("input[name=password]", "123")
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    return page, errs, dialogs


def confirm_ok(page):
    page.click("#fgConfirm.show [data-fg-confirm-ok]")


def expected_only(errs, statuses):
    """Drop the browser's console echo of a deliberately provoked HTTP error."""
    return [e for e in errs if not any(f"status of {s}" in e for s in statuses)]


# ------------------------------------------------------------------ flows
def teacher_gate(ctx, vp):
    """Before any evaluation-scope consent exists, the server refuses to start an evaluation."""
    page, errs, dialogs = login(ctx, "teacher", expected=[(400, "/api/teacher/evaluations")])
    page.goto(BASE + "/teacher/evaluation")
    page.wait_for_selector("#evalStartCard:not([hidden])", timeout=15000)
    page.check("#eConsent")
    page.click("#evalStart")
    page.wait_for_selector("#evalFormError:not([hidden])", timeout=10000)
    msg = page.text_content("#evalFormError").strip()
    check(vp, "Evaluation refused server-side without the 'evaluation' consent scope", "đồng ý" in msg, msg)
    page_checks(page, vp, "Evaluation gate", "0-evaluation-gate")
    return expected_only(errs, (400,)), dialogs


def admin_flow(ctx, vp):
    page, errs, dialogs = login(ctx, "admin")
    page.goto(BASE + "/admin/privacy")
    page.wait_for_selector("#privacyBody table", timeout=15000)
    rows = page.locator("#privacyBody tbody tr").count()
    check(vp, "Admin Privacy Dashboard lists students", rows >= 9, f"{rows} students")
    body = page.text_content("#privacyBody")
    check(vp, "Privacy rows show consent policy version", "Chính sách privacy-" in body)
    page.wait_for_selector("#auditBody .fg-event, #auditBody .fg-state", timeout=15000)
    check(vp, "Audit log visible", page.locator("#auditBody .fg-event").count() >= 1,
          f"{page.locator('#auditBody .fg-event').count()} entries")
    opts = page.locator("#auditAction option").count()
    check(vp, "Audit filter lists every action", opts >= 13, f"{opts} options")
    page_checks(page, vp, "Admin Privacy Dashboard", "6-privacy")

    # Consent workflow: validation, grant, revoke (student 5)
    page.click("#privacyBody [data-act=consent][data-id='5']")
    page.wait_for_selector("#consentModal.show", timeout=10000)
    page.click("#consentSave")
    check(vp, "Consent form requires guardian", page.locator("#consentFormError:not([hidden])").count() == 1,
          page.text_content("#consentFormError").strip())
    page.fill("#cGuardian", "Phụ huynh (walkthrough)")
    page.fill("#cRelation", "Mẹ")
    page_checks(page, vp, "Consent modal", "7-consent-modal")
    page.click("#consentSave")
    page.wait_for_selector("#consentModal", state="hidden", timeout=10000)
    page.wait_for_selector("#privacyBody [data-act=revoke][data-id='5']", timeout=10000)
    check(vp, "Consent granted -> revoke available", True)
    page.click("#privacyBody [data-act=revoke][data-id='5']")
    page.wait_for_selector("#fgConfirm.show", timeout=5000)
    check(vp, "Revoke asks for confirmation in an in-page dialog", page.locator("#fgConfirm.show").count() == 1)
    confirm_ok(page)
    page.wait_for_selector("#privacyBody [data-act=revoke][data-id='5']", state="detached", timeout=10000)
    check(vp, "Consent revoked", True)
    page.select_option("#auditAction", "CONSENT_REVOKED")
    page.wait_for_function("(() => { const t = [...document.querySelectorAll('#auditBody .fg-event-title')]; "
                           "return t.length >= 1 && t.every(e => e.textContent.includes('Thu hồi')); })()",
                           timeout=10000)
    check(vp, "Audit filter shows the revoke entry", True)

    # Evaluation-scope consent for student 6 (used by the teacher evaluation flow)
    page.click("#privacyBody [data-act=consent][data-id='6']")
    page.wait_for_selector("#consentModal.show", timeout=10000)
    page.fill("#cGuardian", "Phụ huynh (walkthrough)")
    page.fill("#cRelation", "Bố")
    page.check("#c_scope_evaluation")
    page.click("#consentSave")
    page.wait_for_selector("#consentModal", state="hidden", timeout=10000)
    page.wait_for_selector("#privacyBody [data-act=revoke][data-id='6']", timeout=10000)
    row = page.locator("#privacyBody tbody tr", has=page.locator("[data-act=revoke][data-id='6']")).inner_text()
    check(vp, "Evaluation scope consent recorded", "Đánh giá" in row and "Đang có hiệu lực" in row)
    return errs, dialogs


def teacher_flow(ctx, vp):
    page, errs, dialogs = login(ctx, "teacher")

    # Teacher Monitor + "Cần giáo viên xem lại"
    page.goto(BASE + "/teacher/dashboard")
    page.wait_for_selector("#reviewQueue .fg-event, #reviewQueue .fg-state", timeout=15000)
    check(vp, "Teacher Monitor renders", all(page.locator(x).count() == 1 for x in
                                             ("#sessionDot", "#studentGrid", "#contextCard", "#startBtn")))
    rq = page.locator("#reviewQueue .fg-event")
    queue_text = page.text_content("#reviewQueue")
    check(vp, "'Cần giáo viên xem lại' lists students, neutral wording",
          rq.count() >= 1 and "kém" not in queue_text and "nhất" not in queue_text, f"{rq.count()} rows")
    page_checks(page, vp, "Teacher Monitor", "1-monitor")

    # Start session (online mode: no class camera needed) + Activity Context + end session
    page.click("#modeTabs [data-mode=online]")
    page.wait_for_function("!document.getElementById('startBtn').disabled", timeout=15000)
    page.click("#startBtn")
    page.wait_for_selector("#stopBtn:not([hidden])", timeout=15000)
    check(vp, "Session started", page.locator("#sessionTimer:not([hidden])").count() == 1)
    page.wait_for_function("[...document.querySelectorAll('#contextButtons [data-context]')].some(b => !b.disabled)",
                           timeout=20000)
    page.locator("#contextButtons [data-context=WRITING]").click()
    page.wait_for_function("document.getElementById('contextBadge').textContent.includes('Chép bài')", timeout=10000)
    pressed = page.get_attribute("#contextButtons [data-context=WRITING]", "aria-pressed")
    check(vp, "Activity Context: WRITING applied (aria-pressed)", pressed == "true",
          page.text_content("#contextBadge").strip())
    page_checks(page, vp, "Activity Context (live)", "2-activity-context")
    page.click("#stopBtn")
    page.wait_for_selector("#fgConfirm.show", timeout=5000)
    confirm_ok(page)
    page.wait_for_selector("#startBtn:not([hidden])", timeout=15000)
    check(vp, "Session ended cleanly", page.locator("#stopBtn").is_hidden())
    page.keyboard.press("Escape")

    # Event Review + review modal + Evidence modal
    page.goto(BASE + "/teacher/events")
    page.wait_for_selector("#eventsBody [data-open-event], #eventsBody .fg-state", timeout=15000)
    page.select_option("#fStatus", "")
    page.wait_for_function("document.querySelectorAll('#eventsBody [data-open-event]').length >= 3", timeout=15000)
    n = page.locator("#eventsBody [data-open-event]").count()
    check(vp, "Event Review lists events", n >= 3, f"{n} events")
    page_checks(page, vp, "Event Review", "3-events")
    with_clip = None
    for i in range(n):
        page.locator("#eventsBody [data-open-event]").nth(i).click()
        page.wait_for_selector("#eventModal.show #eventDetail dl", timeout=10000)
        if page.locator("#evShow").count():
            with_clip = i
            break
        page.click("#eventModal.show .modal-footer [data-bs-dismiss=modal]")
        page.wait_for_selector("#eventModal", state="hidden", timeout=10000)
    check(vp, "Evidence modal: consented clip found", with_clip is not None)
    if with_clip is not None:
        page.click("#evShow")
        page.wait_for_selector("#evFrame", timeout=10000)
        src = page.get_attribute("#evFrame", "src") or ""
        check(vp, "Evidence modal: loads on request, never autoplays",
              page.get_attribute("#evPlay", "aria-pressed") == "false" and src.startswith("data:image/jpeg"),
              page.text_content("#evPos"))
        caption = page.text_content("#eventEvidence .fg-caption")
        check(vp, "Evidence modal: auto-delete countdown + cropped", "tự xóa sau" in caption and "đã cắt" in caption,
              caption.strip())
        page_checks(page, vp, "Evidence modal", "4-evidence-modal")
        page.fill("#reviewNote", "Walkthrough " + vp)
        page.click("#eventModal [data-verdict=UNCERTAIN]")
        page.wait_for_function("document.getElementById('reviewStatus').textContent.trim().length > 0", timeout=10000)
        check(vp, "Teacher review saved from the modal", True, page.text_content("#reviewStatus").strip())
        page.click("#eventModal.show .modal-footer [data-bs-dismiss=modal]")
        page.wait_for_selector("#eventModal", state="hidden", timeout=10000)
        page.wait_for_timeout(300)
        focused = page.evaluate("() => document.activeElement ? (document.activeElement.id || "
                                "document.activeElement.className || document.activeElement.tagName) : null")
        check(vp, "Focus returns to the page after closing the modal", focused not in (None, "BODY", ""), str(focused))
    # mark another event as a false positive (used by the analytics check)
    page.locator("#eventsBody [data-open-event]").nth(0 if with_clip != 0 else 1).click()
    page.wait_for_selector("#eventModal.show #eventDetail dl", timeout=10000)
    page.click("#eventModal [data-verdict=FALSE_POSITIVE]")
    page.wait_for_function("document.getElementById('reviewStatus').textContent.trim().length > 0", timeout=10000)
    check(vp, "False positive recorded", True, page.text_content("#reviewStatus").strip())
    page.click("#eventModal.show .modal-footer [data-bs-dismiss=modal]")

    # Analytics
    page.goto(BASE + "/teacher/analytics")
    page.wait_for_selector("#leaderBody .fg-event, #leaderBody .fg-state", timeout=15000)
    page.wait_for_function("document.getElementById('anEvents').textContent.trim() !== '—'", timeout=15000)
    sub = page.text_content("#anEventsSub")
    check(vp, "Analytics excludes teacher-confirmed false positives", "Không tính" in sub, sub.strip())
    avatars = page.locator("#leaderBody .fg-avatar").all_text_contents()
    check(vp, "Analytics lists scores without rank numbers", not any(a.strip().isdigit() for a in avatars),
          str(avatars))
    page_checks(page, vp, "Analytics", "5-analytics")

    # Real Classroom Evaluation + scenario guide
    page.goto(BASE + "/teacher/evaluation")
    page.wait_for_selector("#evalStartCard:not([hidden]), #evalActiveCard:not([hidden])", timeout=15000)
    if page.locator("#evalActiveCard:not([hidden])").count():
        page.click("#evalEnd")
        confirm_ok(page)
        page.wait_for_selector("#evalStartCard:not([hidden])", timeout=15000)
    page.click("#evalStart")
    check(vp, "Evaluation refuses to start without consent confirmation",
          page.locator("#evalFormError:not([hidden])").count() == 1)
    page.fill("#eCameraAngle", "góc trái, cao 2.2 m (walkthrough)")
    page.check("#eConsent")
    page.click("#evalStart")
    page.wait_for_selector("#evalActiveCard:not([hidden])", timeout=15000)
    check(vp, "Real Classroom Evaluation started", True)
    opts = page.locator("#eScenario option").count()
    page.select_option("#eScenario", "05_phone")
    help_text = page.text_content("#eScenarioHelp")
    check(vp, "Scenario guide: 15 scenarios with instructions", opts == 15 and "điện thoại" in help_text, f"{opts}")
    page.click("#eScenarioStart")
    page.wait_for_function("document.getElementById('eScenarioState').textContent.includes('05_phone')",
                           timeout=10000)
    check(vp, "Scenario started", True, page.text_content("#eScenarioState").strip())
    page.click("#eScenarioStop")
    page.wait_for_function("document.getElementById('eScenarioState').textContent.includes('Không có kịch bản')",
                           timeout=10000)
    check(vp, "Scenario stopped and listed", "05_phone" in page.text_content("#eScenarioRuns"))
    page.locator("#evalContextButtons [data-context=GROUP_DISCUSSION]").click()
    page.wait_for_function("document.querySelector('#evalContextButtons [data-context=GROUP_DISCUSSION]')"
                           ".getAttribute('aria-pressed') === 'true'", timeout=10000)
    page_checks(page, vp, "Real Classroom Evaluation", "8-evaluation")
    page.click("#evalEnd")
    confirm_ok(page)
    page.wait_for_selector("#evalStartCard:not([hidden])", timeout=15000)
    page.locator("#evalHistory [data-metrics]").first.click()
    page.wait_for_selector("#evalMetricsBody table, #evalMetricsBody .fg-state", timeout=15000)
    check(vp, "Evaluation metrics card says 'not enough data' in words",
          "Chưa đủ dữ liệu" in page.text_content("#evalMetricsBody"), page.text_content("#evalMetricsSub").strip()[:90])
    return errs, dialogs


def student_flow(ctx, vp):
    page, errs, dialogs = login(ctx, "hocsinh", expected=[(403, "/api/student/profile")])
    page.goto(BASE + "/")
    page.wait_for_load_state("networkidle")
    text = page.text_content("body")
    check(vp, "Student page shows own data only", "Nguyễn Văn An" in text and not any(n in text for n in OTHER_STUDENTS))
    other = page.evaluate("async () => (await fetch('/api/student/profile?student_id=4')).status")
    own = page.evaluate("async () => JSON.stringify(await (await fetch('/api/student/profile')).json())")
    forced = page.evaluate("async () => { const r = await fetch('/api/student/profile?student_id=4'); "
                           "return r.ok ? JSON.stringify(await r.json()) : null; }")
    check(vp, "Student cannot read another student's data", other == 403 or forced == own, f"status {other}")
    check(vp, "Student page uses 'tín hiệu AI' wording", "mất tập trung" not in text.lower())
    page_checks(page, vp, "Student page", "9-student")
    return expected_only(errs, (403,)), dialogs


def parent_flow(ctx, vp):
    page, errs, dialogs = login(ctx, "phuhuynh", expected=[(403, "/api/export/student/4"),
                                                           (404, "/api/export/student/4")])
    page.goto(BASE + "/parent/dashboard")
    page.wait_for_load_state("networkidle")
    text = page.text_content("body")
    check(vp, "Parent page shows the linked child only",
          "Nguyễn Văn An" in text and not any(n in text for n in OTHER_STUDENTS))
    status = page.evaluate("async () => (await fetch('/api/export/student/4?format=json')).status")
    check(vp, "Parent cannot export another child", status in (403, 404), f"status {status}")
    page_checks(page, vp, "Parent page", "10-parent")
    return expected_only(errs, (403, 404)), dialogs


# ------------------------------------------------------------ camera settings
def camera_teacher_flow(ctx, vp):
    """Settings > Camera: discover mock LAN camera, wrong code, test, preview, save, default, rename, delete."""
    page, errs, dialogs = login(ctx, "teacher")
    page.goto(BASE + "/settings/cameras")
    page.wait_for_selector("#myCamerasBody .fg-cam-grid, #myCamerasBody .fg-state", timeout=15000)
    page.click("#camDiscover")
    page.wait_for_selector("#discoveryResults [data-act=connect]", timeout=20000)
    page.wait_for_function("document.getElementById('discoveryStatus').textContent.includes('Đã tìm thấy')",
                           timeout=20000)
    found = page.text_content("#discoveryResults")
    check(vp, "LAN discovery finds the (mock) EZVIZ camera and says it needs authentication",
          "EZVIZ" in found and "Cần xác thực" in found, page.text_content("#discoveryStatus").strip())
    page_checks(page, vp, "Camera discovery", "11-camera-discovery")
    page.click("#discoveryResults [data-act=connect]")
    page.wait_for_selector("#connectModal.show", timeout=10000)
    check(vp, "EZVIZ username prefilled, no RTSP/URL field needed",
          page.input_value("#camUser") == "admin" and not page.is_visible("#camUrl"))
    page.fill("#camSecret", "WRONG1")
    page.click("#camTest")
    page.wait_for_function("document.getElementById('camResult').textContent.includes('Sai tên đăng nhập')", timeout=20000)
    check(vp, "Wrong verification code -> AUTH_FAILED in words, save stays disabled", page.is_disabled("#camSave"))
    page.fill("#camSecret", "ABCDEF")
    page.click("#camTest")
    page.wait_for_function("document.getElementById('camResult').textContent.includes('Đã kết nối')", timeout=20000)
    res = page.text_content("#camResult")
    check(vp, "Connection test: connected with resolution / FPS / latency", "FPS" in res and "ms" in res, res.strip()[:120])
    check(vp, "The typed code is cleared from the form after the test", page.input_value("#camSecret") == "")
    page.click("#camPreview")
    page.wait_for_function("(() => { const i = document.getElementById('camPreviewImg'); return i && i.naturalWidth > 0; })()",
                           timeout=20000)
    page.wait_for_function("document.getElementById('camPreviewInfo').textContent.includes('FPS')", timeout=15000)
    check(vp, "Preview shows live frames + health", True, page.text_content("#camPreviewInfo").strip()[:100])
    page_checks(page, vp, "Camera connect modal", "12-camera-connect")
    name = f"EZVIZ H1C – Phòng 8/1 ({vp})"
    page.fill("#camName", name)
    page.check("#camDefault")
    page.click("#camSave")
    page.wait_for_selector("#connectModal", state="hidden", timeout=15000)
    card = page.locator("#myCamerasBody .fg-cam-card", has_text=name)
    card.wait_for(timeout=10000)
    check(vp, "Saved camera card: default, connected, credentials hidden",
          "Mặc định" in card.inner_text() and "Đã kết nối" in card.inner_text() and "ABCDEF" not in page.content())
    # second camera: the server's webcam
    page.click("#camWebcam")
    page.wait_for_selector("#connectModal.show", timeout=10000)
    page.fill("#camName", f"Webcam {vp}")
    page.click("#camTest")
    page.wait_for_function("document.getElementById('camResult').textContent.includes('Đã kết nối')", timeout=20000)
    page.click("#camSave")
    page.wait_for_selector("#connectModal", state="hidden", timeout=15000)
    web = page.locator("#myCamerasBody .fg-cam-card", has_text=f"Webcam {vp}")
    web.wait_for(timeout=10000)
    web.locator("[data-act=default]").click()
    page.wait_for_function(f"[...document.querySelectorAll('#myCamerasBody .fg-cam-card')].some(c => "
                           f"c.textContent.includes('Webcam {vp}') && c.textContent.includes('Mặc định'))", timeout=10000)
    check(vp, "Set default moves the default badge (one default per teacher)",
          "Mặc định" not in page.locator("#myCamerasBody .fg-cam-card", has_text=name).inner_text())
    page.locator("#myCamerasBody .fg-cam-card", has_text=name).locator("[data-act=rename]").click()
    page.wait_for_selector("#renameModal.show", timeout=10000)
    page.fill("#renameInput", name + " ✓")
    page.click("#renameSave")
    page.wait_for_selector("#renameModal", state="hidden", timeout=10000)
    page.locator("#myCamerasBody .fg-cam-card", has_text=name + " ✓").wait_for(timeout=10000)
    check(vp, "Camera renamed", True)
    web.locator("[data-act=delete]").click()
    page.wait_for_selector("#fgConfirm.show", timeout=5000)
    confirm_ok(page)
    page.wait_for_function(f"![...document.querySelectorAll('#myCamerasBody .fg-cam-card')].some(c => "
                           f"c.textContent.includes('Webcam {vp}'))", timeout=10000)
    check(vp, "Camera deleted after an in-page confirmation", True)
    # stored-XSS probe in a camera name (rendered through FG.esc only)
    page.click("#camDiscover")
    page.wait_for_selector("#discoveryResults [data-act=connect]", timeout=20000)
    page.click("#discoveryResults [data-act=connect]")
    page.wait_for_selector("#connectModal.show", timeout=10000)
    page.fill("#camSecret", "ABCDEF")
    page.click("#camTest")
    page.wait_for_function("document.getElementById('camResult').textContent.includes('Đã kết nối')", timeout=20000)
    page.fill("#camName", f'<img src=x onerror="window.__xss=7">XSS {vp}')
    page.click("#camSave")
    page.wait_for_selector("#connectModal", state="hidden", timeout=15000)
    page.locator("#myCamerasBody .fg-cam-card", has_text=f"XSS {vp}").wait_for(timeout=10000)
    page_checks(page, vp, "Settings > Camera", "13-cameras")
    ids = page.evaluate("async () => (await (await fetch('/api/settings/cameras')).json()).my_cameras.map(c => c.camera_id)")
    with open(os.path.join(OUT, f"camera-ids-{vp}.json"), "w") as f:
        json.dump(ids, f)
    return errs, dialogs


def camera_idor_flow(ctx, vp):
    """Teacher B cannot read or change teacher A's cameras."""
    ids = json.load(open(os.path.join(OUT, f"camera-ids-{vp}.json")))
    page, errs, dialogs = login(ctx, "teacher2", expected=[(403, "/api/settings/cameras/" + i) for i in ids] +
                                [(403, "/api/settings/cameras/" + i + "/preview/start") for i in ids])
    mine = page.evaluate("async () => (await (await fetch('/api/settings/cameras')).json()).my_cameras.length")
    codes = page.evaluate("async (ids) => Promise.all(ids.map(async id => [ (await fetch('/api/settings/cameras/' + id, "
                          "{method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: 'x'})})).status, "
                          "(await fetch('/api/settings/cameras/' + id + '/preview/start', {method: 'POST', headers: {'Content-Type': "
                          "'application/json'}, body: '{}'})).status ]))", ids)
    check(vp, "Teacher B: no access to teacher A's cameras (list empty, PATCH/preview 403)",
          mine == 0 and all(c == [403, 403] for c in codes), str(codes))
    return expected_only(errs, (403,)), dialogs


def camera_admin_flow(ctx, vp):
    page, errs, dialogs = login(ctx, "admin")
    page.goto(BASE + "/settings/cameras")
    page.wait_for_selector("#classCamerasBody .fg-cam-grid, #classCamerasBody .fg-state", timeout=15000)
    page.click("#camManual")
    page.wait_for_selector("#connectModal.show", timeout=10000)
    check(vp, "Manual mode shows the advanced fields", page.is_visible("#camHost") and page.is_visible("#camPort"))
    page.fill("#camHost", "192.168.1.64")
    page.fill("#camUser", "admin")
    page.fill("#camSecret", "ABCDEF")
    page.click("#camTest")
    page.wait_for_function("document.getElementById('camResult').textContent.includes('Đã kết nối')", timeout=20000)
    name = f"Camera cổng ({vp})"
    page.fill("#camName", name)
    page.click("#camSave")
    page.wait_for_selector("#connectModal", state="hidden", timeout=15000)
    card = page.locator("#classCamerasBody .fg-cam-card", has_text=name)
    card.wait_for(timeout=10000)
    card.locator("[data-act=assign]").click()
    page.wait_for_selector("#assignModal.show", timeout=10000)
    page.select_option("#assignClass", "1")
    page.click("#assignSave")
    page.wait_for_selector("#assignModal", state="hidden", timeout=10000)
    page.wait_for_function(f"[...document.querySelectorAll('#classCamerasBody .fg-cam-card')].some(c => "
                           f"c.textContent.includes('{name}') && c.textContent.includes('10A1'))", timeout=10000)
    check(vp, "Admin school camera assigned to class 10A1", True)
    page_checks(page, vp, "Admin Settings > Camera", "14-admin-cameras")
    return errs, dialogs


# ------------------------------------------------------- classroom identity
def sim(page, action, person, other=None, weight=0.6):
    return page.evaluate("async (b) => (await fetch('/__demo/sim', {method: 'POST', headers: {'Content-Type': "
                         "'application/json'}, body: JSON.stringify(b)})).status",
                         {"action": action, "person": person, "other": other, "weight": weight})


def open_track(page, tid):
    page.click(f"#trackOverlay [data-track='{tid}']")
    page.wait_for_selector("#tagModal.show", timeout=10000)


def roster_row(page, name):
    return page.locator("#identityRoster li", has_text=name).inner_text()


def start_classroom(page, initialize):
    page.goto(BASE + "/teacher/dashboard")
    page.wait_for_function("!document.getElementById('startBtn').disabled || "
                           "!document.getElementById('stopBtn').hidden", timeout=15000)
    if page.locator("#stopBtn:not([hidden])").count():
        end_classroom(page)
    page.click("#modeTabs [data-mode=classroom]")
    if initialize:
        page.check("#initFirst")
    else:
        page.uncheck("#initFirst")
    page.click("#startBtn")
    page.wait_for_selector("#stopBtn:not([hidden])", timeout=15000)
    page.wait_for_function("document.querySelectorAll('#trackOverlay .fg-track').length === 5", timeout=30000)


def end_classroom(page):
    page.click("#stopBtn")
    page.wait_for_selector("#fgConfirm.show", timeout=5000)
    confirm_ok(page)
    page.wait_for_selector("#startBtn:not([hidden])", timeout=15000)
    page.wait_for_selector("#sessionModal.show", timeout=10000)            # end-of-class summary
    page.wait_for_selector("#sessionModal.show table, #sessionModal.show .fg-state", timeout=15000)
    page.click("#sessionModal.show .modal-footer [data-bs-dismiss=modal]")
    page.wait_for_selector("#sessionModal", state="hidden", timeout=10000)


def ensure_class_ended(ctx):
    """A crashed classroom flow must not leave class 10A1 running for the next flows."""
    for pg in ctx.pages:
        try:
            pg.evaluate("async () => fetch('/api/teacher/end_offline_class', {method: 'POST', headers: "
                        "{'Content-Type': 'application/json'}, body: JSON.stringify({class_id: 1})})")
            return
        except Exception:
            continue


def classroom_identity_flow(ctx, vp):
    page, errs, dialogs = login(ctx, "teacher", expected=[(409, "/api/teacher/classroom/tag")])
    start_classroom(page, initialize=True)
    check(vp, "Khởi tạo danh sách: 5 clickable person boxes over the camera",
          page.locator("#trackOverlay .fg-track").count() == 5 and page.is_visible("#identityCard"))
    check(vp, "Initialization phase: no scoring yet", "chưa chấm điểm" in page.text_content("#identityPhase"))
    page.wait_for_function("[...document.querySelectorAll('#trackOverlay .fg-track')].filter(b => "
                           "b.dataset.tone === 'info').length >= 3", timeout=20000)
    check(vp, "AI suggests 3 known students", True)
    page_checks(page, vp, "Classroom initialization", "15-init")
    for tid in (1, 2):
        open_track(page, tid)
        check(vp, f"Track {tid}: AI suggestion offered", page.is_visible("#tagSuggestion"))
        page.click("#tagAccept")
        page.wait_for_selector("#tagModal", state="hidden", timeout=10000)
        page.wait_for_selector(f"#trackOverlay [data-track='{tid}'][data-tone=success]", timeout=10000)
    check(vp, "Teacher confirmed 2 suggestions", True)
    open_track(page, 3)
    sug = page.text_content("#tagSuggestionText")
    page.click("#tagOther")
    page.check("#tagList input[value='9']")
    page.click("#tagSave")
    page.wait_for_selector("#tagModal", state="hidden", timeout=10000)
    page.wait_for_function("document.querySelector('#identityRoster') && "
                           "[...document.querySelectorAll('#identityRoster li')].some(l => l.textContent.includes('Hoàng Đức Minh')"
                           " && l.textContent.includes('Giáo viên sửa'))", timeout=10000)
    check(vp, "Wrong AI suggestion corrected by the teacher (TEACHER_CORRECTED)", "Bùi Thị Ngọc" in sug, sug)
    open_track(page, 4)
    taken = page.is_disabled("#tagList input[value='3']")
    check(vp, "Duplicate assignment blocked: an already tagged student cannot be picked", taken)
    dup = page.evaluate("async () => (await fetch('/api/teacher/classroom/tag', {method: 'POST', headers: "
                        "{'Content-Type': 'application/json'}, body: JSON.stringify({class_id: 1, track_id: 4, student_id: 3})})).status")
    check(vp, "Duplicate assignment refused by the server too (409)", dup == 409)
    page.check("#tagList input[value='7']")
    page.click("#tagSave")
    page.wait_for_selector("#tagModal", state="hidden", timeout=10000)
    row7 = roster_row(page, "Vũ Tiến Khoa")
    check(vp, "Student without face consent: session-only tag", "Người #4" in row7 and "chỉ trong buổi" in row7, row7)
    open_track(page, 5)
    page.check("#tagList input[value='8']")
    page.click("#tagSave")
    page.wait_for_selector("#tagModal", state="hidden", timeout=10000)
    page.wait_for_function("[...document.querySelectorAll('#identityRoster li')].some(l => l.textContent.includes('Ngô Khánh Linh')"
                           " && (l.textContent.includes('Sẵn sàng') || l.textContent.includes('Đủ dùng')))", timeout=30000)
    check(vp, "Consented student: enrollment burst -> profile PARTIAL/READY", True, roster_row(page, "Ngô Khánh Linh")[:120])
    page_checks(page, vp, "Roster after tagging", "16-roster")
    page.click("#beginBtn")
    page.wait_for_function("document.getElementById('identityPhase').textContent.includes('Đang theo dõi')", timeout=10000)
    check(vp, "Hoàn tất danh sách -> monitoring", True)
    sim(page, "hide_face", "P4")
    page.wait_for_timeout(3000)
    check(vp, "Face hidden: the tracked person keeps the identity", "Người #4" in roster_row(page, "Vũ Tiến Khoa"))
    sim(page, "contradict", "P2", "P1", 0.95)
    page.wait_for_function("[...document.querySelectorAll('#identityRoster li')].some(l => l.textContent.includes('Trần Thị Bình')"
                           " && l.textContent.includes('Cần xem lại'))", timeout=20000)
    check(vp, "Contradicting face evidence -> review required, no silent switch",
          "Người #2" in roster_row(page, "Trần Thị Bình"))
    sim(page, "reset", "P2")
    open_track(page, 2)
    page.check("#tagList input[value='4']")
    page.click("#tagSave")
    page.wait_for_selector("#tagModal", state="hidden", timeout=10000)
    page.wait_for_function("![...document.querySelectorAll('#identityRoster li')].some(l => l.textContent.includes('Cần xem lại'))",
                           timeout=10000)
    check(vp, "Teacher resolved the review", True)
    # camera pipeline rebuilt mid-class (reconnect / page reload): the teacher's session tag of the
    # student WITHOUT face consent is kept and continued by seat continuity (never by an old track id)
    sim(page, "rebuild", None)
    page.reload()
    page.wait_for_function("[...document.querySelectorAll('#identityRoster li')].some(l => l.textContent.includes('Vũ Tiến Khoa')"
                           " && l.textContent.includes('Khôi phục theo vị trí'))", timeout=30000)
    check(vp, "Camera rebuilt + page reload: teacher tag kept, restored by seat (no face data)",
          "Người #4" in roster_row(page, "Vũ Tiến Khoa"), roster_row(page, "Vũ Tiến Khoa")[:120])
    # attendance override
    page.goto(BASE + "/teacher/attendance")
    page.wait_for_selector("#attBody [data-att-student]", timeout=15000)
    page.select_option("#attBody [data-att-student='11']", "LATE")
    page.wait_for_function("[...document.querySelectorAll('#attBody tr')].some(r => r.textContent.includes('GV sửa'))", timeout=10000)
    check(vp, "Attendance teacher override saved (GV sửa)", True)
    page_checks(page, vp, "Attendance override", "17-attendance")
    start_classroom(page, initialize=False)        # ends the running one first, then a later session
    sim(page, "remove", "P2")                      # student 4 leaves the room for a moment ...
    sim(page, "bias", "P1", "P2", 0.85)            # ... and P1's face resembles 4's profile: a wrong recognition
    page.wait_for_function("(() => { const b = document.querySelector(\"#trackOverlay [data-track='1']\"); "
                           "return b && b.getAttribute('aria-label').includes('AI: Trần Thị Bình'); })()", timeout=30000)
    check(vp, "Later session: AI recognizes (here wrongly) without tagging", True)
    open_track(page, 1)
    page.check("#tagList input[value='3']")
    page.click("#tagSave")
    page.wait_for_selector("#tagModal", state="hidden", timeout=10000)
    sim(page, "reset", "P1")
    page.wait_for_function("[...document.querySelectorAll('#identityRoster li')].some(l => l.textContent.includes('Nguyễn Văn An')"
                           " && l.textContent.includes('Giáo viên sửa'))", timeout=15000)
    check(vp, "Wrong recognition re-tagged by the teacher (TEACHER_CORRECTED)", True)
    sim(page, "restore", "P2")                     # student 4 comes back (new tracker id)
    page.wait_for_function("[...document.querySelectorAll('#identityRoster li')].some(l => l.textContent.includes('Trần Thị Bình')"
                           " && l.textContent.includes('Người #6'))", timeout=20000)
    check(vp, "The correct student is recognized again on their new track", True)
    page_checks(page, vp, "Later session", "18-later-session")
    end_classroom(page)
    return expected_only(errs, (409,)), dialogs


def classroom_identity_mobile(ctx, vp):
    page, errs, dialogs = login(ctx, "teacher")
    start_classroom(page, initialize=True)
    open_track(page, 1)
    page_checks(page, vp, "Tag modal (mobile)", "15-tag-modal")
    if page.is_visible("#tagSuggestion"):
        page.click("#tagAccept")
    else:
        page.check("#tagList input[value='3']")
        page.click("#tagSave")
    page.wait_for_selector("#tagModal", state="hidden", timeout=10000)
    page.wait_for_selector("#trackOverlay [data-track='1'][data-tone=success]", timeout=10000)
    check(vp, "Mobile: tag a person on the camera", True)
    page_checks(page, vp, "Classroom initialization (mobile)", "16-init")
    page.click("#beginBtn")
    end_classroom(page)
    return errs, dialogs


def run_checks():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for vp, size in VIEWPORTS.items():
            kw = {"viewport": size}
            if vp == "mobile":
                kw.update(device_scale_factor=3, is_mobile=True, has_touch=True)
            flows = ((teacher_gate,) if vp == "desktop" else ()) + (admin_flow, teacher_flow, student_flow, parent_flow)
            flows += (camera_teacher_flow, camera_idor_flow, camera_admin_flow)
            flows += (classroom_identity_flow,) if vp == "desktop" else (classroom_identity_mobile,)
            for flow in flows:
                ctx = browser.new_context(**kw)
                try:
                    errs, dialogs = flow(ctx, vp)
                    check(vp, f"{flow.__name__}: no unexpected JS errors / failed requests", not errs, "; ".join(errs[:5]))
                    check(vp, f"{flow.__name__}: no native alert/confirm dialogs", not dialogs, "; ".join(dialogs[:3]))
                except Exception as exc:          # a broken step is a FAIL, never skipped
                    check(vp, f"{flow.__name__} crashed", False, f"{type(exc).__name__}: {str(exc)[:300]}")
                    for pg in ctx.pages:
                        pg.screenshot(path=os.path.join(OUT, f"{vp}-{flow.__name__}-crash.png"), full_page=True)
                    if flow.__name__.startswith("classroom"):
                        ensure_class_ended(ctx)
                ctx.close()
        browser.close()

    with open(os.path.join(OUT, "walkthrough.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    failed = [r for r in results if not r["ok"]]
    print(f"WALKTHROUGH: {len(results) - len(failed)}/{len(results)} checks PASS")
    return 1 if failed else 0


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv=None):
    global BASE, OUT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=None, help="use an already running server instead of starting one")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    OUT = a.out or tempfile.mkdtemp(prefix="focusguard-walkthrough-")
    os.makedirs(OUT, exist_ok=True)
    server = None
    data = None
    try:
        if a.base_url:
            BASE = a.base_url.rstrip("/")
        else:
            port = free_port()
            data = tempfile.mkdtemp(prefix="focusguard-ui-demo-")
            # Server output goes to a file: an undrained pipe fills up and blocks the server.
            server_log = open(os.path.join(OUT, "demo-server.log"), "w", encoding="utf-8")
            server = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "ui_demo_server.py"), data, str(port)],
                                      stdout=server_log, stderr=subprocess.STDOUT, text=True)
            BASE = f"http://127.0.0.1:{port}"
            for _ in range(120):
                try:
                    if urllib.request.urlopen(BASE + "/login", timeout=2).status == 200:
                        break
                except Exception:
                    if server.poll() is not None:
                        server_log.flush()
                        print(open(os.path.join(OUT, "demo-server.log"), encoding="utf-8").read()[-4000:])
                        return 1
                    time.sleep(0.5)
            print(f"demo server {BASE} (temporary data {data})")
        code = run_checks()
        print(f"screenshots + walkthrough.json -> {OUT}")
        return code
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        if data:
            import shutil
            shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
