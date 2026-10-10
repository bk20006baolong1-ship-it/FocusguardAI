/* Admin pages: accounts, classes, face enrolment.
 * Every action is authorised again on the server; hiding a button here is
 * only a convenience, never a permission check. */
(function () {
  "use strict";
  const FG = window.FG;
  const bootEl = document.getElementById("fgBoot");
  if (!bootEl) return;
  const PAGE = JSON.parse(bootEl.textContent).page;
  const $ = (id) => document.getElementById(id);
  const esc = FG.esc;

  const ROLE = {
    student: { label: "Học sinh", tone: "primary", icon: "fa-user" },
    teacher: { label: "Giáo viên", tone: "info", icon: "fa-chalkboard-user" },
    parent:  { label: "Phụ huynh", tone: "success", icon: "fa-user-group" },
    admin:   { label: "Quản trị viên", tone: "warning", icon: "fa-user-shield" },
  };

  function fieldError(input, bad) {
    input.classList.toggle("is-invalid", bad);
    input.setAttribute("aria-invalid", bad ? "true" : "false");
    const err = input.closest(".fg-field").querySelector(".fg-error-text");
    if (err) err.hidden = !bad;
    return !bad;
  }
  function formError(id, message) {
    const box = $(id);
    box.hidden = !message;
    box.querySelector("span").textContent = message || "";
  }

  /* ───────────────────────────── Tài khoản ───────────────────────────── */
  const accounts = {
    users: [], classes: [], editing: null,

    init() {
      $("userAdd").addEventListener("click", () => accounts.open(null));
      $("userSearch").addEventListener("input", accounts.render);
      $("userRole").addEventListener("change", accounts.render);
      $("usersBody").addEventListener("click", accounts.onAction);
      $("uRole").addEventListener("change", accounts.roleFields);
      $("userForm").addEventListener("submit", accounts.save);
      ["uDisplay", "uUsername", "uPassword"].forEach((id) => $(id).addEventListener("input", () => fieldError($(id), false)));
      $("uPasswordToggle").addEventListener("click", () => {
        const input = $("uPassword"), show = input.type === "password";
        input.type = show ? "text" : "password";
        $("uPasswordToggle").setAttribute("aria-pressed", show ? "true" : "false");
        $("uPasswordToggle").setAttribute("aria-label", show ? "Ẩn mật khẩu" : "Hiện mật khẩu");
        $("uPasswordToggle").innerHTML = '<i class="fa-solid ' + (show ? "fa-eye-slash" : "fa-eye") + '" aria-hidden="true"></i>';
      });
      face.init();
      accounts.load();
    },

    async load() {
      const box = $("usersBody");
      if (!accounts.users.length) FG.skeleton(box, 6);
      try {
        const [users, classes] = await Promise.all([FG.api("/api/admin/users"), FG.api("/api/admin/classes")]);
        accounts.users = users; accounts.classes = classes;
        accounts.render();
      } catch (e) {
        $("usersSub").textContent = "";
        FG.setState(box, { error: true, title: "Không tải được danh sách tài khoản", text: e.message, retry: accounts.load });
      }
    },

    render() {
      const box = $("usersBody"), q = $("userSearch").value.trim().toLowerCase(), role = $("userRole").value;
      const list = accounts.users.filter((u) => (!role || u.role === role) &&
        (!q || String(u.display_name || "").toLowerCase().indexOf(q) !== -1 || String(u.username || "").toLowerCase().indexOf(q) !== -1));
      $("usersSub").textContent = accounts.users.length + " tài khoản" + (list.length !== accounts.users.length ? " · đang hiện " + list.length : "");
      if (!list.length) {
        FG.setState(box, { compact: true, icon: "fa-magnifying-glass", title: "Không có tài khoản nào khớp", text: "Thử đổi từ khóa hoặc bộ lọc vai trò." });
        return;
      }
      box.innerHTML = '<div class="fg-table-wrap"><table class="fg-table is-stack"><thead><tr><th>Người dùng</th><th>Vai trò</th><th>Lớp / Liên kết</th><th>Khuôn mặt</th><th><span class="fg-sr-only">Thao tác</span></th></tr></thead><tbody>' +
        list.map((u) => {
          const r = ROLE[u.role] || { label: u.role, tone: "neutral", icon: "fa-user" };
          const link = u.role === "parent" ? (u.linked_student_name ? "Con: " + u.linked_student_name : "Chưa liên kết học sinh")
            : (u.role === "student" || u.role === "teacher") ? (u.class_id ? u.class_name : "Chưa gán lớp") : "—";
          const faceCell = u.role !== "student" ? '<span class="fg-muted">—</span>'
            : u.has_face ? FG.badge("Đã đăng ký", "success", "fa-circle-check") : FG.badge("Chưa đăng ký", "neutral", "fa-circle-question");
          return '<tr><td class="is-primary"><div class="fg-person"><span class="fg-avatar" aria-hidden="true">' + esc(FG.initials(u.display_name)) + "</span><div>" +
            '<div class="fg-person-name">' + esc(u.display_name) + (u.is_self ? ' <span class="fg-caption">(bạn)</span>' : "") + "</div>" +
            '<div class="fg-person-sub">' + esc(u.username) + "</div></div></div></td>" +
            '<td data-label="Vai trò">' + FG.badge(r.label, r.tone, r.icon) + "</td>" +
            '<td data-label="Lớp / Liên kết">' + esc(link) + "</td>" +
            '<td data-label="Khuôn mặt">' + faceCell + "</td>" +
            '<td class="is-actions">' +
            (u.role === "student" ? '<button type="button" class="btn btn-secondary btn-sm" data-act="face" data-id="' + u.id + '" aria-label="' + (u.has_face ? "Đăng ký lại khuôn mặt cho " : "Đăng ký khuôn mặt cho ") + esc(u.display_name) + '"><i class="fa-solid fa-camera" aria-hidden="true"></i>' + (u.has_face ? "Đăng ký lại" : "Đăng ký khuôn mặt") + "</button> " : "") +
            (u.role === "student" && u.has_face ? '<button type="button" class="btn btn-outline-danger btn-sm" data-act="face-reset" data-id="' + u.id + '" aria-label="Xóa dữ liệu khuôn mặt của ' + esc(u.display_name) + '">Xóa khuôn mặt</button> ' : "") +
            '<button type="button" class="fg-icon-btn is-bordered" data-act="edit" data-id="' + u.id + '" aria-label="Sửa tài khoản ' + esc(u.display_name) + '"><i class="fa-solid fa-pen" aria-hidden="true"></i></button> ' +
            (u.is_self ? "" : '<button type="button" class="fg-icon-btn is-bordered" data-act="delete" data-id="' + u.id + '" aria-label="Xóa tài khoản ' + esc(u.display_name) + '"><i class="fa-solid fa-trash" aria-hidden="true"></i></button>') +
            "</td></tr>";
        }).join("") + "</tbody></table></div>";
    },

    onAction(e) {
      const btn = e.target.closest("[data-act]");
      if (!btn) return;
      const user = accounts.users.find((u) => u.id === Number(btn.dataset.id));
      if (!user) return;
      if (btn.dataset.act === "edit") accounts.open(user);
      if (btn.dataset.act === "delete") accounts.remove(user, btn);
      if (btn.dataset.act === "face") face.open(user);
      if (btn.dataset.act === "face-reset") accounts.resetFace(user, btn);
    },

    roleFields() {
      const role = $("uRole").value;
      $("uClassField").hidden = !(role === "student" || role === "teacher");
      $("uStudentField").hidden = role !== "parent";
      $("uClassHelp").textContent = role === "teacher" ? "Giáo viên chỉ xem được lớp được phân công." : "Học sinh cần có lớp để giáo viên điểm danh.";
    },

    open(user) {
      accounts.editing = user;
      formError("userFormError", "");
      ["uDisplay", "uUsername", "uPassword", "uStudent"].forEach((id) => fieldError($(id), false));
      $("userModalTitle").textContent = user ? "Sửa tài khoản" : "Thêm tài khoản";
      $("uDisplay").value = user ? user.display_name : "";
      $("uUsername").value = user ? user.username : "";
      $("uPassword").value = ""; $("uPassword").type = "password";
      $("uPasswordLabel").classList.toggle("fg-required", !user);
      $("uPasswordHelp").textContent = user ? "Để trống nếu không đổi mật khẩu." : "Người dùng dùng mật khẩu này để đăng nhập lần đầu.";
      $("uRole").value = user ? user.role : "student";
      $("uRole").disabled = !!(user && user.is_self);     // an admin cannot demote themselves by accident
      $("uClass").innerHTML = '<option value="">Chưa gán lớp</option>' +
        accounts.classes.map((c) => '<option value="' + c.id + '">' + esc(c.class_name) + "</option>").join("");
      $("uClass").value = user && user.class_id ? String(user.class_id) : "";
      const students = accounts.users.filter((u) => u.role === "student");
      $("uStudent").innerHTML = '<option value="">Chọn học sinh</option>' +
        students.map((s) => '<option value="' + s.id + '">' + esc(s.display_name) + (s.class_id ? " · " + esc(s.class_name) : "") + "</option>").join("");
      $("uStudent").value = user && user.student_id ? String(user.student_id) : "";
      accounts.roleFields();
      window.bootstrap.Modal.getOrCreateInstance($("userModal")).show();
      setTimeout(() => $("uDisplay").focus(), 300);
    },

    async save(e) {
      e.preventDefault();
      const btn = $("userSave"), user = accounts.editing;
      if (FG.isBusy(btn)) return;
      const role = $("uRole").value;
      const ok = [
        fieldError($("uDisplay"), !$("uDisplay").value.trim()),
        fieldError($("uUsername"), !$("uUsername").value.trim()),
        fieldError($("uPassword"), !user && !$("uPassword").value),
        fieldError($("uStudent"), role === "parent" && !$("uStudent").value),
      ].every(Boolean);
      if (!ok) { const first = $("userForm").querySelector(".is-invalid"); if (first) first.focus(); return; }
      formError("userFormError", "");
      FG.busy(btn, true, "Đang lưu…");
      try {
        const body = { display_name: $("uDisplay").value.trim(), username: $("uUsername").value.trim(), password: $("uPassword").value,
          role: role, class_id: $("uClass").value || null, student_id: $("uStudent").value || null };
        const res = await FG.api(user ? "/api/admin/edit_user/" + user.id : "/api/admin/create_user", { body: body });
        window.bootstrap.Modal.getOrCreateInstance($("userModal")).hide();
        FG.toast(res.message || "Đã lưu tài khoản.", "success");
        accounts.load();
      } catch (err) {
        formError("userFormError", err.message);     // keep the form and its values so the admin can fix it
      } finally { FG.busy(btn, false); }
    },

    async remove(user, btn) {
      const role = ROLE[user.role] || { label: user.role };
      const consequences = ["Người này sẽ không đăng nhập được nữa."];
      if (user.role === "student") consequences.push("Dữ liệu khuôn mặt của học sinh bị xóa.", "Phụ huynh đang liên kết sẽ không còn xem được kết quả.");
      if (user.role === "teacher") consequences.push("Lớp đang phân công sẽ không còn giáo viên này.");
      consequences.push("Thao tác này không thể hoàn tác.");
      const ok = await FG.confirm({ title: "Xóa tài khoản " + user.display_name + "?", body: role.label + " · " + user.username,
        consequences: consequences, confirmLabel: "Xóa tài khoản", danger: true });
      if (!ok || btn.disabled) return;
      btn.disabled = true;
      try {
        const res = await FG.api("/api/admin/delete_user/" + user.id, { method: "POST", body: {} });
        FG.toast(res.message || "Đã xóa tài khoản.", "success");
        accounts.load();
      } catch (e) { FG.toast(e.message, "danger"); btn.disabled = false; }
    },

    async resetFace(user, btn) {
      const ok = await FG.confirm({ title: "Xóa dữ liệu khuôn mặt của " + user.display_name + "?",
        body: "Camera lớp học sẽ không nhận ra học sinh này cho đến khi đăng ký lại.",
        consequences: ["Dãy số đặc trưng khuôn mặt bị xóa khỏi hệ thống.", "Học sinh sẽ không được điểm danh tự động ở lớp có camera."],
        confirmLabel: "Xóa dữ liệu khuôn mặt", danger: true });
      if (!ok || btn.disabled) return;
      btn.disabled = true;
      try {
        const res = await FG.api("/api/admin/reset_face", { body: { user_id: user.id } });
        FG.toast(res.message || "Đã xóa dữ liệu khuôn mặt.", "success");
        accounts.load();
      } catch (e) { FG.toast(e.message, "danger"); btn.disabled = false; }
    },
  };

  /* ─────────────────────────── Đăng ký khuôn mặt ─────────────────────────── */
  const face = {
    user: null, stream: null, images: [], MAX: 5, MIN: 3, busy: false,

    init() {
      $("faceConsent").addEventListener("change", face.update);
      $("faceStart").addEventListener("click", face.start);
      $("faceCapture").addEventListener("click", face.capture);
      $("faceSave").addEventListener("click", face.save);
      $("faceThumbs").addEventListener("click", (e) => {
        const b = e.target.closest("[data-remove]");
        if (b) { face.images.splice(Number(b.dataset.remove), 1); face.update(); }
      });
      $("faceModal").addEventListener("hidden.bs.modal", face.stop);
    },

    open(user) {
      face.user = user; face.images = []; face.busy = false;
      $("faceModalTitle").textContent = "Đăng ký khuôn mặt: " + user.display_name;
      $("faceConsent").checked = false;
      $("faceHint").textContent = "";
      face.state("fa-video-slash", "Camera chưa bật", "Xác nhận đã có sự đồng ý, sau đó bấm Bật camera.");
      face.update();
      window.bootstrap.Modal.getOrCreateInstance($("faceModal")).show();
    },

    state(icon, title, text, spinner) {
      const overlay = $("faceState");
      overlay.hidden = false; $("faceVideo").hidden = true;
      overlay.innerHTML = (spinner ? '<span class="spinner-border text-light" aria-hidden="true"></span>' : '<i class="fa-solid ' + icon + '" aria-hidden="true"></i>') +
        '<div class="fg-camera-state-title">' + esc(title) + '</div><p class="fg-camera-state-text">' + esc(text) + "</p>";
    },

    update() {
      const consent = $("faceConsent").checked, on = !!face.stream;
      $("faceStart").disabled = !consent || on;
      $("faceCapture").disabled = !consent || !on || face.busy || face.images.length >= face.MAX;
      if (!FG.isBusy($("faceSave"))) $("faceSave").disabled = !consent || face.images.length < face.MIN;
      $("faceCount").textContent = face.images.length;
      $("faceThumbs").innerHTML = face.images.map((im, i) =>
        '<span style="position: relative; display: inline-block;"><img src="' + im.thumb + '" alt="Ảnh khuôn mặt ' + (i + 1) + '" width="72" height="72" style="object-fit: cover; border-radius: var(--fg-r-md); border: 1px solid var(--fg-border);">' +
        '<button type="button" class="fg-icon-btn is-bordered" data-remove="' + i + '" aria-label="Bỏ ảnh ' + (i + 1) + '" style="position: absolute; top: -10px; right: -10px; width: 28px; height: 28px; border-radius: 50%;"><i class="fa-solid fa-xmark" aria-hidden="true"></i></button></span>').join("");
    },

    async start() {
      if (face.stream) return;
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        face.state("fa-video-slash", "Trình duyệt không hỗ trợ camera", "Hãy dùng trình duyệt mới hơn và mở trang qua địa chỉ an toàn (https hoặc localhost).");
        return;
      }
      face.state("", "Đang bật camera", "Nếu trình duyệt hỏi, hãy chọn Cho phép.", true);
      try {
        face.stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 } });
        const video = $("faceVideo");
        video.srcObject = face.stream;
        video.hidden = false; $("faceState").hidden = true;
        $("faceHint").textContent = "Đặt khuôn mặt học sinh vào giữa khung hình rồi bấm Chụp ảnh.";
      } catch (e) {
        face.stream = null;
        if (e.name === "NotAllowedError" || e.name === "SecurityError")
          face.state("fa-lock", "Chưa được cấp quyền camera", "Bấm vào biểu tượng ổ khóa trên thanh địa chỉ, cho phép dùng camera, rồi bấm Bật camera lại.");
        else if (e.name === "NotFoundError" || e.name === "OverconstrainedError")
          face.state("fa-video-slash", "Không tìm thấy camera", "Hãy cắm camera vào máy rồi bấm Bật camera lại.");
        else if (e.name === "NotReadableError" || e.name === "AbortError")
          face.state("fa-video-slash", "Camera đang được ứng dụng khác sử dụng", "Đóng ứng dụng đang dùng camera (ví dụ buổi học đang giám sát) rồi thử lại.");
        else face.state("fa-video-slash", "Không bật được camera", "Vui lòng thử lại.");
      }
      face.update();
    },

    stop() {
      if (face.stream) { face.stream.getTracks().forEach((t) => t.stop()); face.stream = null; }
      const video = $("faceVideo");
      video.srcObject = null; video.hidden = true;
      face.images = [];        // captured frames never outlive the dialog
      $("faceThumbs").innerHTML = "";
    },

    async capture() {
      if (face.busy || !face.stream || face.images.length >= face.MAX) return;
      const btn = $("faceCapture"), canvas = $("faceCanvas");
      face.busy = true;
      FG.busy(btn, true, "Đang kiểm tra…");
      canvas.getContext("2d").drawImage($("faceVideo"), 0, 0, canvas.width, canvas.height);
      const dataUrl = canvas.toDataURL("image/jpeg", 0.9);
      try {
        const res = await FG.api("/api/admin/detect_face", { body: { image: dataUrl } });
        if (res.status === "success") {
          face.images.push({ full: dataUrl, thumb: res.cropped_image });
          $("faceHint").textContent = face.images.length < face.MIN ? "Đã nhận ảnh. Chụp thêm " + (face.MIN - face.images.length) + " ảnh nữa." : "Đã đủ ảnh. Có thể bấm Lưu đăng ký.";
        } else {
          $("faceHint").textContent = (res.message || "Không nhận được khuôn mặt") + ". Hãy thử lại.";
        }
      } catch (e) {
        $("faceHint").textContent = e.message;
      } finally {
        face.busy = false;
        FG.busy(btn, false);
        face.update();
      }
    },

    async save() {
      const btn = $("faceSave");
      if (FG.isBusy(btn) || face.images.length < face.MIN || !$("faceConsent").checked) return;
      FG.busy(btn, true, "Đang lưu…");
      try {
        const res = await FG.api("/api/admin/register_face", { body: { user_id: face.user.id, images: face.images.map((i) => i.full) } });
        window.bootstrap.Modal.getOrCreateInstance($("faceModal")).hide();
        FG.toast(res.message || "Đã đăng ký khuôn mặt.", "success");
        accounts.load();
      } catch (e) {
        $("faceHint").textContent = e.message;
        FG.toast(e.message, "danger");
      } finally { FG.busy(btn, false); face.update(); }
    },
  };

  /* ───────────────────────────── Lớp học ───────────────────────────── */
  const classes = {
    list: [], editing: null,
    init() {
      $("classAdd").addEventListener("click", () => classes.open(null));
      $("classForm").addEventListener("submit", classes.save);
      $("cName").addEventListener("input", () => fieldError($("cName"), false));
      $("classesBody").addEventListener("click", (e) => {
        const btn = e.target.closest("[data-act]");
        if (!btn) return;
        const c = classes.list.find((x) => x.id === Number(btn.dataset.id));
        if (!c) return;
        if (btn.dataset.act === "edit") classes.open(c);
        if (btn.dataset.act === "delete") classes.remove(c, btn);
      });
      classes.load();
    },
    async load() {
      const box = $("classesBody");
      if (!classes.list.length) FG.skeleton(box, 4);
      try {
        classes.list = await FG.api("/api/admin/classes");
        $("classesSub").textContent = classes.list.length + " lớp";
        if (!classes.list.length) {
          FG.setState(box, { icon: "fa-school", title: "Chưa có lớp học nào", text: "Bấm Thêm lớp để tạo lớp đầu tiên, sau đó gán giáo viên và học sinh ở trang Tài khoản." });
          return;
        }
        box.innerHTML = '<div class="fg-table-wrap"><table class="fg-table is-stack"><thead><tr><th>Lớp</th><th class="is-num">Số học sinh</th><th><span class="fg-sr-only">Thao tác</span></th></tr></thead><tbody>' +
          classes.list.map((c) => '<tr><td class="is-primary"><span class="fg-person-name">' + esc(c.class_name) + "</span></td>" +
            '<td class="is-num" data-label="Số học sinh">' + c.student_count + "</td>" +
            '<td class="is-actions"><button type="button" class="fg-icon-btn is-bordered" data-act="edit" data-id="' + c.id + '" aria-label="Đổi tên lớp ' + esc(c.class_name) + '"><i class="fa-solid fa-pen" aria-hidden="true"></i></button> ' +
            '<button type="button" class="fg-icon-btn is-bordered" data-act="delete" data-id="' + c.id + '" aria-label="Xóa lớp ' + esc(c.class_name) + '"><i class="fa-solid fa-trash" aria-hidden="true"></i></button></td></tr>').join("") +
          "</tbody></table></div>";
      } catch (e) {
        $("classesSub").textContent = "";
        FG.setState(box, { error: true, title: "Không tải được danh sách lớp", text: e.message, retry: classes.load });
      }
    },
    open(c) {
      classes.editing = c;
      formError("classFormError", ""); fieldError($("cName"), false);
      $("classModalTitle").textContent = c ? "Đổi tên lớp" : "Thêm lớp";
      $("cName").value = c ? c.class_name : "";
      window.bootstrap.Modal.getOrCreateInstance($("classModal")).show();
      setTimeout(() => $("cName").focus(), 300);
    },
    async save(e) {
      e.preventDefault();
      const btn = $("classSave"), c = classes.editing;
      if (FG.isBusy(btn)) return;
      if (!fieldError($("cName"), !$("cName").value.trim())) { $("cName").focus(); return; }
      formError("classFormError", "");
      FG.busy(btn, true, "Đang lưu…");
      try {
        const res = await FG.api(c ? "/api/admin/edit_class/" + c.id : "/api/admin/create_class", { body: { class_name: $("cName").value.trim() } });
        window.bootstrap.Modal.getOrCreateInstance($("classModal")).hide();
        FG.toast(res.message || "Đã lưu lớp học.", "success");
        classes.load();
      } catch (err) { formError("classFormError", err.message); }
      finally { FG.busy(btn, false); }
    },
    async remove(c, btn) {
      const ok = await FG.confirm({ title: "Xóa lớp " + c.class_name + "?",
        body: c.student_count ? "Lớp đang có " + c.student_count + " học sinh." : "Lớp chưa có học sinh.",
        consequences: [
          "Học sinh và giáo viên của lớp sẽ trở thành chưa gán lớp (tài khoản không bị xóa).",
          "Giáo viên không còn mở được lớp này.",
          "Không xóa được nếu lớp đang có buổi học diễn ra.",
          "Thao tác này không thể hoàn tác.",
        ], confirmLabel: "Xóa lớp", danger: true });
      if (!ok || btn.disabled) return;
      btn.disabled = true;
      try {
        const res = await FG.api("/api/admin/delete_class/" + c.id, { method: "POST", body: {} });
        FG.toast(res.message || "Đã xóa lớp.", "success");
        classes.load();
      } catch (e) { FG.toast(e.message, "danger"); btn.disabled = false; }
    },
  };

  const PAGES = { accounts: accounts, classes: classes };
  document.addEventListener("DOMContentLoaded", () => { if (PAGES[PAGE]) PAGES[PAGE].init(); });
})();
