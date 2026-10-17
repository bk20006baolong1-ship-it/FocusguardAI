/* Teacher review, activity context, evidence clips and real-classroom
 * evaluation (BTC feedback). The browser only shows what the server returns
 * and asks for actions; who reviews, consent and access are decided by the
 * server. No browser alert/confirm: FG.confirm uses the page dialog. */
(function () {
  "use strict";
  const FG = window.FG;
  const bootEl = document.getElementById("fgBoot");
  if (!bootEl) return;
  const BOOT = JSON.parse(bootEl.textContent);
  const $ = (id) => document.getElementById(id);
  const esc = FG.esc;
  const classQuery = (extra) => "?class_id=" + encodeURIComponent(BOOT.class_id) + (extra || "");
  const typeBadge = (t) => { const b = FG.behavior(t); return FG.badge(b.label, b.tone, b.icon); };
  const reviewBadge = (k) => { const r = FG.review(k); return FG.badge(r.label, r.tone, r.icon); };
  const pct = (c) => (c === null || c === undefined ? "—" : Math.round(c * 100) + "%");

  /* ───────────────────────── Activity context (dashboard) ───────────────────────── */
  const context = {
    current: null, active: false, busy: false,
    init() {
      const box = $("contextButtons");
      if (!box) return;
      box.addEventListener("click", (e) => { const b = e.target.closest("[data-context]"); if (b) context.set(b.dataset.context); });
      document.addEventListener("fg:snapshot", (e) => context.apply(e.detail));
    },
    apply(d) {
      context.active = !!d.class_session_active;
      context.current = d.activity_context || null;
      FG.$$("#contextButtons [data-context]").forEach((b) => {
        const on = (b.dataset.context || null) === context.current;
        b.setAttribute("aria-pressed", on ? "true" : "false");
        b.disabled = !context.active || context.busy;
      });
      const badge = $("contextBadge");
      badge.dataset.tone = context.current ? "info" : "neutral";
      badge.innerHTML = '<i class="fa-solid fa-chalkboard" aria-hidden="true"></i>' + esc(FG.contextLabel(context.current));
      $("contextHelp").textContent = !context.active ? "Bắt đầu buổi học để chọn hoạt động."
        : context.current ? "Đang áp dụng chính sách cho hoạt động “" + FG.contextLabel(context.current) + "”. Tín hiệu AI vẫn được ghi lại để giáo viên xem lại."
          : "Chưa chọn hoạt động: hệ thống dùng cách tính như trước (mọi tín hiệu đều được báo).";
    },
    async set(ctx) {
      if (context.busy || !context.active || (ctx || null) === context.current) return;
      context.busy = true;
      try {
        const res = await FG.api("/api/teacher/activity_context", { body: { class_id: BOOT.class_id, context: ctx || null } });
        FG.toast("Hoạt động lớp: " + res.label, "success");
      } catch (e) { FG.toast(e.message, "danger"); }
      finally { context.busy = false; }
    },
  };

  /* ─────────────────────────── Needs-review queue ─────────────────────────── */
  const queue = {
    init() { if ($("reviewQueue")) { FG.skeleton($("reviewQueue"), 2); queue.load(); setInterval(queue.load, 30000); } },
    async load() {
      const box = $("reviewQueue");
      try {
        const res = await FG.api("/api/teacher/review_queue" + classQuery());
        if (!res.queue.length) {
          FG.setState(box, { compact: true, icon: "fa-circle-check", title: "Không có sự kiện nào đang chờ xem lại",
                             text: "Sự kiện AI mới sẽ hiện ở đây sau khi buổi học ghi nhận." });
          return;
        }
        box.innerHTML = '<ul class="fg-list">' + res.queue.map((q) =>
          '<li class="fg-event"><span class="fg-avatar" aria-hidden="true">' + esc(FG.initials(q.name)) + "</span>" +
          '<div class="fg-event-main"><div class="fg-event-title">' + esc(q.name) + "</div>" +
          '<div class="fg-event-meta">' + esc(q.message) + " · " + q.recent_event_count + " sự kiện trong " + (res.recent_days || 7) + " ngày · tổng " +
          esc(FG.duration(q.total_event_duration)) + "</div></div>" +
          '<a class="btn btn-secondary btn-sm" href="/teacher/events' + classQuery("&student_id=" + q.student_id) +
          '" aria-label="Xem lại sự kiện của ' + esc(q.name) + '">Xem lại</a></li>').join("") + "</ul>";
      } catch (e) {
        if (e.status === 401) return;
        FG.setState(box, { error: true, compact: true, title: "Không tải được danh sách", text: e.message, retry: queue.load });
      }
    },
  };

  /* ──────────────────────────────── Sự kiện ──────────────────────────────── */
  const events = {
    rows: [], loaded: false,
    init() {
      if (!$("eventsBody")) return;
      const params = new URLSearchParams(window.location.search);
      if (params.get("student_id")) { events.pendingStudent = params.get("student_id"); $("fStatus").value = ""; }
      ["fStudent", "fDate", "fType", "fStatus", "fContext"].forEach((id) => $(id).addEventListener("change", events.load));
      $("fReset").addEventListener("click", () => {
        ["fStudent", "fDate", "fType", "fContext"].forEach((id) => { $(id).value = ""; });
        $("fStatus").value = "UNREVIEWED"; events.load();
      });
      $("eventsBody").addEventListener("click", (e) => { const b = e.target.closest("[data-open-event]"); if (b) modal.open(Number(b.dataset.openEvent)); });
      FG.skeleton($("eventsBody"), 5);
      events.load();
      events.metrics();
    },
    query() {
      const q = new URLSearchParams({ class_id: BOOT.class_id });
      const sid = $("fStudent").value || events.pendingStudent;
      if (sid) q.set("student_id", sid);
      [["fDate", "date"], ["fType", "type"], ["fStatus", "review_status"], ["fContext", "context"]].forEach(([id, k]) => { if ($(id).value) q.set(k, $(id).value); });
      return "?" + q.toString();
    },
    async load() {
      const box = $("eventsBody");
      try {
        const res = await FG.api("/api/teacher/events" + events.query());
        const sel = $("fStudent");
        if (sel.options.length <= 1) {
          res.students.forEach((s) => { const o = document.createElement("option"); o.value = s.student_id; o.textContent = s.name; sel.appendChild(o); });
          if (events.pendingStudent) { sel.value = events.pendingStudent; events.pendingStudent = null; }
        }
        events.rows = res.events;
        events.render();
      } catch (e) {
        if (e.status === 401) return;
        FG.setState(box, { error: true, title: "Không tải được sự kiện", text: e.message, retry: events.load });
      }
    },
    render() {
      const box = $("eventsBody"), rows = events.rows;
      $("eventsCount").textContent = rows.length ? rows.length + " sự kiện" : "";
      if (!rows.length) {
        FG.setState(box, { icon: "fa-list-check", title: "Không có sự kiện nào khớp bộ lọc",
                           text: "Sự kiện được ghi khi buổi học trên lớp kết thúc một tín hiệu AI (điện thoại, buồn ngủ, quay đi, rời chỗ)." });
        return;
      }
      box.innerHTML = '<div class="fg-table-wrap"><table class="fg-table is-stack"><caption class="fg-sr-only">Danh sách sự kiện AI phát hiện</caption>' +
        '<thead><tr><th scope="col">Thời điểm</th><th scope="col">Học sinh</th><th scope="col">AI phát hiện tín hiệu</th>' +
        '<th scope="col" class="is-num">Kéo dài</th><th scope="col" class="is-num">Độ tin cậy</th><th scope="col">Hoạt động</th>' +
        '<th scope="col">Trạng thái xem lại</th><th scope="col" class="is-actions"><span class="fg-sr-only">Thao tác</span></th></tr></thead><tbody>' +
        rows.map((r) =>
          "<tr><td data-label=\"Thời điểm\">" + esc(FG.dateTime(r.start_time)) + ":" + esc(FG.time(r.start_time).slice(6)) + "</td>" +
          '<td class="is-primary">' + esc(r.name) + "</td>" +
          '<td data-label="Tín hiệu">' + typeBadge(r.type) + (r.evidence ? ' <span class="fg-badge" data-tone="info"><i class="fa-solid fa-film" aria-hidden="true"></i>Có minh chứng</span>' : "") + "</td>" +
          '<td class="is-num" data-label="Kéo dài">' + esc(FG.duration(r.duration_seconds)) + "</td>" +
          '<td class="is-num" data-label="Độ tin cậy">' + pct(r.confidence) + "</td>" +
          '<td data-label="Hoạt động">' + esc(FG.contextLabel(r.activity_context)) + "</td>" +
          '<td data-label="Xem lại">' + reviewBadge(r.review_state) + "</td>" +
          '<td class="is-actions"><button type="button" class="btn btn-secondary btn-sm" data-open-event="' + r.event_id +
          '" aria-label="Xem lại sự kiện ' + esc(FG.behavior(r.type).label) + " của " + esc(r.name) + '">Xem lại</button></td></tr>').join("") +
        "</tbody></table></div>";
    },
    async metrics() {
      try {
        const m = await FG.api("/api/teacher/review_metrics" + classQuery());
        renderMetrics(m, $("feedbackBody"));
        $("rmEvents").textContent = m.overall.ai_events;
        $("rmCoverage").textContent = FG.rate(m.overall.review_coverage);
        $("rmCoverageSub").textContent = m.overall.reviewed + " / " + m.overall.ai_events + " sự kiện";
        $("rmConfirmed").textContent = m.overall.teacher_confirmed_events;
        $("rmFalse").textContent = Object.values(m.per_behavior).reduce((a, v) => a + v.false_positive_events, 0);
      } catch (e) { if (e.status !== 401) FG.setState($("feedbackBody"), { error: true, compact: true, title: "Không tải được tỉ lệ", text: e.message }); }
    },
  };

  function renderMetrics(m, box) {
    if (!box) return;
    box.innerHTML = '<div class="fg-table-wrap"><table class="fg-table is-stack"><caption class="fg-sr-only">Tỉ lệ theo phản hồi của giáo viên</caption>' +
      '<thead><tr><th scope="col">Tín hiệu</th><th scope="col" class="is-num">Sự kiện AI</th><th scope="col" class="is-num">Giáo viên xác nhận đúng</th>' +
      '<th scope="col" class="is-num">Tỉ lệ đúng</th><th scope="col" class="is-num">Tỉ lệ báo nhầm</th><th scope="col" class="is-num">Không chắc</th>' +
      '<th scope="col" class="is-num">Đã xem lại</th></tr></thead><tbody>' +
      Object.entries(m.per_behavior).map(([t, v]) =>
        '<tr><td class="is-primary">' + typeBadge(t) + "</td>" +
        '<td class="is-num" data-label="Sự kiện AI">' + v.ai_events + "</td>" +
        '<td class="is-num" data-label="Xác nhận đúng">' + v.teacher_confirmed_events + "</td>" +
        '<td class="is-num" data-label="Tỉ lệ đúng">' + FG.rate(v.confirmed_rate) + "</td>" +
        '<td class="is-num" data-label="Tỉ lệ báo nhầm">' + FG.rate(v.false_positive_rate) + "</td>" +
        '<td class="is-num" data-label="Không chắc">' + FG.rate(v.uncertain_rate) + "</td>" +
        '<td class="is-num" data-label="Đã xem lại">' + FG.rate(v.review_coverage) + "</td></tr>").join("") +
      '</tbody></table></div><p class="fg-helper px-3 pb-3 mb-0">Tỉ lệ chỉ tính khi có ít nhất ' + m.min_reviews_for_rates + ' sự kiện đã xem lại của loại đó.</p>';
  }

  /* ─────────────────────────── Event detail modal ─────────────────────────── */
  const modal = {
    event: null, player: null,
    init() {
      const el = $("eventModal");
      if (!el) return;
      FG.$$("[data-verdict]", el).forEach((b) => b.addEventListener("click", () => modal.review(b.dataset.verdict, b)));
      el.addEventListener("hidden.bs.modal", modal.stopPlayer);
    },
    async open(eventId) {
      const el = $("eventModal");
      $("eventDetail").innerHTML = '<span class="fg-muted">Đang tải…</span>';
      $("eventEvidence").innerHTML = ""; $("eventHistory").innerHTML = ""; $("reviewStatus").textContent = ""; $("reviewNote").value = "";
      window.bootstrap.Modal.getOrCreateInstance(el).show();
      try {
        const res = await FG.api("/api/teacher/events/" + eventId);
        modal.event = res.event;
        modal.render();
      } catch (e) { FG.setState($("eventDetail"), { error: true, compact: true, title: "Không tải được sự kiện", text: e.message }); }
    },
    render() {
      const e = modal.event, it = e.interpretation || {};
      $("eventModalTitle").textContent = "Xem lại: " + FG.behavior(e.type).label + " · " + e.name;
      $("eventDetail").innerHTML = '<dl class="fg-dl">' +
        "<dt>Học sinh</dt><dd>" + esc(e.name) + "</dd>" +
        "<dt>AI phát hiện tín hiệu</dt><dd>" + typeBadge(e.type) + "</dd>" +
        "<dt>Bắt đầu</dt><dd>" + esc(FG.dateTime(e.start_time)) + " (" + esc(FG.time(e.start_time)) + ")</dd>" +
        "<dt>Kéo dài</dt><dd>" + esc(FG.duration(e.duration_seconds)) + "</dd>" +
        "<dt>Độ tin cậy</dt><dd>" + pct(e.confidence) + "</dd>" +
        "<dt>Hoạt động lớp</dt><dd>" + esc(FG.contextLabel(e.activity_context)) + "</dd>" +
        "<dt>Cách hiểu theo hoạt động</dt><dd>" + esc(it.label || "AI phát hiện tín hiệu") + "</dd>" +
        "<dt>Trạng thái xem lại</dt><dd>" + reviewBadge(e.review_state) + "</dd></dl>" +
        '<p class="fg-helper mb-0">Đây là tín hiệu quan sát được, không phải kết luận về học sinh. Giáo viên là người quyết định.</p>';
      if (e.teacher_note) $("reviewNote").value = e.teacher_note;
      modal.renderEvidence();
      $("eventHistory").innerHTML = e.history && e.history.length
        ? '<p class="fg-card-title mt-3 mb-1">Lịch sử xem lại</p><ul class="fg-list">' + e.history.slice().reverse().map((h) =>
          '<li class="fg-event px-0"><div class="fg-event-main">' + reviewBadge(h.verdict) +
          (h.teacher_note ? '<div class="fg-event-meta">' + esc(h.teacher_note) + "</div>" : "") + "</div>" +
          '<span class="fg-event-time">' + esc(FG.dateTime(h.reviewed_at)) + "</span></li>").join("") + "</ul>"
        : "";
    },
    renderEvidence() {
      const ev = modal.event.evidence, box = $("eventEvidence");
      if (!ev) { box.innerHTML = '<p class="fg-helper mb-0"><i class="fa-solid fa-shield-halved me-1" aria-hidden="true"></i>Không có đoạn minh chứng cho sự kiện này (mặc định chỉ lưu thông tin sự kiện).</p>'; return; }
      box.innerHTML = '<div class="fg-evidence"><div class="fg-row-between"><div><p class="fg-card-title mb-0">Đoạn minh chứng</p>' +
        '<p class="fg-caption mb-0">Thời lượng ' + esc(FG.duration(ev.duration_seconds)) + " · lúc " + esc(FG.time(ev.clip_start)) +
        " · tự xóa sau " + esc(FG.duration(ev.expires_in_seconds)) + (ev.cropped ? " · đã cắt theo khung học sinh" : "") + "</p></div>" +
        '<div class="fg-row"><button type="button" class="btn btn-secondary" id="evShow"><i class="fa-solid fa-film" aria-hidden="true"></i>Xem đoạn minh chứng</button>' +
        '<button type="button" class="btn btn-outline-danger" id="evDelete"><i class="fa-solid fa-trash" aria-hidden="true"></i>Xóa ngay</button></div></div>' +
        '<div id="evPlayer" class="mt-2" hidden></div></div>';
      $("evShow").addEventListener("click", modal.loadClip);
      $("evDelete").addEventListener("click", modal.deleteClip);
    },
    async loadClip() {
      const ev = modal.event.evidence, btn = $("evShow");
      FG.busy(btn, true, "Đang tải…");
      try {
        const clip = await FG.api("/api/teacher/evidence/" + encodeURIComponent(ev.evidence_id));
        const box = $("evPlayer");
        box.hidden = false;
        box.innerHTML = '<div class="fg-player"><img id="evFrame" alt="Khung hình minh chứng" src="' + clip.frames[0].src + '">' +
          '<div class="fg-row mt-2"><button type="button" class="btn btn-secondary" id="evPlay" aria-pressed="false"><i class="fa-solid fa-play" aria-hidden="true"></i>Phát</button>' +
          '<span class="fg-caption fg-num" id="evPos" aria-live="off">1 / ' + clip.frames.length + "</span></div></div>";
        modal.player = { clip: clip, i: 0, timer: null };
        $("evPlay").addEventListener("click", modal.togglePlay);   // never autoplays
      } catch (e) { FG.toast(e.message, "danger"); }
      finally { FG.busy(btn, false); }
    },
    togglePlay() {
      const p = modal.player, btn = $("evPlay");
      if (!p) return;
      if (p.timer) { modal.stopPlayer(true); return; }
      btn.setAttribute("aria-pressed", "true"); btn.innerHTML = '<i class="fa-solid fa-pause" aria-hidden="true"></i>Tạm dừng';
      p.timer = setInterval(() => {
        p.i = (p.i + 1) % p.clip.frames.length;
        $("evFrame").src = p.clip.frames[p.i].src;
        $("evPos").textContent = (p.i + 1) + " / " + p.clip.frames.length;
        if (p.i === p.clip.frames.length - 1) modal.stopPlayer(true);
      }, 1000 / Math.max(0.5, p.clip.fps || 2));
    },
    stopPlayer(keep) {
      const p = modal.player;
      if (p && p.timer) { clearInterval(p.timer); p.timer = null; }
      const btn = $("evPlay");
      if (btn) { btn.setAttribute("aria-pressed", "false"); btn.innerHTML = '<i class="fa-solid fa-play" aria-hidden="true"></i>Phát'; }
      if (keep !== true) modal.player = null;
    },
    async deleteClip() {
      const ev = modal.event.evidence;
      const ok = await FG.confirm({ title: "Xóa đoạn minh chứng?", body: "Đoạn minh chứng sẽ bị xóa vĩnh viễn khỏi máy chủ. Thông tin sự kiện vẫn được giữ.",
                                    consequences: ["Không thể khôi phục.", "Thao tác được ghi vào nhật ký."], confirmLabel: "Xóa ngay", danger: true });
      if (!ok) return;
      try {
        await FG.api("/api/evidence/" + encodeURIComponent(ev.evidence_id) + "/delete", { body: {} });
        modal.stopPlayer(); modal.event.evidence = null; modal.renderEvidence();
        FG.toast("Đã xóa đoạn minh chứng.", "success");
        if (events.rows.length) events.load();
      } catch (e) { FG.toast(e.message, "danger"); }
    },
    async review(verdict, btn) {
      if (!modal.event || FG.isBusy(btn)) return;
      FG.busy(btn, true, "Đang lưu…");
      try {
        const res = await FG.api("/api/teacher/events/" + modal.event.event_id + "/review", { body: { verdict: verdict, note: $("reviewNote").value } });
        $("reviewStatus").textContent = "Đã lưu: " + res.review_label + ".";
        FG.toast("Đã lưu: " + res.review_label, "success");
        const fresh = await FG.api("/api/teacher/events/" + modal.event.event_id);
        modal.event = fresh.event; modal.render();
        if ($("eventsBody")) { events.load(); events.metrics(); }
      } catch (e) { $("reviewStatus").textContent = e.message; FG.toast(e.message, "danger"); }
      finally { FG.busy(btn, false); }
    },
  };

  /* ───────────────────────── Real classroom evaluation ───────────────────────── */
  const evaluation = {
    active: null, vocab: null, live: null, offset: 0, pack: null, runs: [],
    init() {
      if (!$("evalForm")) return;
      $("evalForm").addEventListener("submit", (e) => { e.preventDefault(); evaluation.start(); });
      $("evalEnd").addEventListener("click", evaluation.end);
      $("evalContextButtons").addEventListener("click", (e) => { const b = e.target.closest("[data-context]"); if (b) evaluation.setContext(b.dataset.context); });
      $("annForm").addEventListener("submit", (e) => { e.preventDefault(); evaluation.annotate(); });
      $("aLiveStart").addEventListener("click", evaluation.liveStart);
      $("aLiveEnd").addEventListener("click", evaluation.liveEnd);
      $("eScenario").addEventListener("change", evaluation.renderScenarioHelp);
      $("eScenarioStart").addEventListener("click", () => evaluation.scenario("start"));
      $("eScenarioStop").addEventListener("click", () => evaluation.scenario("stop"));
      $("evalHistory").addEventListener("click", (e) => {
        const m = e.target.closest("[data-metrics]"); if (m) evaluation.metrics(m.dataset.metrics);
        const r = e.target.closest("[data-resume]"); if (r) evaluation.open(r.dataset.resume);
      });
      evaluation.load();
    },
    async load() {
      try {
        if (!evaluation.vocab) {
          evaluation.vocab = await FG.api("/api/feedback/vocabulary");
          $("aLabel").innerHTML = evaluation.vocab.gt_labels.map((l) => '<option value="' + esc(l.key) + '">' + esc(l.label) + "</option>").join("");
        }
        const res = await FG.api("/api/teacher/evaluations" + classQuery());
        evaluation.setPack(res.scenario_pack);
        $("evalParticipants").textContent = res.participants.length
          ? res.participants.length + " học sinh đã đồng ý tham gia đánh giá." + (res.class_session_active ? " Buổi học đang diễn ra: kết quả AI sẽ được ghép với nhãn." : " Chưa có buổi học đang diễn ra: chỉ ghi nhãn, chưa có kết quả AI để so sánh.")
          : "Chưa có học sinh nào đồng ý phạm vi “Tham gia đánh giá thử nghiệm”. Quản trị viên cần ghi nhận đồng ý trước.";
        $("aStudent").innerHTML = res.participants.map((p) => '<option value="' + p.student_id + '">' + esc(p.name) + "</option>").join("");
        evaluation.renderHistory(res.sessions);
        const running = res.sessions.find((s) => s.status === "ACTIVE");
        if (running) evaluation.open(running.evaluation_session_id);
        else {
          $("evalActiveCard").hidden = true; $("evalStartCard").hidden = false;
          FG.loadClassCamera($("eCamera"), $("eCameraHelp"), BOOT.class_id);
        }
      } catch (e) {
        if (e.status !== 401) FG.setState($("evalHistory"), { error: true, title: "Không tải được buổi đánh giá", text: e.message, retry: evaluation.load });
      }
    },
    renderHistory(list) {
      const box = $("evalHistory");
      if (!list.length) { FG.setState(box, { icon: "fa-flask", title: "Chưa có buổi đánh giá nào", text: "Bắt đầu một buổi đánh giá có sự đồng ý để đo độ chính xác trên lớp học thật." }); return; }
      box.innerHTML = '<div class="fg-table-wrap"><table class="fg-table is-stack"><caption class="fg-sr-only">Các buổi đánh giá của lớp</caption><thead><tr><th scope="col">Bắt đầu</th><th scope="col">Camera</th><th scope="col">Loại dữ liệu</th><th scope="col">Trạng thái</th><th scope="col" class="is-actions"><span class="fg-sr-only">Thao tác</span></th></tr></thead><tbody>' +
        list.map((s) => '<tr><td class="is-primary">' + esc(FG.dateTime(s.started_at)) + "</td>" +
          '<td data-label="Camera">' + esc(s.camera_name ? s.camera_name + " · " : "") + esc(s.camera_position || "—") + (s.camera_angle ? " · " + esc(s.camera_angle) : "") + "</td>" +
          '<td data-label="Loại dữ liệu">' + esc(s.dataset_kind) + "</td>" +
          '<td data-label="Trạng thái">' + (s.status === "ACTIVE" ? FG.badge("Đang chạy", "success", "fa-circle-play") : FG.badge("Đã kết thúc", "neutral", "fa-circle-stop")) + "</td>" +
          '<td class="is-actions">' + (s.status === "ACTIVE" ? '<button type="button" class="btn btn-secondary btn-sm" data-resume="' + esc(s.evaluation_session_id) + '">Mở</button> ' : "") +
          '<button type="button" class="btn btn-secondary btn-sm" data-metrics="' + esc(s.evaluation_session_id) + '">Xem chỉ số</button> ' +
          '<a class="btn btn-secondary btn-sm" href="/api/teacher/evaluations/' + encodeURIComponent(s.evaluation_session_id) + '/export">Tải gói dữ liệu</a></td></tr>').join("") +
        "</tbody></table></div>";
    },
    formError(msg) { const el = $("evalFormError"); el.hidden = !msg; el.querySelector("span").textContent = msg || ""; },
    async start() {
      const btn = $("evalStart");
      if (FG.isBusy(btn)) return;
      if (!$("eConsent").checked) { evaluation.formError("Cần xác nhận đã có sự đồng ý trước khi bắt đầu."); $("eConsent").focus(); return; }
      evaluation.formError("");
      FG.busy(btn, true, "Đang bắt đầu…");
      try {
        const res = await FG.api("/api/teacher/evaluations", { body: {
          class_id: BOOT.class_id, camera_position: $("eCameraPosition").value, camera_angle: $("eCameraAngle").value,
          resolution: $("eResolution").value, fps_target: $("eFps").value, device: $("eDevice").value,
          lighting_condition: $("eLighting").value, activity_context: $("eContext").value, notes: $("eNotes").value,
          consent_verified: true, dataset_kind: "REAL", camera_id: $("eCamera").value || null } });
        FG.toast("Đã bắt đầu buổi đánh giá.", "success");
        evaluation.show(res);
        evaluation.load();
      } catch (e) { evaluation.formError(e.message); }
      finally { FG.busy(btn, false); }
    },
    async open(eid) {
      try { evaluation.show(await FG.api("/api/teacher/evaluations/" + encodeURIComponent(eid))); }
      catch (e) { FG.toast(e.message, "danger"); }
    },
    show(res) {
      evaluation.active = res.session;
      // server clock offset so live labels use server time
      evaluation.offset = 0;
      $("evalStartCard").hidden = true; $("evalActiveCard").hidden = false;
      $("evalActiveMeta").textContent = "Bắt đầu " + FG.dateTime(res.session.started_at) + " · " +
        (res.session.camera_name ? "Camera: " + res.session.camera_name + " · " : "") + (res.session.camera_position || "") +
        (res.session.class_session_id ? " · gắn với buổi học đang diễn ra" : " · chưa gắn buổi học");
      FG.$$("#evalContextButtons [data-context]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.context === res.session.activity_context ? "true" : "false"));
      evaluation.setPack(res.scenario_pack);
      evaluation.renderRuns(res.scenario_runs || []);
      evaluation.renderAnnotations(res.annotations);
    },
    setPack(pack) {
      if (!pack || evaluation.pack) return;
      evaluation.pack = pack;
      $("eScenario").innerHTML = pack.scenarios.map((s) => '<option value="' + esc(s.scenario_id) + '">' +
        esc(s.scenario_id.slice(0, 2) + ". " + s.title) + " (" + esc(s.suggested_minutes) + " phút)</option>").join("");
      evaluation.renderScenarioHelp();
    },
    scenarioById(id) { return evaluation.pack ? evaluation.pack.scenarios.find((s) => s.scenario_id === id) : null; },
    renderScenarioHelp() {
      const s = evaluation.scenarioById($("eScenario").value);
      if (!s) { $("eScenarioHelp").innerHTML = ""; return; }
      $("eScenarioHelp").innerHTML = '<p class="mb-1"><strong>Người tham gia:</strong> ' + esc(s.participants) +
        " · <strong>Hoạt động đề xuất:</strong> " + esc(FG.contextLabel(s.context)) + "</p>" +
        '<ol class="mb-1">' + s.steps.map((x) => "<li>" + esc(x) + "</li>").join("") + "</ol>" +
        '<p class="mb-0"><strong>Người quan sát:</strong> ' + esc(s.observer) + (s.identity_sheet ? " Cần bảng danh tính (track → học sinh)." : "") + "</p>";
    },
    renderRuns(runs) {
      evaluation.runs = runs;
      const open = runs.find((r) => !r.ended_at), t0 = evaluation.active ? evaluation.active.started_at : 0;
      $("eScenarioStart").disabled = false;
      $("eScenarioStop").disabled = !open;
      $("eScenarioState").textContent = open ? "Đang chạy kịch bản " + open.scenario_id + " từ " + FG.clock(open.started_at - t0) + "." : (runs.length ? "Không có kịch bản đang chạy." : "");
      const done = {};
      runs.forEach((r) => { done[r.scenario_id] = true; });
      const total = evaluation.pack ? evaluation.pack.scenarios.length : 0;
      $("eScenarioRuns").innerHTML = runs.length
        ? '<p class="fg-caption mb-1">Đã chạy ' + Object.keys(done).length + "/" + total + " kịch bản.</p>" +
          '<ul class="fg-list">' + runs.map((r) => '<li class="fg-event px-0"><div class="fg-event-main"><div class="fg-event-title">' + esc(r.scenario_id) +
          '</div><div class="fg-event-meta">' + FG.clock(r.started_at - t0) + " → " + (r.ended_at ? FG.clock(r.ended_at - t0) : "đang chạy") + "</div></div></li>").join("") + "</ul>"
        : "";
    },
    async scenario(action) {
      if (!evaluation.active) return;
      const btn = action === "start" ? $("eScenarioStart") : $("eScenarioStop");
      if (FG.isBusy(btn)) return;
      FG.busy(btn, true, action === "start" ? "Đang bắt đầu…" : "Đang kết thúc…");
      try {
        const res = await FG.api("/api/teacher/evaluations/" + encodeURIComponent(evaluation.active.evaluation_session_id) + "/scenario", { body: {
          action: action, scenario_id: $("eScenario").value, apply_context: $("eScenarioContext").checked } });
        evaluation.show(res);
        FG.toast(action === "start" ? "Đã bắt đầu kịch bản " + $("eScenario").value + "." : "Đã kết thúc kịch bản.", "success");
      } catch (e) { FG.toast(e.message, "danger"); }
      finally { FG.busy(btn, false); evaluation.renderRuns(evaluation.runs); }
    },
    renderAnnotations(list) {
      const box = $("annList"), names = {};
      FG.$$("#aStudent option").forEach((o) => { names[o.value] = o.textContent; });
      const labels = {};
      (evaluation.vocab ? evaluation.vocab.gt_labels : []).forEach((l) => { labels[l.key] = l.label; });
      if (!list.length) { FG.setState(box, { compact: true, icon: "fa-tag", title: "Chưa có nhãn nào" }); return; }
      const t0 = evaluation.active.started_at;
      box.innerHTML = '<div class="fg-table-wrap"><table class="fg-table is-stack"><caption class="fg-sr-only">Nhãn thực tế do người quan sát ghi</caption><thead><tr><th scope="col">Học sinh</th><th scope="col">Nhãn</th><th scope="col">Từ</th><th scope="col">Đến</th><th scope="col">Ghi chú</th></tr></thead><tbody>' +
        list.map((a) => '<tr><td class="is-primary">' + esc(names[a.student_id] || "#" + a.student_id) + "</td>" +
          '<td data-label="Nhãn">' + esc(labels[a.label] || a.label) + "</td>" +
          '<td data-label="Từ" class="fg-num">' + FG.clock(a.started_at - t0) + "</td>" +
          '<td data-label="Đến" class="fg-num">' + FG.clock(a.ended_at - t0) + "</td>" +
          '<td data-label="Ghi chú">' + esc(a.note || "") + "</td></tr>").join("") + "</tbody></table></div>";
    },
    async setContext(ctx) {
      if (!evaluation.active) return;
      try {
        await FG.api("/api/teacher/evaluations/" + encodeURIComponent(evaluation.active.evaluation_session_id) + "/context", { body: { context: ctx } });
        evaluation.active.activity_context = ctx;
        FG.$$("#evalContextButtons [data-context]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.context === ctx ? "true" : "false"));
        FG.toast("Hoạt động: " + FG.contextLabel(ctx), "success");
      } catch (e) { FG.toast(e.message, "danger"); }
    },
    parseOffset(v) {
      const m = String(v || "").trim().match(/^(\d{1,3}):(\d{1,2})$/);
      if (m) return Number(m[1]) * 60 + Number(m[2]);
      return /^\d+$/.test(String(v || "").trim()) ? Number(v) : null;
    },
    liveStart() {
      evaluation.live = Date.now() / 1000;
      $("aLiveStart").disabled = true; $("aLiveEnd").disabled = false;
      $("aLiveState").textContent = "Đang ghi nhãn từ " + FG.time(evaluation.live) + "…";
    },
    liveEnd() {
      if (!evaluation.live) return;
      const t0 = evaluation.active.started_at;
      $("aStart").value = FG.clock(evaluation.live - t0); $("aEnd").value = FG.clock(Date.now() / 1000 - t0);
      evaluation.live = null;
      $("aLiveStart").disabled = false; $("aLiveEnd").disabled = true; $("aLiveState").textContent = "Kiểm tra rồi bấm Lưu nhãn.";
    },
    async annotate() {
      const btn = $("aSave");
      if (!evaluation.active || FG.isBusy(btn)) return;
      const a = evaluation.parseOffset($("aStart").value), b = evaluation.parseOffset($("aEnd").value);
      $("aStart").setAttribute("aria-invalid", a === null ? "true" : "false");
      $("aEnd").setAttribute("aria-invalid", b === null || (a !== null && b <= a) ? "true" : "false");
      if (a === null || b === null || b <= a) {
        const msg = "Nhập thời gian dạng phút:giây, thời điểm kết thúc sau thời điểm bắt đầu.";
        $("aLiveState").textContent = msg; FG.toast(msg, "warning");
        (a === null ? $("aStart") : $("aEnd")).focus();
        return;
      }
      FG.busy(btn, true, "Đang lưu…");
      const t0 = evaluation.active.started_at;
      try {
        await FG.api("/api/teacher/evaluations/" + encodeURIComponent(evaluation.active.evaluation_session_id) + "/annotations", { body: {
          student_id: Number($("aStudent").value), label: $("aLabel").value, started_at: t0 + a, ended_at: t0 + b, note: $("aNote").value } });
        $("aNote").value = ""; $("aLiveState").textContent = "Đã lưu nhãn.";
        evaluation.open(evaluation.active.evaluation_session_id);
      } catch (e) { FG.toast(e.message, "danger"); }
      finally { FG.busy(btn, false); }
    },
    async end() {
      if (!evaluation.active) return;
      const ok = await FG.confirm({ title: "Kết thúc buổi đánh giá?", body: "Không thể thêm hoạt động mới sau khi kết thúc. Vẫn có thể xem chỉ số và tải gói dữ liệu.", confirmLabel: "Kết thúc đánh giá", danger: true });
      if (!ok) return;
      try {
        await FG.api("/api/teacher/evaluations/" + encodeURIComponent(evaluation.active.evaluation_session_id) + "/end", { body: {} });
        evaluation.active = null; FG.toast("Đã kết thúc buổi đánh giá.", "success"); evaluation.load();
      } catch (e) { FG.toast(e.message, "danger"); }
    },
    async metrics(eid) {
      const card = $("evalMetricsCard"), box = $("evalMetricsBody");
      card.hidden = false; FG.skeleton(box, 3);
      try {
        const r = await FG.api("/api/teacher/evaluations/" + encodeURIComponent(eid) + "/metrics");
        $("evalMetricsSub").textContent = "Loại dữ liệu " + r.dataset_kind + " · " + (r.is_real_world_evidence ? "đủ điều kiện làm bằng chứng thực tế cho buổi này" : "chưa đủ dữ liệu để kết luận độ chính xác") +
          " · thời gian có nhãn " + FG.duration(r.behavior.annotated_student_seconds) + " (cộng theo học sinh)";
        // rates (0..1) are shown as %, everything else (FPS, ms, counts) as a plain number
        const v = (x) => (FG.notEnough(x) ? "Chưa đủ dữ liệu" : x === null || x === undefined ? "—" : typeof x === "number" ? Math.round(x * 100) + "%" : esc(JSON.stringify(x)));
        const n = (x) => (FG.notEnough(x) ? "Chưa đủ dữ liệu" : x === null || x === undefined ? "—" : typeof x === "number" ? String(x) : esc(JSON.stringify(x)));
        const rows = Object.entries(r.behavior.per_behavior_context_aware).map(([t, m]) =>
          '<tr><td class="is-primary">' + typeBadge(t) + '</td><td class="is-num" data-label="Precision">' + v(m.precision) + '</td><td class="is-num" data-label="Recall">' + v(m.recall) +
          '</td><td class="is-num" data-label="F1">' + v(m.f1) + '</td><td class="is-num" data-label="Báo nhầm">' + m.false_positives +
          '</td><td class="is-num" data-label="Báo nhầm / giờ">' + (FG.notEnough(m.false_positives_per_hour) ? "Chưa đủ dữ liệu" : m.false_positives_per_hour) +
          '</td><td class="is-num" data-label="Bỏ sót">' + m.false_negatives + "</td></tr>").join("");
        box.innerHTML = '<div class="fg-table-wrap"><table class="fg-table is-stack"><caption class="fg-sr-only">Chỉ số theo tín hiệu (đã áp dụng hoạt động)</caption><thead><tr><th scope="col">Tín hiệu</th><th scope="col" class="is-num">Precision</th><th scope="col" class="is-num">Recall</th><th scope="col" class="is-num">F1</th><th scope="col" class="is-num">Báo nhầm</th><th scope="col" class="is-num">Báo nhầm / giờ</th><th scope="col" class="is-num">Bỏ sót</th></tr></thead><tbody>' +
          rows + '</tbody></table></div><p class="fg-helper px-3 pb-3 mb-0">Danh tính: ' + (r.identity.status === "OK" ? "độ chính xác " + v(r.identity.identity_match_accuracy) + ", " + r.identity.identity_switch_count + " lần đổi danh tính" : "Chưa đủ dữ liệu") +
          ". Kịch bản đã chạy: " + r.scenarios.run.length + "/" + r.scenarios.pack_scenarios + (r.scenarios.not_run.length ? " (chưa chạy: " + esc(r.scenarios.not_run.join(", ")) + ")" : "") +
          ". Hiệu năng: " + (r.performance.status === "OK" ? n(r.performance.fps) + " FPS, p95 " + n(r.performance.latency_p95_ms) + " ms" : "Chưa đủ dữ liệu") + ".</p>";
      } catch (e) { FG.setState(box, { error: true, compact: true, title: "Không tính được chỉ số", text: e.message }); }
    },
  };

  /* ─────────────────────────── Analytics: AI vs teacher ─────────────────────────── */
  const aiVsTeacher = {
    async init() {
      const box = $("aiVsTeacherBody");
      if (!box) return;
      FG.skeleton(box, 3);
      try { renderMetrics(await FG.api("/api/teacher/review_metrics" + classQuery()), box); }
      catch (e) { if (e.status !== 401) FG.setState(box, { error: true, compact: true, title: "Không tải được số liệu", text: e.message }); }
    },
  };

  document.addEventListener("DOMContentLoaded", () => {
    context.init(); queue.init(); events.init(); modal.init(); evaluation.init(); aiVsTeacher.init();
  });
})();
