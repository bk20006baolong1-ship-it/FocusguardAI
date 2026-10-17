/* Admin > Dữ liệu & quyền riêng tư. Consent is recorded and enforced by the
 * server; this page only shows state and asks for actions. Every destructive
 * action asks for confirmation in the page dialog. */
(function () {
  "use strict";
  const FG = window.FG;
  const $ = (id) => document.getElementById(id);
  const esc = FG.esc;
  const STATE = {
    ACTIVE: { tone: "success", icon: "fa-circle-check" },
    NOT_PROVIDED: { tone: "neutral", icon: "fa-circle-minus" },
    EXPIRED: { tone: "warning", icon: "fa-hourglass-end" },
    REVOKED: { tone: "danger", icon: "fa-ban" },
  };
  const SCOPES = { face_recognition: "Khuôn mặt", event_evidence: "Minh chứng", evaluation: "Đánh giá" };
  const S = { rows: [], editing: null };

  async function load() {
    try {
      const res = await FG.api("/api/admin/privacy/students");
      S.rows = res.students;
      $("privacySub").textContent = res.students.length + " học sinh · " +
        res.students.filter((r) => r.consent_state === "ACTIVE").length + " có đồng ý còn hiệu lực · " +
        res.students.filter((r) => r.face_without_consent).length + " có dữ liệu khuôn mặt nhưng chưa có đồng ý (không được dùng để nhận diện)";
      render();
    } catch (e) {
      if (e.status !== 401) FG.setState($("privacyBody"), { error: true, title: "Không tải được dữ liệu", text: e.message, retry: load });
    }
  }

  function render() {
    const box = $("privacyBody");
    const q = $("privacySearch").value.trim().toLowerCase();
    const rows = S.rows.filter((r) => !q || String(r.name || "").toLowerCase().indexOf(q) !== -1);
    if (!rows.length) { FG.setState(box, { compact: true, icon: "fa-user-lock", title: "Không có học sinh nào" }); return; }
    box.innerHTML = '<div class="fg-table-wrap"><table class="fg-table is-stack"><caption class="fg-sr-only">Trạng thái đồng ý và dữ liệu của từng học sinh</caption>' +
      '<thead><tr><th scope="col">Học sinh</th><th scope="col">Đồng ý</th><th scope="col">Phạm vi</th><th scope="col">Dữ liệu khuôn mặt</th>' +
      '<th scope="col" class="is-num">Minh chứng</th><th scope="col">Hết hạn</th><th scope="col" class="is-actions"><span class="fg-sr-only">Thao tác</span></th></tr></thead><tbody>' +
      rows.map((r) => {
        const st = STATE[r.consent_state] || STATE.NOT_PROVIDED;
        const scopes = Object.keys(SCOPES).filter((k) => r.scopes[k]).map((k) => SCOPES[k]).join(", ") || "—";
        const face = r.face_embedding_exists
          ? FG.badge(r.face_without_consent ? "Có, chưa có đồng ý" : "Có", r.face_without_consent ? "warning" : "success", r.face_without_consent ? "fa-triangle-exclamation" : "fa-face-smile") +
            '<div class="fg-helper mb-0">Đăng ký ' + esc(FG.dateTime(r.last_enrollment)) + (r.face_expires_at ? " · tự xóa " + esc(FG.date(r.face_expires_at)) : "") + "</div>"
          : '<span class="fg-muted">Không có</span>';
        const name = esc(r.name);
        return '<tr><td class="is-primary">' + name + '<div class="fg-helper mb-0">' + esc(r.class_name || "") + "</div></td>" +
          '<td data-label="Đồng ý">' + FG.badge(r.consent_label, st.tone, st.icon) +
          (r.guardian_name ? '<div class="fg-helper mb-0">' + esc(r.guardian_name) + " (" + esc(r.guardian_relationship || "") + ")</div>" : "") +
          (r.consent_policy_version ? '<div class="fg-helper mb-0">Chính sách ' + esc(r.consent_policy_version) + "</div>" : "") +
          (r.policy_outdated ? FG.badge("Chính sách đã đổi: cần ghi nhận lại", "warning", "fa-rotate") : "") + "</td>" +
          '<td data-label="Phạm vi">' + esc(scopes) + "</td>" +
          '<td data-label="Khuôn mặt">' + face + "</td>" +
          '<td class="is-num" data-label="Minh chứng">' + r.evidence_count + "</td>" +
          '<td data-label="Hết hạn">' + (r.expires_at ? esc(FG.date(r.expires_at)) : "—") + "</td>" +
          '<td class="is-actions"><div class="fg-row" style="justify-content: flex-end;">' +
          '<button type="button" class="btn btn-secondary btn-sm" data-act="consent" data-id="' + r.student_id + '" aria-label="Ghi nhận đồng ý cho ' + name + '">Ghi nhận đồng ý</button>' +
          (r.consent_state === "ACTIVE" ? '<button type="button" class="btn btn-outline-danger btn-sm" data-act="revoke" data-id="' + r.student_id + '" aria-label="Thu hồi đồng ý của ' + name + '">Thu hồi đồng ý</button>' : "") +
          (r.face_embedding_exists ? '<button type="button" class="btn btn-outline-danger btn-sm" data-act="face" data-id="' + r.student_id + '" aria-label="Xóa dữ liệu khuôn mặt của ' + name + '">Xóa face embedding</button>' : "") +
          (r.evidence_count ? '<button type="button" class="btn btn-outline-danger btn-sm" data-act="evidence" data-id="' + r.student_id + '" aria-label="Xóa minh chứng của ' + name + '">Xóa evidence</button>' : "") +
          '<a class="btn btn-secondary btn-sm" href="/api/export/student/' + r.student_id + '?format=json" aria-label="Xuất dữ liệu của ' + name + ' (JSON)">Xuất JSON</a>' +
          '<a class="btn btn-secondary btn-sm" href="/api/export/student/' + r.student_id + '?format=csv&section=events" aria-label="Xuất sự kiện của ' + name + ' (CSV)">Xuất CSV</a>' +
          "</div></td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  const byId = (id) => S.rows.find((r) => r.student_id === Number(id));

  async function act(kind, id) {
    const r = byId(id);
    if (!r) return;
    if (kind === "consent") { openConsent(r); return; }
    const plan = {
      revoke: { title: "Thu hồi đồng ý của " + r.name + "?", url: "/api/consent/" + id + "/revoke", label: "Thu hồi đồng ý",
                body: "Nhận diện khuôn mặt dừng ngay cho học sinh này.",
                list: ["Vector khuôn mặt bị xóa.", "Đoạn minh chứng liên quan bị xóa theo chính sách.", "Dữ liệu học tập khác được giữ theo thời hạn lưu trữ.", "Thao tác được ghi nhật ký."] },
      face: { title: "Xóa dữ liệu khuôn mặt của " + r.name + "?", url: "/api/admin/privacy/" + id + "/delete_face", label: "Xóa face embedding",
              body: "Camera lớp học sẽ không nhận ra học sinh này cho tới khi đăng ký lại (cần đồng ý còn hiệu lực).", list: ["Không thể khôi phục.", "Thao tác được ghi nhật ký."] },
      evidence: { title: "Xóa toàn bộ đoạn minh chứng của " + r.name + "?", url: "/api/admin/privacy/" + id + "/delete_evidence", label: "Xóa evidence",
                  body: "Thông tin sự kiện và xác nhận của giáo viên vẫn được giữ.", list: ["Không thể khôi phục.", "Thao tác được ghi nhật ký."] },
    }[kind];
    const ok = await FG.confirm({ title: plan.title, body: plan.body, consequences: plan.list, confirmLabel: plan.label, danger: true });
    if (!ok) return;
    try {
      const res = await FG.api(plan.url, { body: { reason: "admin_dashboard" } });
      FG.toast(res.message || "Đã thực hiện.", "success");
      load(); audit();
    } catch (e) { FG.toast(e.message, "danger"); }
  }

  function formError(msg) { const el = $("consentFormError"); el.hidden = !msg; el.querySelector("span").textContent = msg || ""; }

  function openConsent(r) {
    S.editing = r;
    $("consentModalTitle").textContent = "Ghi nhận đồng ý: " + r.name;
    ["cGuardian", "cRelation", "cExpires", "cNote"].forEach((k) => { $(k).value = ""; });
    $("cAdult").checked = false; $("cGuardianFields").hidden = false;
    FG.$$("[data-scope]").forEach((c) => { c.checked = c.dataset.scope === "face_recognition"; });
    formError("");
    window.bootstrap.Modal.getOrCreateInstance($("consentModal")).show();
  }

  async function saveConsent(e) {
    e.preventDefault();
    const btn = $("consentSave");
    if (FG.isBusy(btn) || !S.editing) return;
    const scopes = {};
    FG.$$("[data-scope]").forEach((c) => { scopes[c.dataset.scope] = c.checked; });
    if (!Object.values(scopes).some(Boolean)) { formError("Cần chọn ít nhất một phạm vi đồng ý."); return; }
    const adult = $("cAdult").checked;
    if (!adult && (!$("cGuardian").value.trim() || !$("cRelation").value.trim())) { formError("Cần họ tên và quan hệ của phụ huynh/người giám hộ."); return; }
    const exp = $("cExpires").value ? new Date($("cExpires").value + "T23:59:59").getTime() / 1000 : null;
    FG.busy(btn, true, "Đang lưu…");
    try {
      await FG.api("/api/admin/consent/" + S.editing.student_id, { body: {
        guardian_name: $("cGuardian").value, guardian_relationship: $("cRelation").value, student_is_adult: adult,
        scopes: scopes, expires_at: exp, note: $("cNote").value } });
      window.bootstrap.Modal.getOrCreateInstance($("consentModal")).hide();
      FG.toast("Đã ghi nhận đồng ý.", "success");
      load(); audit();
    } catch (err) { formError(err.message); }
    finally { FG.busy(btn, false); }
  }

  async function audit() {
    const box = $("auditBody"), action = $("auditAction").value;
    try {
      const res = await FG.api("/api/admin/audit" + (action ? "?action=" + encodeURIComponent(action) : ""));
      const names = {};
      S.rows.forEach((r) => { names[r.student_id] = r.name; });
      if (!res.entries.length) { FG.setState(box, { compact: true, icon: "fa-clipboard-list", title: "Chưa có thao tác nào" }); return; }
      box.innerHTML = '<ul class="fg-list">' + res.entries.map((a) =>
        '<li class="fg-event"><div class="fg-event-main"><div class="fg-event-title">' + esc(a.action_label) + "</div>" +
        '<div class="fg-event-meta">' + (a.student_id ? "Học sinh: " + esc(names[a.student_id] || "#" + a.student_id) + " · " : "") +
        "Người thực hiện: " + esc(a.actor_role === "system" ? "hệ thống" : (a.actor_role || "") + " #" + (a.actor_id || "")) + "</div></div>" +
        '<span class="fg-event-time">' + esc(FG.dateTime(a.timestamp)) + "</span></li>").join("") + "</ul>";
    } catch (e) { if (e.status !== 401) FG.setState(box, { error: true, compact: true, title: "Không tải được nhật ký", text: e.message, retry: audit }); }
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!$("privacyBody")) return;
    FG.skeleton($("privacyBody"), 4); FG.skeleton($("auditBody"), 3);
    $("privacySearch").addEventListener("input", render);
    $("privacyBody").addEventListener("click", (e) => { const b = e.target.closest("[data-act]"); if (b) act(b.dataset.act, b.dataset.id); });
    $("consentForm").addEventListener("submit", saveConsent);
    $("cAdult").addEventListener("change", () => { $("cGuardianFields").hidden = $("cAdult").checked; });
    $("auditAction").addEventListener("change", audit);
    $("retentionRun").addEventListener("click", async () => {
      const btn = $("retentionRun");
      if (FG.isBusy(btn)) return;
      FG.busy(btn, true, "Đang dọn…");
      try {
        const res = await FG.api("/api/admin/retention/run", { body: {} });
        const r = res.report;
        FG.toast("Đã dọn: " + r.evidence_deleted + " minh chứng, " + r.faces_deleted + " dữ liệu khuôn mặt, " + r.events_deleted + " sự kiện cũ.", "success");
        load(); audit();
      } catch (e) { FG.toast(e.message, "danger"); }
      finally { FG.busy(btn, false); }
    });
    load().then(audit);
  });
})();
