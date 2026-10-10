/* Student pages. The student's state, score and attendance are produced by
 * the server; this file only displays /api/stats and the analytics APIs. */
(function () {
  "use strict";
  const FG = window.FG;
  const bootEl = document.getElementById("fgBoot");
  if (!bootEl) return;
  const PAGE = JSON.parse(bootEl.textContent).page;
  const $ = (id) => document.getElementById(id);
  const esc = FG.esc;

  /* -------------------------------------------------------- preferences */
  const prefs = {
    get sound() { try { return localStorage.getItem("fg-sound") !== "off"; } catch (e) { return true; } },
    set sound(v) { try { localStorage.setItem("fg-sound", v ? "on" : "off"); } catch (e) { /* private mode */ } },
    get volume() { try { const v = parseInt(localStorage.getItem("fg-volume"), 10); return isNaN(v) ? 50 : Math.max(0, Math.min(100, v)); } catch (e) { return 50; } },
    set volume(v) { try { localStorage.setItem("fg-volume", String(v)); } catch (e) { /* private mode */ } },
  };
  let audioCtx = null;
  function beep() {
    if (!prefs.sound || !prefs.volume) return;
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      const osc = audioCtx.createOscillator(), gain = audioCtx.createGain();
      osc.frequency.value = 660;
      gain.gain.value = 0.25 * (prefs.volume / 100);
      osc.connect(gain); gain.connect(audioCtx.destination);
      osc.start(); osc.stop(audioCtx.currentTime + 0.18);
    } catch (e) { /* audio not available */ }
  }

  const sessionScore = (s) => (FG.hasScore(s.avg_focus_score) ? Math.round(s.avg_focus_score) : FG.hasScore(s.final_score) ? Math.round(s.final_score) : null);
  const SOURCE_LABEL = { online_class: "Lớp trực tuyến", personal_camera: "Tự học" };

  const STATE_TEXT = {
    FOCUSED: "Em đang tập trung. Tiếp tục nhé!",
    PHONE: "Hệ thống thấy em đang dùng điện thoại.",
    DROWSY: "Em có vẻ buồn ngủ. Thử đứng dậy vận động một chút.",
    HEAD_AWAY: "Em đang quay đi chỗ khác.",
    AWAY: "Em đã rời khỏi chỗ ngồi.",
    TEMPORARILY_NOT_VISIBLE: "Camera tạm thời không thấy em.",
    NOT_SEEN: "Camera chưa thấy khuôn mặt em. Hãy ngồi đối diện camera.",
    NO_SIGNAL: "Mất tín hiệu camera. Điểm tạm dừng, không bị trừ.",
    UNKNOWN: "Đang chờ dữ liệu từ camera.",
    OFFLINE: "Đang kết nối lại với phiên học.",
  };

  /* ───────────────────────────── Phiên học ───────────────────────────── */
  const dashboard = {
    data: null, receivedAt: 0, lastState: null, chart: null, socket: null,
    camera: { src: false, startedAt: 0, error: false },

    init() {
      $("startBtn").addEventListener("click", dashboard.start);
      $("stopBtn").addEventListener("click", dashboard.stop);
      $("cameraFeed").addEventListener("error", () => { if (dashboard.camera.src) { dashboard.camera.error = true; dashboard.renderCamera(); } });
      $("cameraState").addEventListener("click", (e) => { if (e.target.closest("[data-camera-retry]")) dashboard.cameraRestart(); });
      document.addEventListener("fg:theme", () => { FG.chartDefaults(); if (dashboard.chart) { dashboard.chart.destroy(); dashboard.chart = null; dashboard.renderChart(); } });
      document.addEventListener("visibilitychange", () => {
        const d = dashboard.data;
        // Disclosed to the student on this page; it never changes the score.
        if (document.hidden && d && d.is_active && d.linked_class_session_id && dashboard.socket) dashboard.socket.emit("student_tab_switch", {});
        if (!document.hidden) dashboard.load();
      });
      dashboard.socket = FG.connectSocket({ onConnect: (s) => s.emit("join_student_room", {}) });
      if (dashboard.socket) {
        dashboard.socket.on("class_started", () => { FG.toast("Giáo viên đã bắt đầu buổi học.", "info"); dashboard.load(); });
        dashboard.socket.on("class_ended", () => { FG.toast("Giáo viên đã kết thúc buổi học.", "info"); dashboard.load(); });
      }
      dashboard.load();
      setInterval(() => {
        const d = dashboard.data;
        if (d && d.is_active) { $("sessionTimer").textContent = FG.clock(d.seconds_elapsed + (performance.now() - dashboard.receivedAt) / 1000); dashboard.renderCamera(); }
      }, 1000);
      // Live numbers come from the server once a second while studying; idle pages poll slowly.
      setInterval(() => { if (!document.hidden && dashboard.data && dashboard.data.is_active) dashboard.load(); }, 1000);
      setInterval(() => { if (!document.hidden && (!dashboard.data || !dashboard.data.is_active)) dashboard.load(); }, 5000);
    },

    loading: false,
    async load() {
      if (dashboard.loading) return;
      dashboard.loading = true;
      try {
        const d = await FG.api("/api/stats");
        const wasActive = dashboard.data && dashboard.data.is_active;
        dashboard.data = d; dashboard.receivedAt = performance.now(); dashboard.error = null;
        if (wasActive && !d.is_active && !dashboard.stopping) FG.toast("Phiên học đã kết thúc.", "info");
        dashboard.render();
      } catch (e) {
        if (e.status === 401) return;
        dashboard.error = e.message;
        if (!dashboard.data) {
          $("sessionTitle").textContent = "Không tải được trạng thái phiên học";
          $("sessionMeta").textContent = e.message;
        } else { FG.setConnection("reconnecting"); }
      } finally { dashboard.loading = false; }
    },

    render() {
      const d = dashboard.data, active = d.is_active;
      const linked = !!d.linked_class_session_id;
      $("sessionDot").classList.toggle("is-idle", !active);
      $("sessionTitle").textContent = active ? (linked ? "Đang học cùng lớp" : "Đang tự học") : "Chưa bắt đầu phiên học";
      $("sessionMeta").textContent = active
        ? (linked ? "Giáo viên thấy trạng thái của em trong buổi học này." : "Phiên tự học: chỉ em và phụ huynh xem được kết quả.")
        : "Bấm Bắt đầu học để bật camera và tính điểm tập trung.";
      $("subjectWrap").hidden = active;
      const timer = $("sessionTimer");
      timer.hidden = !active;
      if (active) timer.textContent = FG.clock(d.seconds_elapsed);
      const startBtn = $("startBtn"), stopBtn = $("stopBtn");
      startBtn.hidden = active; stopBtn.hidden = !active;
      if (!FG.isBusy(startBtn)) startBtn.disabled = false;

      // class banner
      const banner = $("classBanner"), cl = d.class_live || {};
      if (cl.active && cl.mode === "online" && !active) {
        banner.hidden = false; banner.dataset.tone = "warning";
        $("classBannerTitle").textContent = "Lớp của em đang có buổi học trực tuyến";
        $("classBannerText").textContent = "Bấm Bắt đầu học để vào lớp. Giáo viên sẽ thấy em có mặt.";
      } else if (cl.active && cl.mode === "online" && linked) {
        banner.hidden = false; banner.dataset.tone = "info";
        $("classBannerTitle").textContent = "Em đang trong buổi học trực tuyến của lớp";
        $("classBannerText").textContent = "Nếu em chuyển sang tab khác, trình duyệt sẽ báo cho giáo viên biết. Việc này không ảnh hưởng điểm.";
      } else if (cl.active && cl.mode === "classroom") {
        banner.hidden = false; banner.dataset.tone = "info";
        $("classBannerTitle").textContent = "Lớp của em đang học trên lớp với camera lớp học";
        $("classBannerText").textContent = "Giáo viên đang dùng camera của lớp. Em không cần bật camera riêng.";
      } else { banner.hidden = true; }

      dashboard.renderScore();
      dashboard.renderCounts();
      dashboard.renderCamera();
      dashboard.renderChart();
    },

    renderScore() {
      const d = dashboard.data, gauge = $("gauge"), value = $("gaugeValue");
      const st = FG.studentStatus(d, { active: d.is_active, mode: "personal" });
      const has = d.is_active && FG.hasScore(d.focus_score);
      value.textContent = has ? d.focus_score : "Chưa có";
      value.classList.toggle("is-na", !has);
      $("gaugeSub").textContent = has ? "trên 100" : "điểm";
      $("gaugeBar").setAttribute("stroke-dasharray", (has ? d.focus_score : 0) + " 100");
      gauge.dataset.tone = has ? FG.scoreTone(d.focus_score) : "unknown";
      gauge.setAttribute("role", "img");
      gauge.setAttribute("aria-label", has ? "Điểm tập trung " + d.focus_score + " trên 100" : "Chưa có điểm tập trung");
      // A camera that is not analysing means "nothing is known", not "we cannot see you".
      const blind = d.is_active && (!d.camera || d.camera.state !== "ACTIVE");
      $("stateBadge").innerHTML = !d.is_active ? "" : blind ? FG.badge("Camera chưa hoạt động", "nocam", "fa-video-slash")
        : FG.badge(st.label, st.tone, st.icon);
      const first = (d.behaviors || []).find((b) => b.type === st.key);
      const secs = first && first.since ? Math.max(0, d.server_time - first.since) : null;
      $("stateText").textContent = !d.is_active ? "Điểm sẽ xuất hiện khi em bắt đầu phiên học."
        : blind ? "Camera chưa hoạt động nên chưa tính điểm. Xem hướng dẫn ở khung camera."
        : (STATE_TEXT[st.key] || "") + (secs !== null ? " (" + FG.duration(secs) + ")" : "");
      // one short beep when a distraction starts
      const alerting = !blind && ["PHONE", "DROWSY", "HEAD_AWAY"].indexOf(st.key) !== -1;
      if (d.is_active && alerting && dashboard.lastState !== st.key) beep();
      dashboard.lastState = d.is_active ? st.key : null;
    },

    renderCounts() {
      const d = dashboard.data, box = $("countsBody");
      if (!d.is_active) {
        FG.setState(box, { compact: true, icon: "fa-list", title: "Chưa có phiên học", text: "Số lần mất tập trung của phiên đang học sẽ hiện ở đây." });
        box._sig = null;
        return;
      }
      const ec = d.event_counts || {};
      const html = '<dl class="fg-kv">' +
        "<dt>Điểm trung bình phiên này</dt><dd>" + (FG.hasScore(d.average_focus_score) ? d.average_focus_score + "/100" : "Chưa có") + "</dd>" +
        "<dt>" + FG.badge("Dùng điện thoại", "phone", "fa-mobile-screen") + "</dt><dd>" + (ec.PHONE || 0) + " lần</dd>" +
        "<dt>" + FG.badge("Buồn ngủ", "drowsy", "fa-moon") + "</dt><dd>" + (ec.DROWSY || 0) + " lần</dd>" +
        "<dt>" + FG.badge("Quay đi chỗ khác", "headaway", "fa-arrows-left-right") + "</dt><dd>" + (ec.HEAD_AWAY || 0) + " lần</dd>" +
        "<dt>" + FG.badge("Rời chỗ", "away", "fa-person-walking-arrow-right") + "</dt><dd>" + (d.seat_leaving_count || 0) + " lần" +
        (d.seat_leaving_duration ? " · " + esc(FG.duration(d.seat_leaving_duration)) : "") + "</dd>" +
        "<dt>Thời gian camera thấy em</dt><dd>" + esc(FG.duration(d.visible_seconds)) + "</dd></dl>";
      if (box._sig !== html) { box.innerHTML = html; box._sig = html; }
    },

    cameraRestart() {
      const cam = dashboard.camera;
      cam.error = false; cam.startedAt = performance.now(); cam.src = true;
      $("cameraFeed").src = "/video_feed?t=" + Date.now();
      dashboard.renderCamera();
    },

    renderCamera() {
      const d = dashboard.data, feed = $("cameraFeed"), overlay = $("cameraState"), badge = $("cameraBadge"), cam = dashboard.camera;
      if (!d) return;
      const setBadge = (tone, icon, label) => { badge.dataset.tone = tone; badge.innerHTML = '<i class="fa-solid ' + icon + '" aria-hidden="true"></i>' + esc(label); };
      const show = (icon, title, text, retry, spinner) => {
        const sig = [icon, title, text, retry, spinner].join("|");
        overlay.hidden = false;
        if (overlay._sig === sig) return;
        overlay._sig = sig;
        overlay.innerHTML = (spinner ? '<span class="spinner-border text-light" aria-hidden="true"></span>' : '<i class="fa-solid ' + icon + '" aria-hidden="true"></i>') +
          '<div class="fg-camera-state-title">' + esc(title) + '</div><p class="fg-camera-state-text">' + esc(text) + "</p>" +
          (retry ? '<button type="button" class="btn btn-light btn-sm mt-1" data-camera-retry><i class="fa-solid fa-rotate-right" aria-hidden="true"></i>' + esc(retry) + "</button>" : "");
      };
      if (!d.is_active) {
        if (cam.src) { feed.removeAttribute("src"); cam.src = false; }
        feed.hidden = true; cam.error = false;
        setBadge("neutral", "fa-video-slash", "Chưa bật");
        show("fa-video-slash", "Camera chưa bật", "Bấm Bắt đầu học để bật camera.");
        return;
      }
      if (!cam.src && !cam.error) { dashboard.cameraRestart(); return; }
      const c = d.camera || { state: "STARTING", label: "Đang khởi động camera", help: "" };
      const waited = (performance.now() - cam.startedAt) / 1000;
      if (cam.error) {
        feed.hidden = true;
        setBadge("danger", "fa-video-slash", "Không có hình ảnh");
        show("fa-video-slash", "Không tải được hình ảnh camera", "Kết nối tới camera bị ngắt. Điểm tạm dừng, không bị trừ.", "Thử lại");
        return;
      }
      switch (c.state) {
        case "ACTIVE":
          feed.hidden = false; overlay.hidden = true; overlay._sig = null;
          setBadge("success", "fa-video", "Đang hoạt động");
          break;
        case "STARTING":
          feed.hidden = true;
          setBadge("info", "fa-hourglass-half", "Đang khởi động");
          if (waited > 30) show("fa-hourglass-half", "Camera khởi động lâu hơn bình thường", "Kiểm tra camera đã được cắm và không bị ứng dụng khác sử dụng.", "Thử lại");
          else show("", c.label, c.help, null, true);
          break;
        case "NOT_STREAMING":
          feed.hidden = true;
          setBadge("warning", "fa-video-slash", "Chưa có hình ảnh");
          if (waited > 6) show("fa-video-slash", "Camera chưa gửi hình ảnh", "Bấm Mở camera để thử lại.", "Mở camera");
          else show("", "Đang kết nối camera", "Vui lòng chờ trong giây lát.", null, true);
          break;
        case "NO_SIGNAL":
          feed.hidden = true;
          setBadge("warning", "fa-video-slash", "Mất tín hiệu");
          show("fa-video-slash", c.label, c.help, "Thử lại");
          break;
        default:
          feed.hidden = true;
          setBadge("danger", "fa-video-slash", "Không dùng được");
          show("fa-video-slash", c.label, c.help, "Thử lại");
      }
    },

    renderChart() {
      const d = dashboard.data, box = $("chartBody");
      const hist = d.is_active ? d.history || [] : [];
      if (hist.length < 2) {
        if (dashboard.chart) { dashboard.chart.destroy(); dashboard.chart = null; }
        FG.setState(box, { compact: true, icon: "fa-chart-line",
          title: d.is_active ? "Đang thu thập dữ liệu" : "Chưa có phiên học",
          text: d.is_active ? "Biểu đồ xuất hiện sau khi camera ghi nhận được vài giây." : "Biểu đồ điểm tập trung hiện khi em đang học." });
        return;
      }
      if (!window.Chart) return;
      const labels = hist.map((h) => h.time), values = hist.map((h) => h.score);
      if (!dashboard.chart) {
        box.innerHTML = '<div class="fg-chart is-sm"><canvas role="img" aria-label="Biểu đồ điểm tập trung theo thời gian trong phiên học"></canvas></div>';
        dashboard.chart = new window.Chart(box.querySelector("canvas"), {
          type: "line",
          data: { labels: labels, datasets: [{ data: values, borderColor: FG.cssVar("--fg-primary"), borderWidth: 2, pointRadius: 0, tension: 0.25 }] },
          options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
                     scales: { y: { min: 0, max: 100 }, x: { ticks: { maxTicksLimit: 6 } } } },
        });
      } else {
        dashboard.chart.data.labels = labels;
        dashboard.chart.data.datasets[0].data = values;
        dashboard.chart.update();
      }
    },

    async start() {
      const btn = $("startBtn");
      if (FG.isBusy(btn)) return;
      FG.busy(btn, true, "Đang bắt đầu…");
      try {
        const res = await FG.api("/api/start_session", { body: { subject: $("subject").value } });
        if (res.already_active) FG.toast("Phiên học đã được bắt đầu trước đó.", "info");
        await dashboard.load();
      } catch (e) { FG.toast(e.message, "danger"); }
      finally { FG.busy(btn, false); }
    },

    async stop() {
      const btn = $("stopBtn"), d = dashboard.data;
      if (FG.isBusy(btn) || !d) return;
      const ok = await FG.confirm({
        title: "Kết thúc phiên học?",
        body: "Camera sẽ tắt và kết quả phiên học được lưu lại.",
        consequences: ["Kết quả được lưu vào Lịch sử của em."].concat(
          d.linked_class_session_id ? ["Giáo viên sẽ thấy em ngoại tuyến trong buổi học đang diễn ra."] : []),
        confirmLabel: "Kết thúc phiên học", danger: true,
      });
      if (!ok) return;
      FG.busy(btn, true, "Đang kết thúc…");
      dashboard.stopping = true;
      try {
        const res = await FG.api("/api/stop_session", { body: {} });
        await dashboard.load();
        if (res.was_active && res.result) showResult(res.result);
        else FG.toast("Phiên học đã được kết thúc trước đó.", "info");
      } catch (e) { FG.toast(e.message, "danger"); }
      finally { dashboard.stopping = false; FG.busy(btn, false); }
    },
  };

  function showResult(r) {
    const has = FG.hasScore(r.avg_focus_score);
    $("detailModalTitle").textContent = "Kết quả phiên học";
    $("detailModalBody").innerHTML =
      '<div class="fg-grid fg-grid-2 mb-3" style="gap: var(--fg-3);">' +
      '<div class="fg-card fg-metric"><span class="fg-metric-label">Điểm trung bình</span><span class="fg-metric-value' + (has ? "" : " is-na") + '">' + (has ? Math.round(r.avg_focus_score) : "Chưa có") + "</span></div>" +
      '<div class="fg-card fg-metric"><span class="fg-metric-label">Số lần mất tập trung</span><span class="fg-metric-value">' + (r.total_distractions || 0) + "</span></div></div>" +
      (has ? '<p class="mb-0">Kết quả đã được lưu. Em có thể xem lại trong Lịch sử.</p>'
           : '<div class="fg-banner" data-tone="info"><i class="fa-solid fa-circle-info" aria-hidden="true"></i><div>Camera chưa ghi nhận đủ dữ liệu nên phiên này không có điểm. Điểm được để trống thay vì ghi là 0.</div></div>');
    window.bootstrap.Modal.getOrCreateInstance($("detailModal")).show();
  }

  /* ───────────────────────────── Thống kê ───────────────────────────── */
  const stats = {
    charts: [],
    init() { document.addEventListener("fg:theme", () => { FG.chartDefaults(); stats.load(); }); stats.load(); },
    async load() {
      stats.charts.forEach((c) => c.destroy()); stats.charts = [];
      ["recoBody", "compBody", "subjBody", "heatBody"].forEach((id) => FG.skeleton($(id), 2));
      let profile, reco, comp, subj, heat;
      try {
        [profile, reco, comp, subj, heat] = await Promise.all([
          FG.api("/api/student/profile"), FG.api("/api/student/recommendations"),
          FG.api("/api/student/session_comparison"), FG.api("/api/student/subject_analytics"), FG.api("/api/student/heatmap"),
        ]);
      } catch (e) {
        ["recoBody", "compBody", "subjBody", "heatBody"].forEach((id) =>
          FG.setState($(id), { error: true, compact: true, title: "Không tải được dữ liệu", text: e.message, retry: stats.load }));
        return;
      }
      const na = (id, text) => { $(id).textContent = text; $(id).classList.add("is-na"); };
      $("pSessions").textContent = profile.total_sessions || 0;
      if (profile.total_sessions) $("pTime").textContent = FG.duration(profile.total_duration_seconds); else na("pTime", "Chưa có");
      if (FG.hasScore(profile.average_focus_score)) $("pAvg").textContent = profile.average_focus_score; else na("pAvg", "Chưa có");
      const TREND = { up: "Đang tăng", down: "Đang giảm", stable: "Ổn định" };
      if (profile.trend) { $("pTrend").textContent = TREND[profile.trend]; $("pTrendSub").textContent = "So sánh nửa đầu và nửa sau các phiên"; }
      else { na("pTrend", "Chưa đủ dữ liệu"); $("pTrendSub").textContent = "Cần ít nhất 4 phiên học có điểm"; }

      if (reco.status === "ok") {
        $("recoBody").innerHTML = '<div class="fg-row"><span class="fg-tone-icon" data-tone="primary"><i class="fa-solid fa-clock" aria-hidden="true"></i></span>' +
          '<div><div class="fg-section-title">' + esc(reco.best_time_range) + '</div><div class="fg-caption">' + esc(reco.text) + "</div></div></div>";
      } else {
        FG.setState($("recoBody"), { compact: true, icon: "fa-clock", title: "Chưa đủ dữ liệu để gợi ý", text: reco.text });
      }

      if (!comp.length) {
        FG.setState($("compBody"), { compact: true, icon: "fa-chart-line", title: "Chưa có phiên học nào có điểm", text: "Hoàn thành một phiên học để thấy điểm ở đây." });
      } else if (window.Chart) {
        $("compBody").innerHTML = '<div class="fg-chart"><canvas role="img" aria-label="Biểu đồ điểm các phiên học gần đây"></canvas></div>';
        stats.charts.push(new window.Chart($("compBody").querySelector("canvas"), {
          type: "bar",
          data: { labels: comp.map((c) => FG.dateTime(c.start_time)), datasets: [{ label: "Điểm", data: comp.map((c) => c.score), backgroundColor: FG.cssVar("--fg-primary") }] },
          options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { min: 0, max: 100 } } },
        }));
      }

      if (!subj.length) {
        FG.setState($("subjBody"), { compact: true, icon: "fa-book", title: "Chưa có dữ liệu theo môn", text: "Chọn môn học khi bắt đầu phiên để thống kê theo môn." });
      } else {
        $("subjBody").innerHTML = '<ul class="fg-list">' + subj.map((s) =>
          '<li class="fg-row-between py-2"><span><strong>' + esc(s.subject) + '</strong> <span class="fg-caption">· ' + s.sessions + " phiên</span></span>" +
          FG.badge(s.score + "/100", FG.scoreTone(s.score)) + "</li>").join("") + "</ul>";
      }

      const hours = Object.keys(heat);
      if (!hours.some((h) => heat[h].samples)) {
        FG.setState($("heatBody"), { compact: true, icon: "fa-table-cells-large", title: "Chưa có dữ liệu theo giờ", text: "Bảng này cho biết em thường tập trung tốt vào giờ nào, sau khi có vài phiên học." });
      } else {
        $("heatBody").innerHTML = '<div class="fg-heat">' + hours.map((h) => {
          const c = heat[h];
          return c.samples
            ? '<div class="fg-heat-cell" data-tone="' + FG.scoreTone(c.score) + '" title="' + c.samples + ' phiên"><span>' + esc(h) + "</span><strong>" + c.score + "</strong></div>"
            : '<div class="fg-heat-cell is-empty"><span>' + esc(h) + '</span><strong aria-label="chưa có dữ liệu">—</strong></div>';
        }).join("") + "</div>";
      }
    },
  };

  /* ───────────────────────────── Lịch sử ───────────────────────────── */
  const reports = {
    sessions: [],
    init() {
      $("sessPrint").addEventListener("click", () => window.print());
      $("sessCsv").addEventListener("click", reports.csv);
      $("sessBody").addEventListener("click", (e) => { const r = e.target.closest("[data-session]"); if (r) reports.detail(Number(r.dataset.session)); });
      $("sessBody").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { const r = e.target.closest("tr[data-session]"); if (r) { e.preventDefault(); reports.detail(Number(r.dataset.session)); } } });
      reports.load(); reports.attendance();
    },
    async load() {
      const box = $("sessBody");
      FG.skeleton(box, 4);
      try {
        const all = await FG.api("/api/sessions");
        const list = reports.sessions = all.filter((s) => s.end_time);
        $("sessCsv").disabled = !list.length;
        if (!list.length) {
          FG.setState(box, { icon: "fa-clock-rotate-left", title: "Em chưa có phiên học nào", text: "Vào trang Phiên học và bấm Bắt đầu học. Kết quả sẽ được lưu ở đây." });
          return;
        }
        box.innerHTML = '<div class="fg-table-wrap"><table class="fg-table is-stack"><thead><tr><th>Thời gian</th><th>Môn học</th><th>Hình thức</th>' +
          '<th class="is-num">Thời lượng</th><th class="is-num">Điểm trung bình</th><th class="is-num">Mất tập trung</th></tr></thead><tbody>' +
          list.map((s) => { const sc = sessionScore(s);
            return '<tr class="is-clickable" tabindex="0" data-session="' + s.id + '">' +
              '<td class="is-primary"><span class="fg-person-name">' + esc(FG.dateTime(s.start_time)) + "</span></td>" +
              '<td data-label="Môn học">' + esc(s.subject || "—") + "</td>" +
              '<td data-label="Hình thức">' + esc(SOURCE_LABEL[s.source] || "Tự học") + "</td>" +
              '<td class="is-num" data-label="Thời lượng">' + esc(FG.duration(s.duration_seconds)) + "</td>" +
              '<td class="is-num" data-label="Điểm trung bình">' + (sc === null ? '<span class="fg-muted">Chưa có</span>' : sc) + "</td>" +
              '<td class="is-num" data-label="Mất tập trung">' + (s.total_distractions || 0) + " lần</td></tr>"; }).join("") +
          "</tbody></table></div>";
      } catch (e) {
        FG.setState(box, { error: true, title: "Không tải được lịch sử", text: e.message, retry: reports.load });
      }
    },
    async attendance() {
      const box = $("attBody");
      FG.skeleton(box, 3);
      try {
        const list = await FG.api("/api/student/attendance_history");
        if (!list.length) {
          FG.setState(box, { compact: true, icon: "fa-clipboard-check", title: "Lớp của em chưa có buổi học nào đã kết thúc" });
          return;
        }
        box.innerHTML = '<div class="fg-table-wrap"><table class="fg-table is-stack"><thead><tr><th>Buổi học</th><th>Điểm danh</th><th class="is-num">Điểm trung bình</th><th class="is-num">Rời chỗ</th></tr></thead><tbody>' +
          list.map((a) => { const att = FG.attendance(a.attendance_status);
            return '<tr><td class="is-primary"><span class="fg-person-name">' + esc(FG.dateTime(a.started_at)) + "</span></td>" +
              '<td data-label="Điểm danh">' + FG.badge(att.label, att.tone, att.icon) + "</td>" +
              '<td class="is-num" data-label="Điểm trung bình">' + (FG.hasScore(a.focus_score) ? a.focus_score : '<span class="fg-muted">Chưa có</span>') + "</td>" +
              '<td class="is-num" data-label="Rời chỗ">' + (a.missed ? "—" : (a.away_count || 0) + " lần") + "</td></tr>"; }).join("") +
          "</tbody></table></div>";
      } catch (e) {
        FG.setState(box, { error: true, compact: true, title: "Không tải được điểm danh", text: e.message, retry: reports.attendance });
      }
    },
    async detail(id) {
      const s = reports.sessions.find((x) => x.id === id), body = $("detailModalBody");
      if (!s) return;
      $("detailModalTitle").textContent = "Phiên học " + FG.dateTime(s.start_time);
      FG.skeleton(body, 3);
      window.bootstrap.Modal.getOrCreateInstance($("detailModal")).show();
      try {
        const bd = await FG.api("/api/session/" + encodeURIComponent(id) + "/breakdown");
        const sc = sessionScore(s), counts = bd.counts || {}, dur = bd.durations_seconds || {};
        body.innerHTML = '<dl class="fg-kv mb-3"><dt>Môn học</dt><dd>' + esc(s.subject || "—") + "</dd>" +
          "<dt>Thời lượng</dt><dd>" + esc(FG.duration(s.duration_seconds)) + "</dd>" +
          "<dt>Điểm trung bình</dt><dd>" + (sc === null ? "Chưa có" : sc + "/100") + "</dd></dl>" +
          '<h3 class="fg-card-title mb-2">Hành vi đã ghi nhận</h3>' +
          (bd.total ? '<dl class="fg-kv">' + ["PHONE", "DROWSY", "HEAD_AWAY", "AWAY"].map((t) => { const b = FG.behavior(t);
              return "<dt>" + FG.badge(b.label, b.tone, b.icon) + "</dt><dd>" + (counts[t] || 0) + " lần" + (dur[t] ? " · " + esc(FG.duration(dur[t])) : "") + "</dd>"; }).join("") + "</dl>"
            : FG.stateHTML({ compact: true, icon: "fa-circle-check", title: "Không ghi nhận hành vi mất tập trung kéo dài nào" }));
      } catch (e) {
        FG.setState(body, { error: true, compact: true, title: "Không tải được chi tiết phiên học", text: e.message, retry: () => reports.detail(id) });
      }
    },
    csv() {
      const rows = [["Thời gian", "Môn học", "Hình thức", "Thời lượng (giây)", "Điểm trung bình", "Số lần mất tập trung"]].concat(
        reports.sessions.map((s) => [s.start_time, s.subject || "", SOURCE_LABEL[s.source] || "Tự học", s.duration_seconds || 0,
          sessionScore(s) === null ? "" : sessionScore(s), s.total_distractions || 0]));
      const csv = rows.map((r) => r.map((v) => '"' + String(v).replace(/"/g, '""') + '"').join(",")).join("\r\n");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
      a.download = "lich-su-phien-hoc.csv";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    },
  };

  /* ───────────────────────────── Cài đặt ───────────────────────────── */
  const settings = {
    init() {
      const sound = $("prefSound"), vol = $("prefVolume");
      sound.checked = prefs.sound; vol.value = prefs.volume; $("prefVolumeValue").textContent = prefs.volume;
      vol.disabled = !prefs.sound;
      sound.addEventListener("change", () => { prefs.sound = sound.checked; vol.disabled = !sound.checked; FG.toast(sound.checked ? "Đã bật âm báo." : "Đã tắt âm báo.", "success"); });
      vol.addEventListener("input", () => { prefs.volume = Number(vol.value); $("prefVolumeValue").textContent = vol.value; });
      $("prefTest").addEventListener("click", () => { if (!prefs.sound) { FG.toast("Âm báo đang tắt.", "info"); return; } beep(); });
      settings.thresholds();
    },
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
          '<p class="fg-helper mb-0 mt-3">Cử động thoáng qua ngắn hơn các mốc này không được ghi nhận.</p>';
      } catch (e) {
        FG.setState(box, { error: true, compact: true, title: "Không tải được cấu hình", text: e.message, retry: settings.thresholds });
      }
    },
  };

  const PAGES = { dashboard: dashboard, stats: stats, reports: reports, settings: settings };
  document.addEventListener("DOMContentLoaded", () => {
    if (PAGES[PAGE]) PAGES[PAGE].init();
    const strip = $("liveStrip");
    if (strip) FG.api("/api/stats").then((d) => { strip.hidden = !d.is_active; }).catch(() => { /* banner is optional */ });
  });
})();
