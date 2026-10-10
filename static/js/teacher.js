/* Teacher pages. Everything shown here comes from the server snapshot
 * (/api/teacher/class_status and the class_snapshot Socket.IO event); the
 * browser never computes AI state, attendance or scores. */
(function () {
  "use strict";
  const FG = window.FG;
  const bootEl = document.getElementById("fgBoot");
  if (!bootEl) return;
  const BOOT = JSON.parse(bootEl.textContent);
  const $ = (id) => document.getElementById(id);
  const esc = FG.esc;

  const S = {
    classId: BOOT.class_id,
    page: BOOT.page,
    threshold: Number(BOOT.min_focus_threshold) || 0,
    data: null,          // last server snapshot
    receivedAt: 0,       // performance.now() when it arrived
    loadError: null,
    mode: "classroom",   // mode picked for the next session
    filter: "all",
    alertFilter: "all",
    extraLogs: [],       // client_report entries (not part of the runtime log)
    socket: null,
    panelStudentId: null,
    camera: { src: false, startedAt: 0, error: false, lastState: null },
  };

  const MODE_LABEL = { classroom: "Camera lớp học", online: "Lớp trực tuyến" };
  const ctx = () => ({ active: !!(S.data && S.data.class_session_active), mode: S.data ? S.data.mode : null });
  const drift = () => (performance.now() - S.receivedAt) / 1000;
  const serverNow = () => (S.data ? S.data.server_time + drift() : Date.now() / 1000);
  const elapsed = () => (S.data && S.data.class_session_active ? S.data.elapsed_seconds + drift() : 0);
  const classQuery = (extra) => "?class_id=" + encodeURIComponent(S.classId) + (extra || "");
  const studentById = (id) => (S.data ? S.data.students.find((s) => s.student_id === id) : null);

  /* ------------------------------------------------------- keyed lists */
  /* Updates children in place so keyboard focus and hover survive the
   * once-per-second refresh. make(item) -> {key, tag, cls, attrs, html}. */
  function renderKeyed(container, items, make) {
    const existing = new Map();
    Array.from(container.children).forEach((el) => { if (el.dataset.key) existing.set(el.dataset.key, el); else el.remove(); });
    items.forEach((item, i) => {
      const spec = make(item);
      let el = existing.get(spec.key);
      if (el) existing.delete(spec.key);
      else { el = document.createElement(spec.tag || "div"); el.dataset.key = spec.key; if (spec.tag === "button") el.type = "button"; }
      const sig = spec.cls + "|" + JSON.stringify(spec.attrs || {}) + "|" + spec.html;
      if (el._sig !== sig) {
        el.className = spec.cls;
        Object.entries(spec.attrs || {}).forEach(([k, v]) => el.setAttribute(k, v));
        el.innerHTML = spec.html;
        el._sig = sig;
      }
      if (container.children[i] !== el) container.insertBefore(el, container.children[i] || null);
    });
    existing.forEach((el) => el.remove());
  }

  /* ------------------------------------------------ status derivations */
  const LOW_SCORE = { key: "LOW_SCORE", label: "Điểm tập trung thấp", tone: "warning", icon: "fa-chart-line",
                      action: "Quan sát thêm và hỗ trợ học sinh khi cần." };

  function attentionItems() {
    if (!S.data || !S.data.class_session_active) return [];
    const now = serverNow(), c = ctx(), out = [];
    S.data.students.forEach((s) => {
      const st = FG.studentStatus(s, c);
      if (FG.NEEDS_ATTENTION.indexOf(st.key) !== -1) {
        out.push({ s: s, st: st, seconds: st.since ? Math.max(0, now - st.since) : null });
      } else if (st.key === "FOCUSED" && FG.hasScore(s.focus_score) && s.focus_score < S.threshold) {
        out.push({ s: s, st: LOW_SCORE, seconds: null });
      }
    });
    return out.sort((a, b) => (b.seconds || 0) - (a.seconds || 0));
  }

  const statusLine = (st, seconds) => st.label + (seconds !== null && seconds !== undefined ? " · " + FG.duration(seconds) : "");
  const confidenceText = (c) => (c === null || c === undefined ? "" : "Độ tin cậy " + Math.round(c * 100) + "%");

  /* ------------------------------------------------------------ loading */
  async function load() {
    try {
      const data = await FG.api("/api/teacher/class_status" + classQuery());
      S.loadError = null;
      apply(data);
    } catch (e) {
      if (e.status === 401) return;
      S.loadError = e.message;
      renderLoadError();
    }
  }

  function apply(data) {
    if (!data || data.class_id !== S.classId) return;   // snapshot of another class
    const wasActive = S.data && S.data.class_session_active;
    S.data = data;
    S.receivedAt = performance.now();
    if (data.class_session_active) S.mode = data.mode;
    if (wasActive && !data.class_session_active) S.extraLogs = [];
    render();
  }

  function mergedLogs() {
    const logs = (S.data ? S.data.logs || [] : []).concat(S.extraLogs);
    return logs.slice().sort((a, b) => (b.time || 0) - (a.time || 0)).slice(0, 50);
  }

  function connect() {
    S.socket = FG.connectSocket({
      onConnect: (socket) => {
        socket.emit("join_teacher_room", { class_id: S.classId });
        socket.emit("request_class_snapshot", { class_id: S.classId });
      },
    });
    if (!S.socket) return;
    S.socket.on("class_snapshot", apply);
    S.socket.on("log_update", (entry) => {
      if (!entry || entry.class_id !== S.classId || entry.type !== "client_report") return;
      S.extraLogs.push(entry);
      if (S.extraLogs.length > 50) S.extraLogs.shift();
      renderLogs();
    });
  }

  /* ------------------------------------------------------------ renders */
  function render() {
    renderLiveStrip();
    const fn = PAGES[S.page];
    if (fn && fn.render) fn.render();
    if (S.panelStudentId !== null) renderPanelLive();
  }

  function renderLoadError() {
    if (S.data) { FG.setConnection("reconnecting"); return; }   // keep the last good snapshot on screen
    const fn = PAGES[S.page];
    if (fn && fn.error) fn.error(S.loadError);
  }

  function renderLiveStrip() {
    const strip = $("liveStrip");
    if (!strip || !S.data) return;
    strip.hidden = !S.data.class_session_active;
    if (!S.data.class_session_active) return;
    strip.querySelector("[data-live-timer]").textContent = FG.clock(elapsed());
    strip.querySelector("[data-live-note]").textContent = S.data.mode === "classroom"
      ? "Camera lớp học chỉ phân tích khi trang Giám sát đang mở. Điểm tạm dừng (không bị trừ) khi không có hình ảnh."
      : "Lớp trực tuyến vẫn tiếp tục ghi nhận trong khi bạn xem trang này.";
  }

  /* log entry -> display parts */
  function logParts(e) {
    const s = e.student_id !== undefined ? studentById(e.student_id) : null;
    const name = s ? s.name : null;
    const ep = e.episode || {};
    if (e.type === "behavior_started" || e.type === "behavior_ended") {
      const b = FG.behavior(ep.type);
      const ended = e.type === "behavior_ended";
      return {
        group: "behavior", tone: ended ? "neutral" : b.tone, icon: b.icon,
        title: (name || "Học sinh") + (ended ? " đã ngừng: " : ": ") + b.label.toLowerCase(),
        meta: ended ? "Kéo dài " + FG.duration(ep.duration) + (ep.confidence !== null && ep.confidence !== undefined ? " · " + confidenceText(ep.confidence) : "")
                    : (ep.confidence !== null && ep.confidence !== undefined ? confidenceText(ep.confidence) : ""),
      };
    }
    if (e.type === "attendance") return { group: "attendance", tone: "success", icon: "fa-user-check", title: e.message, meta: "" };
    if (e.type === "client_report") return { group: "behavior", tone: "info", icon: "fa-window-restore", title: e.message, meta: "Do trình duyệt của học sinh báo, không ảnh hưởng điểm" };
    if (e.type === "error") return { group: "system", tone: "danger", icon: "fa-triangle-exclamation", title: e.message, meta: "" };
    return { group: "system", tone: "neutral", icon: "fa-circle-info", title: e.message || "Hoạt động", meta: "" };
  }

  function logHTML(entries) {
    return '<ul class="fg-list">' + entries.map((e) => {
      const p = logParts(e);
      return '<li class="fg-event"><span class="fg-tone-icon" data-tone="' + p.tone + '"><i class="fa-solid ' + p.icon + '" aria-hidden="true"></i></span>' +
        '<div class="fg-event-main"><div class="fg-event-title">' + esc(p.title) + "</div>" +
        (p.meta ? '<div class="fg-event-meta">' + esc(p.meta) + "</div>" : "") + "</div>" +
        '<time class="fg-event-time">' + FG.time(e.time) + "</time></li>";
    }).join("") + "</ul>";
  }

  function renderLogs() {
    if (S.page === "dashboard") PAGES.dashboard.logs();
    if (S.page === "alerts") PAGES.alerts.render();
  }

  /* ───────────────────────────── Giám sát ───────────────────────────── */
  const dashboard = {
    init() {
      $("modeTabs").addEventListener("click", (e) => {
        const b = e.target.closest("[data-mode]");
        if (!b || b.disabled) return;
        S.mode = b.dataset.mode;
        dashboard.session();
        dashboard.camera();
      });
      $("startBtn").addEventListener("click", dashboard.start);
      $("stopBtn").addEventListener("click", dashboard.stop);
      $("studentFilter").addEventListener("click", (e) => {
        const b = e.target.closest("[data-filter]");
        if (!b) return;
        S.filter = b.dataset.filter;
        dashboard.students();
      });
      $("studentGrid").addEventListener("click", openPanelFromEvent);
      $("attentionList").addEventListener("click", openPanelFromEvent);
      const feed = $("cameraFeed");
      feed.addEventListener("error", () => { if (S.camera.src) { S.camera.error = true; dashboard.camera(); } });
      $("cameraState").addEventListener("click", (e) => { if (e.target.closest("[data-camera-retry]")) dashboard.cameraRestart(); });
      FG.skeleton($("attentionList"), 2);
      FG.skeleton($("studentGrid"), 3);
      FG.skeleton($("logList"), 3);
    },

    error(message) {
      $("sessionTitle").textContent = "Không tải được trạng thái lớp học";
      $("sessionMeta").textContent = message;
      const retry = () => { dashboard.init_loading(); load(); };
      FG.setState($("attentionList"), { error: true, compact: true, title: "Không tải được dữ liệu", text: message, retry: retry });
      FG.setState($("studentGrid"), { error: true, compact: true, title: "Không tải được danh sách học sinh", text: message, retry: retry });
      $("logList").innerHTML = "";
    },
    init_loading() { FG.skeleton($("attentionList"), 2); FG.skeleton($("studentGrid"), 3); },

    render() {
      dashboard.session();
      dashboard.metrics();
      dashboard.attention();
      dashboard.students();
      dashboard.camera();
      dashboard.logs();
    },

    session() {
      const d = S.data;
      if (!d) return;
      const active = d.class_session_active, enrolled = d.statistics.enrolled;
      $("sessionDot").classList.toggle("is-idle", !active);
      $("sessionTitle").textContent = active ? "Buổi học đang diễn ra" : "Chưa bắt đầu buổi học";
      $("sessionMeta").textContent = active
        ? "Lớp " + d.class_name + " · " + (d.mode_label || MODE_LABEL[d.mode] || "") + " · bắt đầu lúc " + FG.time(d.started_at).slice(0, 5)
        : enrolled ? "Lớp " + d.class_name + " · " + enrolled + " học sinh"
                   : "Lớp " + d.class_name + " chưa có học sinh. Quản trị viên cần thêm học sinh trước khi bắt đầu.";
      FG.$$("#modeTabs [data-mode]").forEach((b) => {
        const on = b.dataset.mode === S.mode;
        b.classList.toggle("is-active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
        b.disabled = active;
      });
      $("modeHelp").textContent = active
        ? "Muốn đổi hình thức, hãy kết thúc buổi học này rồi bắt đầu buổi mới."
        : S.mode === "classroom"
          ? "Camera lớp học: một camera đặt trong lớp quan sát cả lớp. Học sinh cần được đăng ký khuôn mặt để hệ thống nhận ra."
          : "Lớp trực tuyến: mỗi học sinh tự đăng nhập và bật camera trên máy của mình.";
      const timer = $("sessionTimer");
      timer.hidden = !active;
      if (active) timer.textContent = FG.clock(elapsed());
      const startBtn = $("startBtn"), stopBtn = $("stopBtn");
      startBtn.hidden = active; stopBtn.hidden = !active;
      if (!FG.isBusy(startBtn)) startBtn.disabled = !enrolled;
    },

    metrics() {
      const d = S.data, st = d.statistics, active = d.class_session_active;
      const set = (id, value, na) => { const el = $(id); el.textContent = value; el.classList.toggle("is-na", !!na); };
      if (!active) {
        set("mPresent", "—", true); $("mPresentSub").textContent = "Sĩ số " + st.enrolled + " học sinh";
        set("mScore", "—", true); $("mScoreSub").textContent = "Chưa bắt đầu buổi học";
        set("mAttention", "—", true); $("mAttentionSub").textContent = "Chưa bắt đầu buổi học";
        set("mVisible", "—", true); $("mVisibleSub").textContent = "Chưa bắt đầu buổi học";
        return;
      }
      const late = d.students.filter((s) => s.attendance_status === "LATE").length;
      set("mPresent", st.present + "/" + st.enrolled);
      $("mPresentSub").textContent = late ? late + " đi muộn" : st.present ? "Không ai đi muộn" : "Chưa ghi nhận học sinh nào";
      const hasAvg = FG.hasScore(st.average_focus_score);
      set("mScore", hasAvg ? st.average_focus_score : "Chưa có", !hasAvg);
      $("mScoreSub").textContent = hasAvg ? "Đo được cho " + st.measured_students + " học sinh" : "Chưa đủ dữ liệu để tính điểm";
      const items = attentionItems();
      // A classroom camera that is not analysing means "unknown", not "everyone is fine".
      const blind = d.mode === "classroom" && (!d.camera || d.camera.state !== "ACTIVE");
      if (blind && !items.length) {
        set("mAttention", "—", true); $("mAttentionSub").textContent = "Chưa có dữ liệu từ camera";
        set("mVisible", "—", true); $("mVisibleSub").textContent = d.camera ? d.camera.label : "Camera chưa hoạt động";
        return;
      }
      set("mAttention", String(items.length));
      const parts = [];
      if (st.phone) parts.push(st.phone + " điện thoại");
      if (st.drowsy) parts.push(st.drowsy + " buồn ngủ");
      if (st.head_away) parts.push(st.head_away + " quay đi");
      if (st.away) parts.push(st.away + " rời chỗ");
      $("mAttentionSub").textContent = parts.length ? parts.join(" · ") : items.length ? "Điểm tập trung thấp" : "Cả lớp đang ổn";
      if (d.mode === "online") {
        const online = d.students.filter((s) => s.connection_status === "ONLINE").length;
        set("mVisible", online + "/" + st.enrolled);
        $("mVisibleLabel").textContent = "Đã vào lớp";
        $("mVisibleSub").textContent = online < st.enrolled ? (st.enrolled - online) + " học sinh chưa vào" : "Tất cả đã vào lớp";
      } else {
        set("mVisible", st.visible + "/" + st.enrolled);
        $("mVisibleLabel").textContent = "Đang trong khung hình";
        $("mVisibleSub").textContent = st.away ? st.away + " học sinh rời chỗ" : "Không ai rời chỗ";
      }
    },

    attention() {
      const box = $("attentionList"), d = S.data;
      const count = $("attentionCount");
      if (!d.class_session_active) {
        count.textContent = "";
        FG.setState(box, { compact: true, icon: "fa-circle-play", title: "Chưa có buổi học",
          text: "Bắt đầu buổi học để biết học sinh nào đang cần được hỗ trợ." });
        return;
      }
      const items = attentionItems();
      count.textContent = items.length ? items.length + " học sinh" : "";
      if (!items.length) {
        const cam = d.camera ? d.camera.state : null;
        if (d.mode === "classroom" && cam !== "ACTIVE") {
          FG.setState(box, { compact: true, icon: "fa-video-slash", title: "Chưa có dữ liệu từ camera",
            text: "Danh sách sẽ xuất hiện khi camera bắt đầu phân tích." });
        } else {
          FG.setState(box, { compact: true, icon: "fa-circle-check", title: "Không có học sinh nào cần chú ý",
            text: "Danh sách tự cập nhật khi hệ thống ghi nhận hành vi kéo dài." });
        }
        return;
      }
      if (!box.firstElementChild || !box.firstElementChild.dataset.key) box.innerHTML = "";
      renderKeyed(box, items, (it) => {
        const conf = confidenceText(it.st.confidence);
        return {
          key: "a" + it.s.student_id, tag: "button", cls: "fg-attention",
          attrs: { "data-tone": it.st.tone, "data-student": it.s.student_id,
                   "aria-label": it.s.name + ": " + statusLine(it.st, it.seconds) + ". Mở chi tiết." },
          html: '<span class="fg-tone-icon"><i class="fa-solid ' + it.st.icon + '" aria-hidden="true"></i></span>' +
            '<span class="fg-attention-main"><span class="fg-attention-name d-block">' + esc(it.s.name) + "</span>" +
            '<span class="fg-attention-what d-block">' + esc(statusLine(it.st, it.seconds)) + "</span>" +
            '<span class="fg-attention-hint d-block">' + esc(it.st.action || "") + (conf ? " · " + conf : "") + "</span></span>" +
            '<i class="fa-solid fa-chevron-right fg-muted" aria-hidden="true"></i>',
        };
      });
    },

    students() {
      const box = $("studentGrid"), d = S.data, c = ctx(), now = serverNow();
      const rows = d.students.map((s) => {
        const st = FG.studentStatus(s, c);
        return { s: s, st: st, seconds: st.since ? Math.max(0, now - st.since) : null };
      });
      const isAttention = (r) => FG.NEEDS_ATTENTION.indexOf(r.st.key) !== -1 ||
        (r.st.key === "FOCUSED" && FG.hasScore(r.s.focus_score) && r.s.focus_score < S.threshold);
      const isMissing = (r) => ["NOT_SEEN", "OFFLINE", "NO_SIGNAL", "TEMPORARILY_NOT_VISIBLE"].indexOf(r.st.key) !== -1;
      const counts = { all: rows.length, attention: rows.filter(isAttention).length, missing: rows.filter(isMissing).length };
      const labels = { all: "Tất cả", attention: "Cần chú ý", missing: "Chưa thấy" };
      FG.$$("#studentFilter [data-filter]").forEach((b) => {
        const on = b.dataset.filter === S.filter;
        b.classList.toggle("is-active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
        b.textContent = labels[b.dataset.filter] + (d.class_session_active || b.dataset.filter === "all" ? " (" + counts[b.dataset.filter] + ")" : "");
      });
      if (!rows.length) {
        FG.setState(box, { icon: "fa-user-group", title: "Lớp chưa có học sinh",
          text: "Quản trị viên cần thêm học sinh vào lớp này trước khi bắt đầu buổi học." });
        return;
      }
      const shown = S.filter === "attention" ? rows.filter(isAttention) : S.filter === "missing" ? rows.filter(isMissing) : rows;
      if (!shown.length) {
        FG.setState(box, { compact: true, icon: "fa-circle-check",
          title: S.filter === "attention" ? "Không có học sinh nào cần chú ý" : "Tất cả học sinh đã được ghi nhận" });
        return;
      }
      let grid = box.querySelector(".fg-students");
      if (!grid) { box.innerHTML = '<div class="fg-students"></div>'; grid = box.firstElementChild; }
      renderKeyed(grid, shown, (r) => {
        const s = r.s, has = FG.hasScore(s.focus_score);
        const att = FG.attendance(s.attendance_status);
        return {
          key: "s" + s.student_id, tag: "button", cls: "fg-student" + (d.class_session_active ? "" : " is-idle"),
          attrs: { "data-tone": r.st.tone, "data-student": s.student_id,
                   "aria-label": s.name + ": " + statusLine(r.st, r.seconds) + (has ? ", điểm " + s.focus_score : ", chưa có điểm") + ". Mở chi tiết." },
          html: '<span class="fg-student-top"><span class="fg-avatar" aria-hidden="true">' + esc(FG.initials(s.name)) + "</span>" +
            '<span class="fg-student-name">' + esc(s.name) + "</span>" +
            '<span class="fg-student-score' + (has ? "" : " is-na") + '">' + (has ? s.focus_score : "—") + "</span></span>" +
            (d.class_session_active
              ? "<span>" + FG.badge(r.st.label, r.st.tone, r.st.icon) + "</span>" +
                '<span class="fg-student-meta"><span>' + esc(att.label) + "</span>" +
                "<span>" + (r.seconds !== null ? esc(FG.duration(r.seconds)) : "") + "</span></span>"
              : ""),
        };
      });
    },

    cameraRestart() {
      const feed = $("cameraFeed");
      S.camera.error = false;
      S.camera.startedAt = performance.now();
      S.camera.src = true;
      feed.src = "/video_feed" + classQuery("&mode=classroom&t=" + Date.now());
      dashboard.camera();
    },

    camera() {
      const d = S.data, feed = $("cameraFeed"), overlay = $("cameraState"), badge = $("cameraBadge");
      if (!d) return;
      const setBadge = (tone, icon, label) => { badge.dataset.tone = tone; badge.innerHTML = '<i class="fa-solid ' + icon + '" aria-hidden="true"></i>' + esc(label); };
      const show = (icon, title, text, retry, spinner) => {
        const sig = [icon, title, text, retry, spinner].join("|");
        overlay.hidden = false;
        if (overlay._sig === sig) return;
        overlay._sig = sig;
        overlay.innerHTML = (spinner ? '<span class="spinner-border text-light" aria-hidden="true"></span>'
                                     : '<i class="fa-solid ' + icon + '" aria-hidden="true"></i>') +
          '<div class="fg-camera-state-title">' + esc(title) + "</div>" +
          '<p class="fg-camera-state-text">' + esc(text) + "</p>" +
          (retry ? '<button type="button" class="btn btn-light btn-sm mt-1" data-camera-retry><i class="fa-solid fa-rotate-right" aria-hidden="true"></i>' + esc(retry) + "</button>" : "");
      };
      const stopFeed = () => { if (S.camera.src) { feed.removeAttribute("src"); S.camera.src = false; } feed.hidden = true; S.camera.error = false; };

      if (!d.class_session_active) {
        stopFeed();
        setBadge("neutral", "fa-video-slash", "Chưa bật");
        show("fa-video-slash", "Camera chưa bật", S.mode === "classroom"
          ? "Bắt đầu buổi học ở chế độ Camera lớp học để bật camera."
          : "Lớp trực tuyến không dùng camera chung của lớp.");
        return;
      }
      if (d.mode !== "classroom") {
        stopFeed();
        setBadge("info", "fa-desktop", "Lớp trực tuyến");
        show("fa-desktop", "Không có camera chung", "Ở lớp trực tuyến, mỗi học sinh dùng camera trên máy của mình. Giáo viên xem trạng thái ở danh sách học sinh.");
        return;
      }
      if (!S.camera.src && !S.camera.error) { dashboard.cameraRestart(); return; }
      const cam = d.camera || { state: "STARTING", label: "Đang khởi động camera", help: "" };
      const waited = (performance.now() - S.camera.startedAt) / 1000;
      if (S.camera.error) {
        feed.hidden = true;
        setBadge("danger", "fa-video-slash", "Không có hình ảnh");
        show("fa-video-slash", "Không tải được hình ảnh camera", "Kết nối tới camera bị ngắt. Điểm tập trung tạm dừng, không bị trừ.", "Thử lại");
        return;
      }
      switch (cam.state) {
        case "ACTIVE":
          feed.hidden = false; overlay.hidden = true; overlay._sig = null;
          setBadge("success", "fa-video", "Đang hoạt động");
          break;
        case "STARTING":
          feed.hidden = true;
          setBadge("info", "fa-hourglass-half", "Đang khởi động");
          if (waited > 30) show("fa-hourglass-half", "Camera khởi động lâu hơn bình thường", "Kiểm tra camera đã được cắm và không bị ứng dụng khác sử dụng.", "Thử lại");
          else show("", cam.label, cam.help, null, true);
          break;
        case "NOT_STREAMING":
          feed.hidden = true;
          setBadge("warning", "fa-video-slash", "Chưa có hình ảnh");
          if (waited > 6) show("fa-video-slash", cam.label, "Camera chưa gửi hình ảnh về trang này.", "Mở camera");
          else show("", "Đang kết nối camera", "Vui lòng chờ trong giây lát.", null, true);
          break;
        case "NO_SIGNAL":
          feed.hidden = true;
          setBadge("warning", "fa-video-slash", "Mất tín hiệu");
          show("fa-video-slash", cam.label, cam.help, "Thử lại");
          break;
        default: /* UNAVAILABLE */
          feed.hidden = true;
          setBadge("danger", "fa-video-slash", "Không dùng được");
          show("fa-video-slash", cam.label, cam.help, "Thử lại");
      }
    },

    logs() {
      const box = $("logList");
      if (!box || !S.data) return;
      const logs = mergedLogs().slice(0, 12);
      if (!logs.length) {
        FG.setState(box, { compact: true, icon: "fa-list",
          title: S.data.class_session_active ? "Chưa có hoạt động nào" : "Chưa có buổi học",
          text: S.data.class_session_active ? "Điểm danh và hành vi được ghi nhận sẽ hiện ở đây." : "Nhật ký sẽ hiện khi buổi học bắt đầu." });
        return;
      }
      const html = logHTML(logs);
      if (box._sig !== html) { box.innerHTML = html; box._sig = html; }
    },

    async start() {
      const btn = $("startBtn");
      if (FG.isBusy(btn)) return;
      FG.busy(btn, true, "Đang bắt đầu…");
      try {
        const url = S.mode === "classroom" ? "/api/teacher/start_offline_class" : "/api/teacher/start_class";
        const res = await FG.api(url, { body: { class_id: S.classId } });
        FG.toast(res.already_active ? "Buổi học đã được bắt đầu trước đó." : "Đã bắt đầu buổi học.", res.already_active ? "info" : "success");
        await load();
      } catch (e) {
        FG.toast(e.message, "danger");
        await load();
      } finally {
        FG.busy(btn, false);
        dashboard.session();
      }
    },

    async stop() {
      const btn = $("stopBtn");
      if (FG.isBusy(btn) || !S.data) return;
      const mode = S.data.mode;
      const ok = await FG.confirm({
        title: "Kết thúc buổi học?",
        body: "Buổi học của lớp " + S.data.class_name + " sẽ dừng ngay cho tất cả học sinh.",
        consequences: [
          "Điểm danh và điểm tập trung được chốt và lưu vào Lịch sử buổi học.",
          "Học sinh chưa được ghi nhận sẽ được tính là vắng mặt.",
          "Không thể tiếp tục lại buổi học này sau khi kết thúc.",
        ],
        confirmLabel: "Kết thúc buổi học", danger: true,
      });
      if (!ok) return;
      FG.busy(btn, true, "Đang kết thúc…");
      try {
        const url = mode === "classroom" ? "/api/teacher/end_offline_class" : "/api/teacher/end_class";
        const res = await FG.api(url, { body: { class_id: S.classId } });
        await load();
        if (res.was_active && res.class_session_id) {
          FG.toast("Đã kết thúc buổi học.", "success");
          openSession(res.class_session_id, "Tổng kết buổi học");
        } else {
          FG.toast("Buổi học đã được kết thúc trước đó.", "info");
        }
      } catch (e) {
        FG.toast(e.message, "danger");
      } finally {
        FG.busy(btn, false);
        if (S.data) dashboard.session();
      }
    },

    tick() {
      if (!S.data || !S.data.class_session_active) return;
      $("sessionTimer").textContent = FG.clock(elapsed());
      dashboard.attention();
      dashboard.students();
      dashboard.camera();
    },
  };

  /* ───────────────────────────── Học sinh ───────────────────────────── */
  const students = {
    init() {
      $("rosterSearch").addEventListener("input", students.render);
      $("rosterBody").addEventListener("click", openPanelFromEvent);
      $("rosterBody").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { const r = e.target.closest("tr[data-student]"); if (r) { e.preventDefault(); openPanel(Number(r.dataset.student)); } } });
      FG.skeleton($("rosterBody"), 5);
    },
    error(message) { FG.setState($("rosterBody"), { error: true, title: "Không tải được danh sách học sinh", text: message, retry: () => { FG.skeleton($("rosterBody"), 5); load(); } }); },
    render() {
      const box = $("rosterBody"), d = S.data, c = ctx(), now = serverNow();
      if (!d) return;
      if (!d.students.length) {
        FG.setState(box, { icon: "fa-user-group", title: "Lớp chưa có học sinh", text: "Quản trị viên cần thêm học sinh vào lớp này." });
        return;
      }
      const q = $("rosterSearch").value.trim().toLowerCase();
      const list = d.students.filter((s) => !q || String(s.name || "").toLowerCase().indexOf(q) !== -1);
      $("rosterSub").textContent = d.class_session_active
        ? "Trạng thái trực tiếp của buổi học đang diễn ra. Chọn một học sinh để xem chi tiết."
        : "Chưa có buổi học đang diễn ra. Chọn một học sinh để xem lịch sử.";
      if (!list.length) {
        FG.setState(box, { compact: true, icon: "fa-magnifying-glass", title: "Không tìm thấy học sinh", text: "Thử tìm với tên khác." });
        return;
      }
      const html = '<div class="fg-table-wrap"><table class="fg-table is-stack"><thead><tr><th>Học sinh</th><th>Trạng thái</th><th>Điểm danh</th>' +
        '<th class="is-num">Điểm hiện tại</th><th class="is-num">Điểm trung bình</th><th class="is-num">Sự kiện</th></tr></thead><tbody>' +
        list.map((s) => {
          const st = FG.studentStatus(s, c), att = FG.attendance(s.attendance_status);
          const secs = st.since ? Math.max(0, now - st.since) : null;
          return '<tr class="is-clickable" tabindex="0" data-student="' + s.student_id + '">' +
            '<td class="is-primary"><div class="fg-person"><span class="fg-avatar" aria-hidden="true">' + esc(FG.initials(s.name)) + "</span>" +
            '<span class="fg-person-name">' + esc(s.name) + "</span></div></td>" +
            '<td data-label="Trạng thái">' + FG.badge(statusLine(st, secs), st.tone, st.icon) + "</td>" +
            '<td data-label="Điểm danh">' + (d.class_session_active ? FG.badge(att.label, att.tone, att.icon) : '<span class="fg-muted">—</span>') + "</td>" +
            '<td class="is-num" data-label="Điểm hiện tại">' + (FG.hasScore(s.focus_score) ? s.focus_score : '<span class="fg-muted">Chưa có</span>') + "</td>" +
            '<td class="is-num" data-label="Điểm trung bình">' + (FG.hasScore(s.average_focus_score) ? s.average_focus_score : '<span class="fg-muted">Chưa có</span>') + "</td>" +
            '<td class="is-num" data-label="Sự kiện">' + (d.class_session_active ? s.distractions : '<span class="fg-muted">—</span>') + "</td></tr>";
        }).join("") + "</tbody></table></div>";
      if (box._sig !== html) {
        const focused = document.activeElement && document.activeElement.closest ? document.activeElement.closest("tr[data-student]") : null;
        const keep = focused ? focused.dataset.student : null;
        box.innerHTML = html; box._sig = html;
        if (keep) { const row = box.querySelector('tr[data-student="' + keep + '"]'); if (row) row.focus(); }
      }
    },
  };

  /* ───────────────────────────── Điểm danh ───────────────────────────── */
  const attendance = {
    sessions: [], selected: "live", detail: null, rows: [],
    async init() {
      $("attSession").addEventListener("change", (e) => { attendance.selected = e.target.value; attendance.loadSelected(); });
      $("attExport").addEventListener("click", attendance.exportCsv);
      FG.skeleton($("attBody"), 5);
      try {
        const res = await FG.api("/api/teacher/class_sessions" + classQuery());
        attendance.sessions = res.sessions || [];
      } catch (e) { attendance.sessions = []; attendance.historyError = e.message; }
      attendance.options();
      attendance.render();
    },
    error(message) { FG.setState($("attBody"), { error: true, title: "Không tải được điểm danh", text: message, retry: () => { FG.skeleton($("attBody"), 5); load(); } }); },
    options() {
      const live = S.data && S.data.class_session_active;
      const sel = $("attSession");
      if (!live && attendance.selected === "live" && attendance.sessions.length) attendance.selected = String(attendance.sessions[0].class_session_id);
      const html = (live ? '<option value="live">Buổi đang diễn ra</option>' : "") +
        attendance.sessions.map((s) => '<option value="' + s.class_session_id + '">' + esc(FG.dateTime(s.started_at)) + " · " + esc(MODE_LABEL[s.mode] || s.mode || "") + "</option>").join("");
      if (sel._sig !== html) { sel.innerHTML = html; sel._sig = html; }
      sel.hidden = !html;
      if (html) sel.value = attendance.selected;
      if (attendance.selected !== "live" && (!attendance.detail || String(attendance.detail.class_session_id) !== attendance.selected)) attendance.loadSelected();
    },
    async loadSelected() {
      if (attendance.selected === "live") { attendance.detail = null; attendance.render(); return; }
      FG.skeleton($("attBody"), 5);
      try {
        attendance.detail = await FG.api("/api/teacher/class_session/" + encodeURIComponent(attendance.selected));
        attendance.render();
      } catch (e) {
        FG.setState($("attBody"), { error: true, title: "Không tải được buổi học", text: e.message, retry: attendance.loadSelected });
      }
    },
    render() {
      const d = S.data, box = $("attBody");
      if (!d) return;
      const live = d.class_session_active;
      if (live && !$("attSession").querySelector('option[value="live"]')) { attendance.selected = "live"; attendance.options(); }
      if (!live && attendance.selected === "live") attendance.options();
      const set = (id, v) => { $(id).textContent = v; };
      let rows = [], sub = "";
      if (attendance.selected === "live" && live) {
        rows = d.students.map((s) => ({ name: s.name, status: s.attendance_status, checkin: s.checkin_offset_seconds,
          visible: s.visible_seconds, away_count: s.away_count, away_seconds: s.away_seconds }));
        sub = "Buổi đang diễn ra · cập nhật trực tiếp";
        $("aAbsentLabel").textContent = "Chưa điểm danh";
      } else if (attendance.selected !== "live" && attendance.detail) {
        rows = attendance.detail.students.map((s) => ({ name: s.name, status: s.attendance_status, checkin: null,
          visible: null, away_count: s.away_count, away_seconds: s.away_seconds }));
        sub = "Buổi học " + FG.dateTime(attendance.detail.started_at) + " · đã kết thúc";
        $("aAbsentLabel").textContent = "Vắng mặt";
      } else if (attendance.selected !== "live") {
        return;   // detail still loading
      } else {
        ["aEnrolled", "aPresent", "aLate", "aAbsent"].forEach((id) => set(id, "—"));
        set("aEnrolled", d.statistics.enrolled);
        $("attSub").textContent = "";
        $("attExport").disabled = true;
        FG.setState(box, { icon: "fa-clipboard-check", title: "Chưa có buổi học nào để điểm danh",
          text: attendance.historyError || "Bắt đầu buổi học ở trang Giám sát. Điểm danh được ghi nhận tự động khi camera nhận ra học sinh." });
        return;
      }
      attendance.rows = rows;
      const count = (k) => rows.filter((r) => r.status === k).length;
      set("aEnrolled", rows.length);
      set("aPresent", count("PRESENT") + count("LATE"));
      set("aLate", count("LATE"));
      set("aAbsent", attendance.selected === "live" ? count("NOT_YET") : count("ABSENT"));
      $("attSub").textContent = sub;
      $("attExport").disabled = !rows.length;
      const isLive = attendance.selected === "live";
      const html = '<div class="fg-table-wrap"><table class="fg-table is-stack"><thead><tr><th>Học sinh</th><th>Điểm danh</th>' +
        (isLive ? "<th>Vào lớp sau</th><th class=\"is-num\">Thời gian trong khung hình</th>" : "") +
        '<th class="is-num">Số lần rời chỗ</th><th class="is-num">Thời gian rời chỗ</th></tr></thead><tbody>' +
        rows.map((r) => {
          const att = FG.attendance(r.status);
          return '<tr><td class="is-primary"><span class="fg-person-name">' + esc(r.name) + "</span></td>" +
            '<td data-label="Điểm danh">' + FG.badge(att.label, att.tone, att.icon) + "</td>" +
            (isLive ? '<td data-label="Vào lớp sau">' + (r.checkin === null || r.checkin === undefined ? '<span class="fg-muted">—</span>' : esc(FG.duration(r.checkin))) + "</td>" +
                      '<td class="is-num" data-label="Trong khung hình">' + esc(FG.duration(r.visible)) + "</td>" : "") +
            '<td class="is-num" data-label="Số lần rời chỗ">' + (r.away_count || 0) + "</td>" +
            '<td class="is-num" data-label="Thời gian rời chỗ">' + (r.away_seconds ? esc(FG.duration(r.away_seconds)) : "0 giây") + "</td></tr>";
        }).join("") + "</tbody></table></div>";
      if (box._sig !== html) { box.innerHTML = html; box._sig = html; }
    },
    exportCsv() {
      const rows = [["Học sinh", "Điểm danh", "Số lần rời chỗ", "Thời gian rời chỗ (giây)"]].concat(
        attendance.rows.map((r) => [r.name, FG.attendance(r.status).label, r.away_count || 0, r.away_seconds || 0]));
      downloadCsv("diem-danh-" + BOOT.class_name + ".csv", rows);
    },
  };

  /* ───────────────────────────── Cảnh báo ───────────────────────────── */
  const alerts = {
    init() {
      $("alertFilter").addEventListener("click", (e) => {
        const b = e.target.closest("[data-filter]");
        if (!b) return;
        S.alertFilter = b.dataset.filter;
        FG.$$("#alertFilter [data-filter]").forEach((x) => { const on = x === b; x.classList.toggle("is-active", on); x.setAttribute("aria-pressed", on ? "true" : "false"); });
        alerts.render();
      });
      FG.skeleton($("alertsBody"), 5);
    },
    error(message) { FG.setState($("alertsBody"), { error: true, title: "Không tải được nhật ký", text: message, retry: () => { FG.skeleton($("alertsBody"), 5); load(); } }); },
    render() {
      const box = $("alertsBody"), d = S.data;
      if (!d) return;
      $("alertsSub").textContent = d.class_session_active ? "Lớp " + d.class_name + " · cập nhật trực tiếp" : "Lớp " + d.class_name;
      if (!d.class_session_active) {
        FG.setState(box, { icon: "fa-bell", title: "Chưa có buổi học đang diễn ra",
          text: "Nhật ký hiện khi buổi học bắt đầu. Xem các buổi trước ở Lịch sử buổi học." });
        box._sig = null;
        return;
      }
      const logs = mergedLogs().filter((e) => S.alertFilter === "all" || logParts(e).group === S.alertFilter);
      if (!logs.length) {
        FG.setState(box, { compact: true, icon: "fa-list", title: "Chưa có hoạt động nào",
          text: S.alertFilter === "all" ? "Điểm danh và hành vi được ghi nhận sẽ hiện ở đây." : "Không có hoạt động nào thuộc nhóm này." });
        box._sig = null;
        return;
      }
      const html = logHTML(logs);
      if (box._sig !== html) { box.innerHTML = html; box._sig = html; }
    },
  };

  /* ───────────────────────────── Phân tích ───────────────────────────── */
  const analytics = {
    charts: {},
    async init() {
      document.addEventListener("fg:theme", () => { FG.chartDefaults(); analytics.load(); });
      analytics.load();
    },
    render() {},
    async load() {
      const setText = (id, v) => { $(id).textContent = v; };
      let summary, history, leaders;
      try {
        [summary, history, leaders] = await Promise.all([
          FG.api("/api/teacher/analytics_summary" + classQuery()),
          FG.api("/api/teacher/class_sessions" + classQuery()),
          FG.api("/api/leaderboard" + classQuery()),
        ]);
      } catch (e) {
        ["trendBody", "breakdownBody", "watchBody", "leaderBody"].forEach((id) =>
          FG.setState($(id), { error: true, compact: true, title: "Không tải được dữ liệu", text: e.message, retry: () => window.location.reload() }));
        return;
      }
      const sessions = (history.sessions || []).slice().reverse();     // oldest first
      const measured = sessions.filter((s) => FG.hasScore(s.average_score));
      setText("anSessions", summary.class_sessions);
      if (measured.length) {
        const avg = Math.round(measured.reduce((a, s) => a + s.average_score, 0) / measured.length);
        setText("anAverage", avg);
        setText("anAverageSub", "Trên " + measured.length + " buổi có điểm");
      } else {
        $("anAverage").textContent = "Chưa có"; $("anAverage").classList.add("is-na");
        setText("anAverageSub", "Chưa có buổi học nào có điểm");
      }
      const dh = summary.danger_hour_detail || {};
      if (dh.status === "ok") { setText("anHour", dh.label); setText("anHourSub", "Điểm trung bình " + dh.average_score + "/100"); }
      else { $("anHour").textContent = "Chưa đủ dữ liệu"; $("anHour").classList.add("is-na"); setText("anHourSub", "Cần thêm dữ liệu theo giờ"); }
      const bd = summary.breakdown || {};
      setText("anEvents", bd.total || 0);

      // trend
      if (measured.length < 2) {
        FG.setState($("trendBody"), { compact: true, icon: "fa-chart-line", title: "Chưa đủ dữ liệu để vẽ xu hướng",
          text: measured.length ? "Cần ít nhất 2 buổi học có điểm. Hiện có 1 buổi." : "Biểu đồ xuất hiện sau khi lớp có ít nhất 2 buổi học đã kết thúc và có điểm." });
      } else {
        analytics.draw("trendChart", "trendBody", {
          type: "line",
          data: { labels: measured.map((s) => FG.dateTime(s.started_at)),
                  datasets: [{ label: "Điểm trung bình", data: measured.map((s) => s.average_score),
                               borderColor: FG.cssVar("--fg-primary"), backgroundColor: FG.cssVar("--fg-primary"), tension: 0.25, pointRadius: 4 }] },
          options: { maintainAspectRatio: false, scales: { y: { min: 0, max: 100, title: { display: true, text: "Điểm (0–100)" } } },
                     plugins: { legend: { display: false } } },
        });
      }

      // breakdown
      const types = ["PHONE", "DROWSY", "HEAD_AWAY", "AWAY"];
      if (!bd.total) {
        FG.setState($("breakdownBody"), { compact: true, icon: "fa-chart-column", title: "Chưa ghi nhận hành vi nào",
          text: summary.class_sessions ? "Các buổi học đã kết thúc không ghi nhận hành vi kéo dài nào." : "Biểu đồ xuất hiện sau buổi học đầu tiên." });
      } else {
        analytics.draw("breakdownChart", "breakdownBody", {
          type: "bar",
          data: { labels: types.map((t) => FG.behavior(t).label),
                  datasets: [{ label: "Số lần", data: types.map((t) => bd.counts[t] || 0),
                               backgroundColor: types.map((t) => FG.cssVar("--fg-" + FG.behavior(t).tone)) }] },
          options: { maintainAspectRatio: false, scales: { y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: "Số lần ghi nhận" } } },
                     plugins: { legend: { display: false } } },
        });
      }

      // watch list
      const watch = summary.watchlist || [];
      if (!watch.length) {
        FG.setState($("watchBody"), { compact: true, icon: "fa-circle-check", title: "Chưa có học sinh nào trong danh sách",
          text: summary.class_sessions >= 2 ? "Không học sinh nào có điểm trung bình dưới 70 qua các buổi đã đo." : "Cần ít nhất 2 buổi học có điểm cho mỗi học sinh." });
      } else {
        $("watchBody").innerHTML = '<ul class="fg-list">' + watch.map((w) =>
          '<li class="fg-event"><span class="fg-avatar" aria-hidden="true">' + esc(FG.initials(w.display_name)) + "</span>" +
          '<div class="fg-event-main"><div class="fg-event-title">' + esc(w.display_name) + "</div>" +
          '<div class="fg-event-meta">Trung bình trên ' + w.measured_sessions + " buổi</div></div>" +
          FG.badge(w.score + "/100", FG.scoreTone(w.score)) + "</li>").join("") + "</ul>";
      }

      // today
      const list = Array.isArray(leaders) ? leaders : [];
      if (!list.length) {
        FG.setState($("leaderBody"), { compact: true, icon: "fa-calendar-day", title: "Hôm nay chưa có buổi học nào kết thúc",
          text: "Điểm trung bình trong ngày hiện sau khi buổi học kết thúc." });
      } else {
        $("leaderBody").innerHTML = '<ul class="fg-list">' + list.map((w, i) =>
          '<li class="fg-event"><span class="fg-avatar" aria-hidden="true">' + (i + 1) + "</span>" +
          '<div class="fg-event-main"><div class="fg-event-title">' + esc(w.display_name) + "</div></div>" +
          FG.badge(w.score + "/100", FG.scoreTone(w.score)) + "</li>").join("") + "</ul>";
      }
    },
    draw(canvasId, bodyId, config) {
      if (!window.Chart) { FG.setState($(bodyId), { error: true, compact: true, title: "Không tải được thư viện biểu đồ" }); return; }
      if (analytics.charts[canvasId]) analytics.charts[canvasId].destroy();
      let canvas = $(canvasId);
      if (!canvas) {
        $(bodyId).innerHTML = '<div class="fg-chart"><canvas id="' + canvasId + '" role="img"></canvas></div>';
        canvas = $(canvasId);
      }
      analytics.charts[canvasId] = new window.Chart(canvas, config);
    },
  };

  /* ───────────────────────────── Lịch sử buổi học ───────────────────────────── */
  const reports = {
    async init() {
      $("historyBody").addEventListener("click", (e) => { const r = e.target.closest("[data-session]"); if (r) openSession(Number(r.dataset.session), "Chi tiết buổi học"); });
      $("historyBody").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { const r = e.target.closest("tr[data-session]"); if (r) { e.preventDefault(); openSession(Number(r.dataset.session), "Chi tiết buổi học"); } } });
      reports.load();
    },
    render() {},
    async load() {
      const box = $("historyBody");
      FG.skeleton(box, 5);
      try {
        const res = await FG.api("/api/teacher/class_sessions" + classQuery());
        const list = res.sessions || [];
        if (!list.length) {
          FG.setState(box, { icon: "fa-clock-rotate-left", title: "Chưa có buổi học nào đã kết thúc",
            text: "Sau khi kết thúc một buổi học ở trang Giám sát, tổng kết của buổi đó sẽ được lưu ở đây." });
          return;
        }
        box.innerHTML = '<div class="fg-table-wrap"><table class="fg-table is-stack"><thead><tr><th>Thời gian</th><th>Hình thức</th><th class="is-num">Thời lượng</th>' +
          '<th class="is-num">Có mặt</th><th class="is-num">Điểm trung bình</th><th class="is-num">Sự kiện</th><th><span class="fg-sr-only">Thao tác</span></th></tr></thead><tbody>' +
          list.map((s) => '<tr class="is-clickable" tabindex="0" data-session="' + s.class_session_id + '">' +
            '<td class="is-primary"><span class="fg-person-name">' + esc(FG.dateTime(s.started_at)) + "</span></td>" +
            '<td data-label="Hình thức">' + esc(MODE_LABEL[s.mode] || s.mode || "—") + "</td>" +
            '<td class="is-num" data-label="Thời lượng">' + esc(FG.duration(s.duration_seconds)) + "</td>" +
            '<td class="is-num" data-label="Có mặt">' + s.present + "/" + s.enrolled + "</td>" +
            '<td class="is-num" data-label="Điểm trung bình">' + (FG.hasScore(s.average_score) ? s.average_score : '<span class="fg-muted">Chưa có</span>') + "</td>" +
            '<td class="is-num" data-label="Sự kiện">' + s.events_total + "</td>" +
            '<td class="is-actions"><span class="btn btn-secondary btn-sm">Xem chi tiết</span></td></tr>').join("") +
          "</tbody></table></div>";
      } catch (e) {
        FG.setState(box, { error: true, title: "Không tải được lịch sử buổi học", text: e.message, retry: reports.load });
      }
    },
  };

  /* ───────────────────────────── Cài đặt ───────────────────────────── */
  const settings = {
    init() {
      const form = $("settingsForm"), name = $("displayName");
      name.addEventListener("input", () => settings.validate());
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const btn = $("settingsSave");
        if (FG.isBusy(btn) || !settings.validate()) { name.focus(); return; }
        FG.busy(btn, true, "Đang lưu…");
        try {
          const res = await FG.api("/api/teacher/settings", { body: { display_name: name.value.trim(), min_focus_threshold: $("minFocus").value } });
          name.value = res.display_name; $("minFocus").value = res.min_focus_threshold;
          S.threshold = res.min_focus_threshold;
          FG.$$("[data-fg-user-name]").forEach((el) => { el.textContent = res.display_name; });
          FG.toast("Đã lưu cài đặt.", "success");
        } catch (err) { FG.toast(err.message, "danger"); }
        finally { FG.busy(btn, false); }
      });
      settings.thresholds();
    },
    validate() {
      const name = $("displayName"), bad = !name.value.trim();
      name.classList.toggle("is-invalid", bad);
      name.setAttribute("aria-invalid", bad ? "true" : "false");
      $("displayNameError").hidden = !bad;
      return !bad;
    },
    render() {},
    async thresholds() {
      const box = $("thresholdBody");
      FG.skeleton(box, 3);
      try {
        const t = await FG.api("/api/settings");
        box.innerHTML = '<dl class="fg-kv">' +
          "<dt>" + FG.badge("Dùng điện thoại", "phone", "fa-mobile-screen") + "</dt><dd>Kéo dài từ " + esc(t.phone_threshold) + " giây</dd>" +
          "<dt>" + FG.badge("Buồn ngủ", "drowsy", "fa-moon") + "</dt><dd>Nhắm mắt từ " + esc(t.drowsy_threshold) + " giây</dd>" +
          "<dt>" + FG.badge("Quay đi chỗ khác", "headaway", "fa-arrows-left-right") + "</dt><dd>Kéo dài từ " + esc(t.distraction_threshold) + " giây</dd>" +
          "<dt>" + FG.badge("Rời chỗ", "away", "fa-person-walking-arrow-right") + "</dt><dd>Không thấy từ " + esc(t.away_threshold) + " giây</dd></dl>" +
          '<p class="fg-helper mb-0 mt-3">Hành vi ngắn hơn các mốc này không được ghi nhận, để tránh báo nhầm khi học sinh chỉ cử động thoáng qua.</p>';
      } catch (e) {
        FG.setState(box, { error: true, compact: true, title: "Không tải được cấu hình", text: e.message, retry: settings.thresholds });
      }
    },
  };

  const PAGES = { dashboard: dashboard, students: students, attendance: attendance, alerts: alerts, analytics: analytics, reports: reports, settings: settings };

  /* ───────────────────────── student detail panel ───────────────────────── */
  function openPanelFromEvent(e) {
    const el = e.target.closest("[data-student]");
    if (el) openPanel(Number(el.dataset.student));
  }

  function panelLiveHTML(s) {
    const c = ctx(), st = FG.studentStatus(s, c), now = serverNow();
    const secs = st.since ? Math.max(0, now - st.since) : null;
    const att = FG.attendance(s.attendance_status);
    const conf = confidenceText(st.confidence);
    const others = (s.behaviors || []).filter((b) => b.type !== st.key && b.type !== "AWAY");
    let html = '<div class="fg-row mb-3"><span class="fg-avatar is-lg" aria-hidden="true">' + esc(FG.initials(s.name)) + "</span>" +
      '<div><div class="fg-section-title">' + esc(s.name) + '</div><div class="fg-caption">Lớp ' + esc(S.data.class_name) + "</div></div></div>";
    html += '<div class="fg-banner mb-3" data-tone="' + st.tone + '"><i class="fa-solid ' + st.icon + '" aria-hidden="true"></i><div>' +
      '<div class="fg-banner-title">' + esc(statusLine(st, secs)) + "</div>" +
      (st.detail ? "<div>" + esc(st.detail) + "</div>" : "") +
      (st.action ? "<div>" + esc(st.action) + "</div>" : "") +
      (conf ? '<div class="fg-caption">' + conf + " (mức chắc chắn của hệ thống, không phải kết luận)</div>" : "") +
      (others.length ? '<div class="fg-caption">Cùng lúc: ' + others.map((b) => esc(FG.behavior(b.type).label.toLowerCase())).join(", ") + "</div>" : "") +
      "</div></div>";
    if (c.active) {
      const ec = s.event_counts || {};
      const live = (s.behaviors || []).map((b) => b.type);
      // Finished episodes plus the one still going on, so "0 lần" never sits next to an active alert.
      const times = (type, base) => { const on = live.indexOf(type) !== -1; return ((base || 0) + (on ? 1 : 0)) + " lần" + (on ? " (đang diễn ra)" : ""); };
      html += '<h3 class="fg-card-title mb-2">Buổi học này</h3><dl class="fg-kv mb-4">' +
        "<dt>Điểm danh</dt><dd>" + FG.badge(att.label, att.tone, att.icon) + "</dd>" +
        "<dt>Điểm tập trung hiện tại</dt><dd>" + (FG.hasScore(s.focus_score) ? s.focus_score + "/100" : "Chưa có") + "</dd>" +
        "<dt>Điểm trung bình buổi này</dt><dd>" + (FG.hasScore(s.average_focus_score) ? s.average_focus_score + "/100" : "Chưa có") + "</dd>" +
        "<dt>Thời gian trong khung hình</dt><dd>" + esc(FG.duration(s.visible_seconds)) + "</dd>" +
        "<dt>Dùng điện thoại</dt><dd>" + times("PHONE", ec.PHONE) + "</dd>" +
        "<dt>Buồn ngủ</dt><dd>" + times("DROWSY", ec.DROWSY) + "</dd>" +
        "<dt>Quay đi chỗ khác</dt><dd>" + times("HEAD_AWAY", ec.HEAD_AWAY) + "</dd>" +
        "<dt>Rời chỗ</dt><dd>" + (s.away_count || 0) + " lần" + (s.away_seconds ? " · " + esc(FG.duration(s.away_seconds)) : "") + "</dd></dl>";
    }
    return html;
  }

  function renderPanelLive() {
    const s = studentById(S.panelStudentId), box = $("studentPanelLive");
    if (!s || !box) return;
    const html = panelLiveHTML(s);
    if (box._sig !== html) { box.innerHTML = html; box._sig = html; }
  }

  async function openPanel(studentId) {
    const s = studentById(studentId), body = $("studentPanelBody");
    if (!s || !body) return;
    S.panelStudentId = studentId;
    body.innerHTML = '<div id="studentPanelLive"></div><h3 class="fg-card-title mb-2">Các buổi học trước</h3><div id="studentPanelHistory"></div>' +
      '<p class="fg-helper mt-3 mb-0">Điểm được tính từ các hành vi quan sát được theo thời gian. Đây là thông tin tham khảo để hỗ trợ học sinh, không phải đánh giá năng lực.</p>';
    renderPanelLive();
    const panel = $("studentPanel");
    window.bootstrap.Offcanvas.getOrCreateInstance(panel).show();
    const hist = $("studentPanelHistory");
    FG.skeleton(hist, 3);
    try {
      const list = await FG.api("/api/student/attendance_history?student_id=" + encodeURIComponent(studentId));
      if (S.panelStudentId !== studentId) return;
      if (!list.length) { FG.setState(hist, { compact: true, icon: "fa-clock-rotate-left", title: "Chưa có buổi học nào đã kết thúc" }); return; }
      const scored = list.filter((a) => FG.hasScore(a.focus_score));
      const attended = list.filter((a) => !a.missed).length;
      hist.innerHTML = '<dl class="fg-kv mb-3"><dt>Đi học</dt><dd>' + attended + "/" + list.length + " buổi</dd>" +
        "<dt>Điểm trung bình các buổi</dt><dd>" + (scored.length ? Math.round(scored.reduce((a, x) => a + x.focus_score, 0) / scored.length) + "/100" : "Chưa có") + "</dd></dl>" +
        '<ul class="fg-list">' + list.slice(0, 5).map((a) => {
          const att = FG.attendance(a.attendance_status);
          return '<li class="fg-row-between py-2"><span class="fg-caption">' + esc(FG.dateTime(a.started_at)) + "</span><span class=\"fg-row\">" +
            FG.badge(att.label, att.tone, att.icon) + '<span class="fg-num" style="min-width: 3.5em; text-align: right;">' +
            (FG.hasScore(a.focus_score) ? a.focus_score + "/100" : "—") + "</span></span></li>";
        }).join("") + "</ul>";
    } catch (e) {
      FG.setState(hist, { error: true, compact: true, title: "Không tải được lịch sử", text: e.message });
    }
  }

  /* ───────────────────────── session detail modal ───────────────────────── */
  let currentDetail = null;

  async function openSession(classSessionId, title) {
    const body = $("sessionModalBody");
    $("sessionModalTitle").textContent = title;
    currentDetail = null;
    FG.skeleton(body, 5);
    window.bootstrap.Modal.getOrCreateInstance($("sessionModal")).show();
    try {
      const d = await FG.api("/api/teacher/class_session/" + encodeURIComponent(classSessionId));
      currentDetail = d;
      const sum = d.summary || {}, bd = d.breakdown || {}, counts = bd.counts || {};
      const metric = (label, value, na) => '<div class="fg-card fg-metric"><span class="fg-metric-label">' + label + '</span><span class="fg-metric-value' + (na ? " is-na" : "") + '" style="font-size: 1.375rem;">' + value + "</span></div>";
      let html = '<p class="fg-caption">Lớp ' + esc(BOOT.class_name) + " · " + esc(FG.dateTime(d.started_at)) + " · " + esc(MODE_LABEL[d.mode] || d.mode || "") + "</p>" +
        '<div class="fg-grid fg-grid-metrics mb-4">' +
        metric("Thời lượng", esc(FG.duration(d.duration_seconds))) +
        metric("Có mặt", (sum.present || 0) + "/" + (sum.enrolled !== undefined ? sum.enrolled : d.students.length)) +
        metric("Điểm trung bình", FG.hasScore(sum.average_score) ? sum.average_score : "Chưa có", !FG.hasScore(sum.average_score)) +
        metric("Sự kiện", bd.total || 0) + "</div>";
      if (!FG.hasScore(sum.average_score)) {
        html += '<div class="fg-banner mb-3" data-tone="info"><i class="fa-solid fa-circle-info" aria-hidden="true"></i><div>Buổi học này không có đủ dữ liệu camera để tính điểm tập trung. Điểm được để trống thay vì ghi là 0.' +
          (!sum.present ? " Camera không ghi nhận được học sinh nào nên điểm danh hiển thị vắng mặt. Hãy đối chiếu lại với thực tế trên lớp." : "") + "</div></div>";
      }
      html += '<div class="fg-row mb-4">' + ["PHONE", "DROWSY", "HEAD_AWAY", "AWAY"].map((t) => {
        const b = FG.behavior(t); return FG.badge(b.label + ": " + (counts[t] || 0) + " lần", b.tone, b.icon);
      }).join("") + "</div>";
      html += '<h3 class="fg-card-title mb-2">Học sinh</h3>';
      html += d.students.length ? '<div class="fg-table-wrap mb-4"><table class="fg-table is-stack"><thead><tr><th>Học sinh</th><th>Điểm danh</th><th class="is-num">Điểm trung bình</th><th class="is-num">Rời chỗ</th></tr></thead><tbody>' +
        d.students.map((s) => { const att = FG.attendance(s.attendance_status);
          return '<tr><td class="is-primary"><span class="fg-person-name">' + esc(s.name) + "</span></td>" +
            '<td data-label="Điểm danh">' + FG.badge(att.label, att.tone, att.icon) + "</td>" +
            '<td class="is-num" data-label="Điểm trung bình">' + (FG.hasScore(s.average_score) ? s.average_score : '<span class="fg-muted">Chưa có</span>') + "</td>" +
            '<td class="is-num" data-label="Rời chỗ">' + s.away_count + " lần</td></tr>"; }).join("") + "</tbody></table></div>"
        : FG.stateHTML({ compact: true, title: "Không có dữ liệu học sinh cho buổi này" });
      html += '<h3 class="fg-card-title mb-2">Hành vi đã ghi nhận</h3>';
      html += d.events.length ? '<div class="fg-table-wrap"><table class="fg-table is-stack"><thead><tr><th>Thời điểm</th><th>Học sinh</th><th>Hành vi</th><th class="is-num">Kéo dài</th></tr></thead><tbody>' +
        d.events.map((e) => { const b = FG.behavior(e.type);
          return '<tr><td class="is-primary fg-num">' + esc(FG.time(e.start_time)) + "</td>" +
            '<td data-label="Học sinh">' + esc(e.name) + "</td>" +
            '<td data-label="Hành vi">' + FG.badge(b.label, b.tone, b.icon) + "</td>" +
            '<td class="is-num" data-label="Kéo dài">' + esc(FG.duration(e.duration_seconds)) + "</td></tr>"; }).join("") + "</tbody></table></div>"
        : FG.stateHTML({ compact: true, icon: "fa-circle-check", title: "Không ghi nhận hành vi kéo dài nào trong buổi này" });
      body.innerHTML = html;
    } catch (e) {
      FG.setState(body, { error: true, title: "Không tải được tổng kết buổi học", text: e.message, retry: () => openSession(classSessionId, title) });
    }
  }

  function downloadCsv(filename, rows) {
    const csv = rows.map((r) => r.map((v) => '"' + String(v === null || v === undefined ? "" : v).replace(/"/g, '""') + '"').join(",")).join("\r\n");
    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename.replace(/[^\w.\-À-ỹ ]+/g, "_");
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  /* ------------------------------------------------------------------ boot */
  document.addEventListener("DOMContentLoaded", () => {
    const classSelect = $("classSelect");
    if (classSelect) classSelect.addEventListener("change", () => { window.location.href = window.location.pathname + "?class_id=" + encodeURIComponent(classSelect.value); });

    const panel = $("studentPanel");
    if (panel) panel.addEventListener("hidden.bs.offcanvas", () => { S.panelStudentId = null; });
    const csvBtn = $("sessionCsv"), printBtn = $("sessionPrint");
    if (csvBtn) csvBtn.addEventListener("click", () => {
      if (!currentDetail) return;
      downloadCsv("buoi-hoc-" + currentDetail.class_session_id + ".csv",
        [["Học sinh", "Điểm danh", "Điểm trung bình", "Số lần rời chỗ", "Thời gian rời chỗ (giây)"]].concat(
          currentDetail.students.map((s) => [s.name, FG.attendance(s.attendance_status).label,
            FG.hasScore(s.average_score) ? s.average_score : "", s.away_count, s.away_seconds])));
    });
    if (printBtn) printBtn.addEventListener("click", () => window.print());

    const page = PAGES[S.page];
    if (page && page.init) page.init();
    load();
    connect();

    setInterval(() => {
      renderLiveStrip();
      if (S.page === "dashboard") dashboard.tick();
      if (S.panelStudentId !== null) renderPanelLive();
    }, 1000);
    // Safety net: if realtime pushes stop (or no session is running, so nothing is pushed), re-read the state.
    setInterval(() => { if (!document.hidden && performance.now() - S.receivedAt > 4000) load(); }, 5000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
  });
})();
