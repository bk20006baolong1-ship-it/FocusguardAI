/* FocusGuard shared front-end helpers (every page loads this first).
 *
 * The browser never decides AI state, identity, role or score: this file only
 * renders what the server returns and reports request failures truthfully.
 */
(function () {
  "use strict";

  const FG = (window.FG = {});

  /* ------------------------------------------------------------ escaping */
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  FG.esc = (v) => (v === null || v === undefined ? "" : String(v).replace(/[&<>"']/g, (c) => ESC[c]));
  FG.$ = (sel, root) => (root || document).querySelector(sel);
  FG.$$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  FG.initials = (name) => {
    const parts = String(name || "?").trim().split(/\s+/);
    const last = parts[parts.length - 1] || "?";
    const first = parts.length > 1 ? parts[0] : "";
    return ((first[0] || "") + (last[0] || "")).toUpperCase() || "?";
  };

  /* ---------------------------------------------------------- formatters */
  FG.score = (v) => (v === null || v === undefined ? "Chưa có" : String(Math.round(v)));
  FG.hasScore = (v) => v !== null && v !== undefined;

  /* "14 giây", "2 phút 05 giây", "1 giờ 12 phút" */
  FG.duration = (seconds) => {
    if (seconds === null || seconds === undefined || isNaN(seconds)) return "—";
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return s + " giây";
    const m = Math.floor(s / 60);
    if (m < 60) return m + " phút" + (s % 60 ? " " + String(s % 60).padStart(2, "0") + " giây" : "");
    const h = Math.floor(m / 60);
    return h + " giờ" + (m % 60 ? " " + (m % 60) + " phút" : "");
  };

  /* 00:00 / 1:02:03 timer */
  FG.clock = (seconds) => {
    const s = Math.max(0, Math.floor(seconds || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    const mm = String(m).padStart(2, "0"), ss = String(r).padStart(2, "0");
    return h ? h + ":" + mm + ":" + ss : mm + ":" + ss;
  };

  /* accepts epoch seconds or "YYYY-MM-DD HH:MM:SS" */
  FG.toDate = (v) => {
    if (v === null || v === undefined || v === "") return null;
    if (typeof v === "number") return new Date(v * 1000);
    const d = new Date(String(v).replace(" ", "T"));
    return isNaN(d.getTime()) ? null : d;
  };
  const pad = (n) => String(n).padStart(2, "0");
  FG.time = (v) => { const d = FG.toDate(v); return d ? pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds()) : "—"; };
  FG.date = (v) => { const d = FG.toDate(v); return d ? pad(d.getDate()) + "/" + pad(d.getMonth() + 1) + "/" + d.getFullYear() : "—"; };
  FG.dateTime = (v) => { const d = FG.toDate(v); return d ? FG.date(v) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes()) : "—"; };

  /* ------------------------------------------------ behaviour vocabulary */
  /* One stable colour + icon + wording per behaviour, everywhere. */
  FG.BEHAVIOR = {
    FOCUSED:   { label: "Tập trung",        tone: "focused",  icon: "fa-circle-check" },
    PHONE:     { label: "Dùng điện thoại",  tone: "phone",    icon: "fa-mobile-screen",
                 action: "Nhắc học sinh cất điện thoại." },
    DROWSY:    { label: "Buồn ngủ",         tone: "drowsy",   icon: "fa-moon",
                 action: "Hỏi thăm hoặc cho học sinh vận động nhẹ." },
    HEAD_AWAY: { label: "Quay đi chỗ khác", tone: "headaway", icon: "fa-arrows-left-right",
                 action: "Gọi học sinh quay lại bài học." },
    AWAY:      { label: "Rời chỗ",          tone: "away",     icon: "fa-person-walking-arrow-right",
                 action: "Kiểm tra học sinh đã xin phép ra ngoài chưa." },
    TEMPORARILY_NOT_VISIBLE: { label: "Tạm khuất", tone: "unknown", icon: "fa-eye-slash" },
    UNKNOWN:   { label: "Chưa có dữ liệu",  tone: "unknown",  icon: "fa-circle-question" },
  };
  FG.behavior = (type) => FG.BEHAVIOR[type] || { label: type || "Không rõ", tone: "unknown", icon: "fa-circle-question" };

  FG.ATTENDANCE = {
    PRESENT: { label: "Có mặt", tone: "success", icon: "fa-user-check" },
    LATE:    { label: "Đi muộn", tone: "warning", icon: "fa-user-clock" },
    ABSENT:  { label: "Vắng mặt", tone: "danger", icon: "fa-user-slash" },
    NOT_YET: { label: "Chưa điểm danh", tone: "neutral", icon: "fa-hourglass-half" },
  };
  FG.attendance = (k) => FG.ATTENDANCE[k] || FG.ATTENDANCE.NOT_YET;

  /* What one student looks like right now. Derived only from server fields.
   * ctx = { active: bool, mode: "classroom"|"online"|"personal"|null } */
  FG.studentStatus = (s, ctx) => {
    ctx = ctx || {};
    if (!ctx.active) return { key: "IDLE", label: "Chưa có buổi học", tone: "neutral", icon: "fa-circle-pause" };
    if (s.visibility_status === "NO_CAMERA_SIGNAL")
      return { key: "NO_SIGNAL", label: "Mất tín hiệu camera", tone: "nocam", icon: "fa-video-slash",
               detail: "Điểm tạm dừng, không bị trừ." };
    if (ctx.mode === "online" && s.connection_status === "OFFLINE")
      return { key: "OFFLINE", label: "Ngoại tuyến", tone: "neutral", icon: "fa-plug-circle-xmark",
               detail: "Học sinh chưa vào phiên học." };
    if (s.visibility_status === "NOT_SEEN")
      return { key: "NOT_SEEN", label: ctx.mode === "classroom" ? "Chưa nhận diện" : "Chưa thấy trong khung hình",
               tone: "unknown", icon: "fa-circle-question",
               detail: ctx.mode === "classroom" ? "Chưa thấy học sinh này trong khung hình." : "Camera chưa ghi nhận khuôn mặt." };
    if (s.visibility_status === "AWAY") return Object.assign({ key: "AWAY", since: s.not_visible_since }, FG.BEHAVIOR.AWAY);
    if (s.visibility_status === "TEMPORARILY_NOT_VISIBLE")
      return Object.assign({ key: "TEMPORARILY_NOT_VISIBLE", since: s.not_visible_since,
                             detail: "Có thể bị che khuất. Chưa tính là rời chỗ." }, FG.BEHAVIOR.TEMPORARILY_NOT_VISIBLE);
    const first = (s.behaviors || []).find((b) => b.type === s.focus_state);
    if (s.focus_state === "PHONE" || s.focus_state === "DROWSY" || s.focus_state === "HEAD_AWAY")
      return Object.assign({ key: s.focus_state, since: first ? first.since : null,
                             confidence: first ? first.confidence : null }, FG.BEHAVIOR[s.focus_state]);
    if (s.focus_state === "FOCUSED") return Object.assign({ key: "FOCUSED" }, FG.BEHAVIOR.FOCUSED);
    return Object.assign({ key: "UNKNOWN", detail: "Chưa đủ dữ liệu quan sát." }, FG.BEHAVIOR.UNKNOWN);
  };
  FG.NEEDS_ATTENTION = ["PHONE", "DROWSY", "HEAD_AWAY", "AWAY"];

  FG.badge = (label, tone, icon) =>
    '<span class="fg-badge" data-tone="' + FG.esc(tone || "neutral") + '">' +
    (icon ? '<i class="fa-solid ' + FG.esc(icon) + '" aria-hidden="true"></i>' : "") + FG.esc(label) + "</span>";

  FG.scoreTone = (v) => (!FG.hasScore(v) ? "unknown" : v >= 80 ? "success" : v >= 50 ? "warning" : "danger");

  /* ------------------------------------------------------- state blocks */
  FG.stateHTML = (o) =>
    '<div class="fg-state' + (o.error ? " is-error" : "") + (o.compact ? " is-compact" : "") + '" role="' + (o.error ? "alert" : "status") + '">' +
    '<div class="fg-state-icon"><i class="fa-solid ' + FG.esc(o.icon || (o.error ? "fa-triangle-exclamation" : "fa-inbox")) + '" aria-hidden="true"></i></div>' +
    '<p class="fg-state-title">' + FG.esc(o.title) + "</p>" +
    (o.text ? '<p class="fg-state-text">' + FG.esc(o.text) + "</p>" : "") +
    (o.retry ? '<button type="button" class="btn btn-secondary btn-sm mt-2" data-fg-retry><i class="fa-solid fa-rotate-right" aria-hidden="true"></i>Thử lại</button>' : "") +
    "</div>";
  FG.setState = (el, o) => {
    if (!el) return;
    el.innerHTML = FG.stateHTML(o);
    if (o.retry) { const b = el.querySelector("[data-fg-retry]"); if (b) b.addEventListener("click", o.retry); }
  };
  FG.skeleton = (el, rows) => {
    if (!el) return;
    el.innerHTML = '<div aria-busy="true" aria-label="Đang tải">' +
      Array.from({ length: rows || 3 }, () => '<div class="fg-skeleton fg-skeleton-row"></div>').join("") + "</div>";
  };

  /* ------------------------------------------------------------- network */
  /* Resolves with parsed JSON on success. Rejects (Error with .status) on
   * network failure, non-2xx, or a body saying status:"error". */
  FG.api = async (url, opts) => {
    opts = opts || {};
    const init = { method: opts.method || "GET", credentials: "same-origin", headers: { Accept: "application/json" } };
    if (opts.body !== undefined) { init.method = opts.method || "POST"; init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    let res;
    try { res = await fetch(url, init); }
    catch (e) { const err = new Error("Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại."); err.status = 0; throw err; }
    if (res.status === 401) { window.location.href = "/login?expired=1"; const err = new Error("Phiên đăng nhập đã hết hạn."); err.status = 401; throw err; }
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok || (data && data.status === "error")) {
      const fallback = res.status === 403 ? "Bạn không có quyền thực hiện thao tác này."
        : res.status === 404 ? "Không tìm thấy dữ liệu."
        : res.status >= 500 ? "Máy chủ gặp lỗi. Vui lòng thử lại." : "Không thực hiện được thao tác này.";
      const err = new Error((data && data.message) || fallback);
      err.status = res.status; err.data = data;
      throw err;
    }
    return data;
  };

  /* Disable + spinner while an action runs; blocks double submit. */
  FG.busy = (btn, on, label) => {
    if (!btn) return;
    if (on) {
      if (btn.dataset.fgBusy === "1") return;
      btn.dataset.fgBusy = "1"; btn.dataset.fgHtml = btn.innerHTML; btn.disabled = true;
      btn.setAttribute("aria-busy", "true");
      btn.innerHTML = '<span class="spinner-border" aria-hidden="true"></span>' + FG.esc(label || "Đang xử lý…");
    } else if (btn.dataset.fgBusy === "1") {
      btn.innerHTML = btn.dataset.fgHtml; btn.disabled = false; btn.removeAttribute("aria-busy");
      delete btn.dataset.fgBusy; delete btn.dataset.fgHtml;
    }
  };
  FG.isBusy = (btn) => !!btn && btn.dataset.fgBusy === "1";

  /* --------------------------------------------------------------- toast */
  const TOAST_ICON = { success: "fa-circle-check", danger: "fa-triangle-exclamation", warning: "fa-circle-exclamation", info: "fa-circle-info" };
  FG.toast = (message, tone, timeout) => {
    const region = document.getElementById("fgToasts");
    if (!region) return;
    tone = tone || "info";
    const el = document.createElement("div");
    el.className = "fg-toast"; el.dataset.tone = tone;
    el.setAttribute("role", tone === "danger" ? "alert" : "status");
    el.innerHTML = '<i class="fa-solid ' + (TOAST_ICON[tone] || TOAST_ICON.info) + '" aria-hidden="true"></i>' +
      '<div class="fg-toast-body">' + FG.esc(message) + "</div>" +
      '<button type="button" class="fg-toast-close" aria-label="Đóng thông báo"><i class="fa-solid fa-xmark" aria-hidden="true"></i></button>';
    const close = () => el.remove();
    el.querySelector("button").addEventListener("click", close);
    region.appendChild(el);
    while (region.children.length > 4) region.firstChild.remove();
    setTimeout(close, timeout || (tone === "danger" ? 8000 : 4500));
  };

  /* ------------------------------------------------------------- confirm */
  /* FG.confirm({title, body, confirmLabel, danger}) -> Promise<boolean> */
  FG.confirm = (o) =>
    new Promise((resolve) => {
      const el = document.getElementById("fgConfirm");
      if (!el || !window.bootstrap) { resolve(window.confirm(o.title + "\n\n" + (o.body || ""))); return; }
      FG.$("[data-fg-confirm-title]", el).textContent = o.title;
      FG.$("[data-fg-confirm-body]", el).textContent = o.body || "";
      const list = FG.$("[data-fg-confirm-list]", el);
      list.innerHTML = (o.consequences || []).map((c) => "<li>" + FG.esc(c) + "</li>").join("");
      list.hidden = !(o.consequences && o.consequences.length);
      const ok = FG.$("[data-fg-confirm-ok]", el);
      ok.textContent = o.confirmLabel || "Xác nhận";
      ok.className = "btn " + (o.danger ? "btn-danger" : "btn-primary");
      const modal = window.bootstrap.Modal.getOrCreateInstance(el);
      let answer = false;
      const onOk = () => { answer = true; modal.hide(); };
      const onHidden = () => { ok.removeEventListener("click", onOk); el.removeEventListener("hidden.bs.modal", onHidden); resolve(answer); };
      ok.addEventListener("click", onOk);
      el.addEventListener("hidden.bs.modal", onHidden);
      modal.show();
    });

  /* ------------------------------------------------- realtime connection */
  /* Shows the truth about the live channel: connecting / live / reconnecting. */
  FG.setConnection = (state) => {
    const pill = document.getElementById("fgConn");
    if (!pill) return;
    const map = {
      connecting:   { tone: "neutral", text: "Đang kết nối…" },
      live:         { tone: "success", text: "Trực tiếp" },
      reconnecting: { tone: "warning", text: "Mất kết nối, đang thử lại…" },
      offline:      { tone: "danger",  text: "Mất kết nối" },
    };
    const m = map[state] || map.connecting;
    pill.hidden = false; pill.dataset.tone = m.tone; pill.dataset.state = state;
    pill.innerHTML = '<span class="fg-dot" aria-hidden="true"></span><span>' + m.text + "</span>";
  };
  FG.connectSocket = (handlers) => {
    if (typeof window.io !== "function") { FG.setConnection("offline"); return null; }
    FG.setConnection("connecting");
    const socket = window.io({ reconnectionDelayMax: 5000 });   // polling first, upgrades when the server supports it
    socket.on("connect", () => { FG.setConnection("live"); if (handlers && handlers.onConnect) handlers.onConnect(socket); });
    socket.on("disconnect", () => { FG.setConnection("reconnecting"); if (handlers && handlers.onDisconnect) handlers.onDisconnect(); });
    socket.on("connect_error", () => FG.setConnection("reconnecting"));
    return socket;
  };

  /* --------------------------------------------------------------- theme */
  FG.theme = () => document.documentElement.getAttribute("data-bs-theme") || "light";
  FG.setTheme = (t) => {
    document.documentElement.setAttribute("data-bs-theme", t);
    try { localStorage.setItem("fg-theme", t); } catch (e) { /* private mode */ }
    document.dispatchEvent(new CustomEvent("fg:theme", { detail: t }));
    const btn = document.getElementById("fgThemeBtn");
    if (btn) {
      btn.setAttribute("aria-label", t === "dark" ? "Chuyển sang giao diện sáng" : "Chuyển sang giao diện tối");
      btn.innerHTML = '<i class="fa-solid ' + (t === "dark" ? "fa-sun" : "fa-moon") + '" aria-hidden="true"></i>';
    }
  };
  FG.cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  /* Chart.js defaults that follow the design tokens. */
  FG.chartDefaults = () => {
    if (!window.Chart) return;
    window.Chart.defaults.font.family = FG.cssVar("--fg-font") || "sans-serif";
    window.Chart.defaults.color = FG.cssVar("--fg-text-2");
    window.Chart.defaults.borderColor = FG.cssVar("--fg-border");
    window.Chart.defaults.animation = false;
  };

  /* ---------------------------------------------------------------- boot */
  document.addEventListener("DOMContentLoaded", () => {
    FG.setTheme(FG.theme());
    const themeBtn = document.getElementById("fgThemeBtn");
    if (themeBtn) themeBtn.addEventListener("click", () => FG.setTheme(FG.theme() === "dark" ? "light" : "dark"));

    const app = document.getElementById("fgApp");
    const navBtn = document.getElementById("fgNavBtn");
    const closeNav = () => { if (app) app.classList.remove("is-nav-open"); if (navBtn) navBtn.setAttribute("aria-expanded", "false"); };
    if (navBtn && app) {
      navBtn.addEventListener("click", () => {
        const open = app.classList.toggle("is-nav-open");
        navBtn.setAttribute("aria-expanded", open ? "true" : "false");
      });
      const backdrop = document.getElementById("fgBackdrop");
      if (backdrop) backdrop.addEventListener("click", closeNav);
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeNav(); });
    }

    FG.$$("[data-fg-datetime]").forEach((el) => { el.textContent = FG.dateTime(el.dataset.fgDatetime); });
    if (window.bootstrap) FG.$$('[data-bs-toggle="tooltip"]').forEach((el) => new window.bootstrap.Tooltip(el));
    FG.chartDefaults();
  });
})();
