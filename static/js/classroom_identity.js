/* Giám sát › Danh sách lớp bằng camera: clickable person boxes over the classroom camera,
 * roster with identity / enrollment / attendance, tag / correct / remove, initialization phase.
 *
 * The browser only displays what the server returns. Who tagged (confirmed_by) and the identity
 * source are decided on the server; no face data reaches the browser (no face vectors, no crops).
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = FG.esc;
  const bootEl = $("fgBoot");
  if (!bootEl || !$("identityCard")) return;
  const BOOT = JSON.parse(bootEl.textContent);
  const S = { data: null, timer: null, track: null, chosen: null, lastFocus: null, busy: false };

  const SOURCE = { TEACHER_TAG: "Giáo viên gắn", TEACHER_CORRECTED: "Giáo viên sửa", AUTO_FACE: "AI nhận ra",
                   AUTO_RECOVERED: "AI nhận lại" };
  const ENROLL_TONE = { READY: "success", PARTIAL: "info", NEEDS_MORE_SAMPLES: "warning", NOT_ENROLLED: "neutral",
                        NEEDS_REVIEW: "danger" };

  function trackLabel(t) {
    if (t.review_required) return { tone: "danger", text: (t.student_name || "") + " · cần xem lại", icon: "fa-triangle-exclamation" };
    if (t.student_id && t.restored) return { tone: "success", text: t.student_name + " · khôi phục theo vị trí", icon: "fa-rotate" };
    if (t.student_id && t.teacher_locked) return { tone: "success", text: t.student_name, icon: "fa-user-check" };
    if (t.student_id) return { tone: "info", text: "AI: " + t.student_name, icon: "fa-wand-magic-sparkles" };
    if (t.suggestion) return { tone: "warning", text: "Gợi ý: " + t.suggestion.student_name, icon: "fa-circle-question" };
    return { tone: "neutral", text: "Chưa xác định", icon: "fa-user" };
  }

  /* Place the overlay exactly over the displayed image (object-fit: contain letterboxes it). */
  function placeOverlay() {
    const img = $("cameraFeed"), layer = $("trackOverlay"), box = $("cameraBox");
    if (!img || img.hidden || !img.naturalWidth) { layer.hidden = true; return false; }
    const bw = box.clientWidth, bh = box.clientHeight, ir = img.naturalWidth / img.naturalHeight;
    let w = bw, h = bw / ir;
    if (h > bh) { h = bh; w = bh * ir; }
    layer.style.left = (bw - w) / 2 + "px"; layer.style.top = (bh - h) / 2 + "px";
    layer.style.width = w + "px"; layer.style.height = h + "px";
    return true;
  }

  function renderTracks() {
    const layer = $("trackOverlay"), d = S.data;
    if (!d || !d.camera_open || !d.tracks.length || !placeOverlay()) { layer.hidden = true; layer.innerHTML = ""; return; }
    layer.hidden = false;
    const html = d.tracks.filter((t) => t.box).map((t) => {
      const l = trackLabel(t), [x, y, w, h] = t.box;
      return '<button type="button" class="fg-track" data-tone="' + l.tone + '" data-track="' + t.track_id + '"' +
        ' style="left:' + (x * 100) + "%;top:" + (y * 100) + "%;width:" + (w * 100) + "%;height:" + (h * 100) + '%"' +
        ' aria-label="Người #' + t.track_id + ": " + esc(l.text) + (t.enrolling ? ", đang tạo mẫu nhận diện" : "") + '">' +
        '<span class="fg-track-tag"><i class="fa-solid ' + l.icon + '" aria-hidden="true"></i>#' + t.track_id + " " + esc(l.text) +
        (t.enrolling ? ' <i class="fa-solid fa-circle-notch fa-spin" aria-hidden="true"></i>' : "") + "</span></button>";
    }).join("");
    if (layer._sig !== html) { layer.innerHTML = html; layer._sig = html; }
  }

  function renderRoster() {
    const d = S.data, card = $("identityCard");
    const show = d && d.active && d.mode === "classroom";
    card.hidden = !show;
    if (!show) return;
    const init = d.phase === "INITIALIZING";
    $("identityPhase").textContent = init ? "Đang khởi tạo danh sách: chưa chấm điểm, chưa ghi sự kiện."
      : "Đang theo dõi buổi học. Có thể sửa tên bất cứ lúc nào bằng cách bấm vào người trên hình.";
    $("initBtn").hidden = init; $("beginBtn").hidden = !init;
    $("confirmSuggestBtn").disabled = !d.camera_open;
    const c = d.counts || {};
    $("identityCounts").textContent = "Đã xác nhận: " + (c.confirmed || 0) + " · Chưa xác định: " + (c.unidentified || 0) +
      " · Chưa thấy: " + (c.not_seen || 0) + (d.camera_open ? "" : " · Mở camera ở trên để gắn tên");
    const warn = $("identityWarning");
    warn.hidden = !(d.warnings && d.warnings.length);
    warn.querySelector("span").textContent = (d.warnings || []).join(" ");
    const html = d.roster.map((r) => {
      const e = r.enrollment || {}, att = FG.attendance(r.attendance_status);
      const who = r.track_id ? (r.review_required ? FG.badge("Cần xem lại · Người #" + r.track_id, "danger", "fa-triangle-exclamation")
        : r.restored ? FG.badge("Khôi phục theo vị trí · Người #" + r.track_id, "success", "fa-rotate")
        : FG.badge((SOURCE[r.identity_source] || "Đã gắn") + " · Người #" + r.track_id, r.identity_source && r.identity_source.indexOf("TEACHER") === 0 ? "success" : "info", "fa-user-check"))
        : r.awaiting_restore ? FG.badge("Đã gắn tên · chờ thấy lại (gắn lại nếu cần)", "warning", "fa-hourglass-half")
        : FG.badge("Chưa gán", "neutral", "fa-user");
      const enroll = r.face_consent ? FG.badge(e.summary + " · " + e.label, ENROLL_TONE[e.status] || "neutral", "fa-id-badge")
        : FG.badge("Không có đồng ý nhận diện · chỉ trong buổi", "neutral", "fa-user-lock");
      return '<li class="fg-identity-row"><span class="fg-person-name">' + esc(r.name) + "</span>" +
        '<span class="fg-row">' + who + enroll + FG.badge(att.label + (r.attendance_source === "TEACHER_CORRECTED" ? " · GV sửa" : ""), att.tone, att.icon) +
        (r.enrolling ? FG.badge("Đang tạo mẫu", "info", "fa-circle-notch") : "") + "</span></li>";
    }).join("");
    const list = $("identityRoster");
    if (list._sig !== html) { list.innerHTML = html; list._sig = html; }
  }

  async function poll() {
    clearTimeout(S.timer);
    try {
      S.data = await FG.api("/api/teacher/classroom/identity?class_id=" + encodeURIComponent(BOOT.class_id));
      renderRoster(); renderTracks();
      if (S.track !== null && $("tagModal").classList.contains("show")) refreshModal();
    } catch (e) { /* the dashboard shows connection problems; try again */ }
    S.timer = setTimeout(poll, document.hidden ? 5000 : 1500);
  }

  /* ------------------------------------------------------------ tag modal */
  const modal = () => window.bootstrap.Modal.getOrCreateInstance($("tagModal"));
  function trackById(id) { return S.data && S.data.tracks.find((t) => t.track_id === id); }

  function openTag(trackId, opener) {
    S.track = trackId; S.chosen = null; S.lastFocus = opener || document.activeElement;
    $("tagError").hidden = true; $("tagStatus").textContent = ""; $("tagSearch").value = "";
    refreshModal(true);
    modal().show();
  }

  function refreshModal(first) {
    const t = trackById(S.track);
    if (!t) { $("tagCurrent").textContent = "Người #" + S.track + " không còn trong khung hình."; $("tagSave").disabled = true; return; }
    $("tagTitle").textContent = "Người #" + t.track_id;
    $("tagCurrent").textContent = t.student_id
      ? "Hiện tại: " + t.student_name + " (" + (SOURCE[t.source] || "đã gắn") + ")" + (t.review_required ? " — AI thấy khuôn mặt giống học sinh khác, hãy kiểm tra lại." : "")
      : "Chưa xác định danh tính." + (t.face_too_small ? " Khuôn mặt quá nhỏ trong khung hình để tạo mẫu nhận diện." : "");
    const sug = !t.teacher_locked && t.suggestion ? t.suggestion : null;
    $("tagSuggestion").hidden = !sug;
    if (sug) $("tagSuggestionText").textContent = "AI đề xuất: " + sug.student_name + " (độ tin cậy " + Math.round(sug.confidence * 100) + "%)";
    $("tagRemove").hidden = !t.student_id;
    $("tagSave").textContent = t.student_id ? "Đổi học sinh" : "Gắn tên";
    if (first) renderPicker();
  }

  function renderPicker() {
    const q = $("tagSearch").value.trim().toLowerCase();
    const rows = (S.data ? S.data.roster : []).filter((r) => !q || r.name.toLowerCase().indexOf(q) >= 0);
    $("tagList").innerHTML = rows.map((r) => {
      const taken = r.track_id && r.track_id !== S.track;
      return '<label class="fg-tag-option' + (taken ? " is-taken" : "") + '"><input type="radio" name="tagStudent" value="' + r.student_id + '"' +
        (taken ? " disabled" : "") + (S.chosen === r.student_id ? " checked" : "") + "> <span>" + esc(r.name) + "</span>" +
        (taken ? '<span class="fg-caption"> — đã gắn cho Người #' + r.track_id + "</span>" : "") +
        (!r.face_consent ? '<span class="fg-caption"> — chỉ trong buổi (không có đồng ý nhận diện)</span>' : "") + "</label>";
    }).join("") || '<p class="fg-caption mb-0">Không tìm thấy học sinh.</p>';
  }

  async function send(url, body, okMsg) {
    if (S.busy) return null;
    S.busy = true;
    $("tagError").hidden = true;
    try {
      const res = await FG.api(url, { body: Object.assign({ class_id: BOOT.class_id }, body) });
      $("tagStatus").textContent = okMsg(res);
      FG.toast(okMsg(res), "success");
      await poll();
      return res;
    } catch (e) {
      $("tagError").hidden = false; $("tagError").querySelector("span").textContent = e.message;
      return null;
    } finally { S.busy = false; }
  }

  const tagged = (res) => (res.source === "TEACHER_CORRECTED" ? "Đã sửa thành " : "Đã gắn ") + res.student_name +
    (res.enrollment === "BURST_STARTED" ? " · đang tạo mẫu nhận diện (vài giây)" : " · chỉ trong buổi học này");

  $("tagForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const sel = document.querySelector('input[name="tagStudent"]:checked');
    if (!sel) { $("tagError").hidden = false; $("tagError").querySelector("span").textContent = "Hãy chọn một học sinh."; return; }
    const res = await send("/api/teacher/classroom/tag", { track_id: S.track, student_id: Number(sel.value) }, tagged);
    if (res) modal().hide();
  });
  $("tagAccept").addEventListener("click", async () => {
    const t = trackById(S.track);
    if (!t || !t.suggestion) return;
    const res = await send("/api/teacher/classroom/tag", { track_id: S.track, student_id: t.suggestion.student_id }, tagged);
    if (res) modal().hide();
  });
  $("tagOther").addEventListener("click", () => { $("tagSearch").focus(); });
  $("tagRemove").addEventListener("click", async () => {
    const res = await send("/api/teacher/classroom/untag", { track_id: S.track }, () => "Đã gỡ gắn tên.");
    if (res) modal().hide();
  });
  $("tagSearch").addEventListener("input", renderPicker);
  $("tagList").addEventListener("change", (e) => { if (e.target.name === "tagStudent") S.chosen = Number(e.target.value); });
  $("tagModal").addEventListener("hidden.bs.modal", () => {
    S.track = null;
    const back = S.lastFocus && document.body.contains(S.lastFocus) ? S.lastFocus : $("identityTitle");
    if (back && back.focus) back.focus();
  });

  $("trackOverlay").addEventListener("click", (e) => {
    const b = e.target.closest("[data-track]");
    if (b) openTag(Number(b.dataset.track), b);
  });
  $("beginBtn").addEventListener("click", async () => {
    FG.busy($("beginBtn"), true, "Đang bắt đầu…");
    try { const r = await FG.api("/api/teacher/classroom/begin", { body: { class_id: BOOT.class_id } }); FG.toast(r.message, "success"); await poll(); }
    catch (e) { FG.toast(e.message, "danger"); } finally { FG.busy($("beginBtn"), false); }
  });
  $("initBtn").addEventListener("click", async () => {
    const ok = await FG.confirm({ title: "Khởi tạo lại danh sách?", confirmLabel: "Khởi tạo danh sách",
      body: "Tạm dừng chấm điểm để gắn tên học sinh trên hình. Không ai bị trừ điểm trong lúc này." });
    if (!ok) return;
    try { await FG.api("/api/teacher/classroom/initialize", { body: { class_id: BOOT.class_id } }); await poll(); }
    catch (e) { FG.toast(e.message, "danger"); }
  });
  $("confirmSuggestBtn").addEventListener("click", async () => {
    FG.busy($("confirmSuggestBtn"), true, "Đang xác nhận…");
    try { const r = await FG.api("/api/teacher/classroom/confirm_suggestions", { body: { class_id: BOOT.class_id } }); FG.toast(r.message, "success"); await poll(); }
    catch (e) { FG.toast(e.message, "danger"); } finally { FG.busy($("confirmSuggestBtn"), false); }
  });
  window.addEventListener("resize", renderTracks);
  $("cameraFeed").addEventListener("load", renderTracks);
  poll();
})();
