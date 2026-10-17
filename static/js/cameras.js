/* Cài đặt › Camera: discovery, easy connect, test, preview, save, default, class assignment.
 *
 * The browser never sees a stored camera password: the API returns has_credentials only.
 * The secret typed here is sent once to /test (or with a pasted URL) and kept server-side
 * behind a short-lived test token; saving uses that token.
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = FG.esc;
  const page = $("camerasPage");
  if (!page) return;
  const IS_ADMIN = page.dataset.admin === "true";

  const S = { data: null, job: null, pollTimer: null, mode: null, device: null, cameraId: null,
              testToken: null, previewId: null, previewTimer: null, modalPreviewId: null, modalTimer: null,
              lastFocus: null };

  const STATUS_TONE = { CONNECTED: "success", DEGRADED: "warning", UNTESTED: "neutral", AUTH_FAILED: "danger",
                        OFFLINE: "danger", NO_SIGNAL: "warning", RECONNECTING: "warning", STREAM_UNAVAILABLE: "danger",
                        DEVICE_FOUND_BUT_NO_COMPATIBLE_STREAM: "danger", UNREACHABLE: "danger" };
  const STATUS_ICON = { CONNECTED: "fa-circle-check", DEGRADED: "fa-gauge-simple-low", UNTESTED: "fa-circle-question",
                        AUTH_FAILED: "fa-key", OFFLINE: "fa-plug-circle-xmark", NO_SIGNAL: "fa-video-slash",
                        RECONNECTING: "fa-rotate", STREAM_UNAVAILABLE: "fa-video-slash", UNREACHABLE: "fa-plug-circle-xmark",
                        DEVICE_FOUND_BUT_NO_COMPATIBLE_STREAM: "fa-ban" };
  const fps = (v) => (v === null || v === undefined ? null : (Math.round(v * 10) / 10) + " FPS");
  const res = (v) => (v ? String(v).replace("x", "×") : null);

  /* ------------------------------------------------------------ modals */
  function modal(id) { return window.bootstrap.Modal.getOrCreateInstance($(id)); }
  function openModal(id) { S.lastFocus = document.activeElement; modal(id).show(); }
  ["connectModal", "renameModal", "assignModal", "previewModal"].forEach((id) => {
    $(id).addEventListener("hidden.bs.modal", () => {
      if (id === "connectModal") stopConnectPreview();
      if (id === "previewModal") stopModalPreview();
      const back = S.lastFocus && document.body.contains(S.lastFocus) ? S.lastFocus : $("camDiscover");
      if (back) back.focus();
    });
  });

  /* ------------------------------------------------------------ lists */
  async function load() {
    const my = $("myCamerasBody"), cls = $("classCamerasBody");
    if (!S.data) { if (my) FG.skeleton(my, 2); FG.skeleton(cls, 2); }
    try {
      S.data = await FG.api("/api/settings/cameras");
      render();
    } catch (e) {
      [my, cls].forEach((el) => el && FG.setState(el, { error: true, title: "Không tải được danh sách camera", text: e.message, retry: load }));
    }
  }

  function badge(c) {
    const st = c.status || "UNTESTED";
    return FG.badge(c.status_label || st, STATUS_TONE[st] || "neutral", STATUS_ICON[st] || "fa-circle-question");
  }

  function card(c) {
    const meta = [
      [c.vendor || c.model ? "Hãng / mẫu" : null, [c.vendor, c.model].filter(Boolean).join(" ")],
      [c.source_type === "WEBCAM" ? "Thiết bị" : "Địa chỉ", c.source_type === "WEBCAM" ? "Webcam số " + (c.device_index || 0) : c.host],
      ["Độ phân giải", res(c.resolution)], ["Tốc độ khung hình", fps(c.measured_fps)],
      ["Lớp", (c.classes || []).map((k) => k.class_name).join(", ") || null],
      ["Xác thực", c.source_type === "WEBCAM" ? null : (c.has_credentials ? "Đã lưu (mã hóa)" : "Không có")],
    ].filter((m) => m[0] && m[1]);
    const acts = [];
    acts.push('<button type="button" class="btn btn-secondary btn-sm" data-act="preview" data-id="' + esc(c.camera_id) + '"><i class="fa-solid fa-eye" aria-hidden="true"></i>Xem thử</button>');
    if (c.can_edit && !c.is_default) acts.push('<button type="button" class="btn btn-secondary btn-sm" data-act="default" data-id="' + esc(c.camera_id) + '"><i class="fa-solid fa-star" aria-hidden="true"></i>Đặt mặc định</button>');
    if (c.can_assign) acts.push('<button type="button" class="btn btn-secondary btn-sm" data-act="assign" data-id="' + esc(c.camera_id) + '"><i class="fa-solid fa-school" aria-hidden="true"></i>Gán lớp</button>');
    if (c.can_edit) {
      acts.push('<button type="button" class="btn btn-secondary btn-sm" data-act="rename" data-id="' + esc(c.camera_id) + '"><i class="fa-solid fa-pen" aria-hidden="true"></i>Đổi tên</button>');
      if (c.source_type !== "WEBCAM") acts.push('<button type="button" class="btn btn-secondary btn-sm" data-act="credentials" data-id="' + esc(c.camera_id) + '"><i class="fa-solid fa-key" aria-hidden="true"></i>Cập nhật xác thực</button>');
      acts.push('<button type="button" class="btn btn-secondary btn-sm" data-act="retest" data-id="' + esc(c.camera_id) + '"><i class="fa-solid fa-plug" aria-hidden="true"></i>Kiểm tra lại</button>');
      acts.push('<button type="button" class="btn btn-outline-danger btn-sm" data-act="delete" data-id="' + esc(c.camera_id) + '"><i class="fa-solid fa-trash" aria-hidden="true"></i>Xóa</button>');
    }
    return '<article class="fg-cam-card" data-camera="' + esc(c.camera_id) + '">' +
      '<div class="fg-cam-head"><span class="fg-cam-icon" aria-hidden="true"><i class="fa-solid ' + (c.source_type === "WEBCAM" ? "fa-laptop" : "fa-video") + '"></i></span>' +
      '<h3 class="fg-cam-name">' + esc(c.name) + "</h3></div>" +
      '<div class="fg-row fg-cam-badges">' + badge(c) + (c.is_default ? FG.badge("Mặc định", "info", "fa-star") : "") +
      (c.is_shared ? FG.badge("Camera của trường", "neutral", "fa-school") : "") + "</div>" +
      '<dl class="fg-cam-meta">' + meta.map((m) => "<div><dt>" + esc(m[0]) + "</dt><dd>" + esc(m[1]) + "</dd></div>").join("") + "</dl>" +
      '<div class="fg-cam-actions">' + acts.join("") + "</div></article>";
  }

  function render() {
    const d = S.data || { my_cameras: [], class_cameras: [] };
    const my = $("myCamerasBody");
    if (my) {
      my.innerHTML = d.my_cameras.length ? '<div class="fg-cam-grid">' + d.my_cameras.map(card).join("") + "</div>"
        : FG.stateHTML({ compact: true, icon: "fa-video", title: "Chưa có camera",
                         text: "Bấm “Tìm camera trong mạng LAN” để thêm. Khi chưa có camera nào, buổi học dùng webcam 0 của máy chủ như trước." });
    }
    $("classCamerasBody").innerHTML = d.class_cameras.length ? '<div class="fg-cam-grid">' + d.class_cameras.map(card).join("") + "</div>"
      : FG.stateHTML({ compact: true, icon: "fa-school", title: "Chưa có camera lớp học",
                       text: IS_ADMIN ? "Thêm camera của trường rồi gán cho lớp." : "Quản trị viên chưa gán camera của trường cho lớp của bạn." });
  }

  function findCam(id) {
    const d = S.data || { my_cameras: [], class_cameras: [] };
    return d.my_cameras.concat(d.class_cameras).find((c) => c.camera_id === id);
  }

  page.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-act]");
    if (!btn) return;
    const id = btn.dataset.id, c = findCam(id), act = btn.dataset.act;
    if (act === "connect") return openConnect("discovered", S.job && S.job.results.find((r) => r.discovery_id === id));
    if (!c) return;
    if (act === "preview") return previewSaved(c);
    if (act === "rename") { S.cameraId = id; S.lastFocus = btn; $("renameInput").value = c.name; $("renameError").hidden = true; modal("renameModal").show(); return; }
    if (act === "assign") return openAssign(c, btn);
    if (act === "credentials") return openConnect("credentials", null, c);
    if (act === "retest") return retest(c, btn);
    if (act === "default") {
      FG.busy(btn, true, "Đang lưu…");
      try { await FG.api("/api/settings/cameras/" + encodeURIComponent(id) + "/default", { body: {} }); FG.toast("Đã đặt “" + c.name + "” làm camera mặc định.", "success"); await load(); }
      catch (e) { FG.toast(e.message, "danger"); FG.busy(btn, false); }
      return;
    }
    if (act === "delete") {
      const okDel = await FG.confirm({ title: "Xóa camera “" + c.name + "”?", danger: true, confirmLabel: "Xóa camera",
        body: "Thông tin kết nối và mật khẩu đã mã hóa của camera này sẽ bị xóa.",
        consequences: c.classes && c.classes.length ? ["Các lớp đang dùng camera này sẽ không còn camera được gán."] : [] });
      if (!okDel) return;
      try { await FG.api("/api/settings/cameras/" + encodeURIComponent(id), { method: "DELETE" }); FG.toast("Đã xóa camera.", "success"); await load(); }
      catch (e) { FG.toast(e.message, "danger"); }
    }
  });

  async function retest(c, btn) {
    FG.busy(btn, true, "Đang kiểm tra…");
    try {
      const r = (await FG.api("/api/settings/cameras/" + encodeURIComponent(c.camera_id) + "/test", { body: {} })).result;
      FG.toast(c.name + ": " + r.status_label + (r.resolution ? " · " + res(r.resolution) : "") + (r.measured_fps ? " · " + fps(r.measured_fps) : ""),
               r.status === "CONNECTED" ? "success" : "warning");
      await load();
    } catch (e) { FG.toast(e.message, "danger"); FG.busy(btn, false); }
  }

  $("renameForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = $("renameInput").value.trim();
    $("renameError").hidden = !!name;
    if (!name) { $("renameInput").focus(); return; }
    FG.busy($("renameSave"), true, "Đang lưu…");
    try {
      await FG.api("/api/settings/cameras/" + encodeURIComponent(S.cameraId), { method: "PATCH", body: { name } });
      modal("renameModal").hide(); FG.toast("Đã đổi tên camera.", "success"); await load();
    } catch (e) { FG.toast(e.message, "danger"); }
    finally { FG.busy($("renameSave"), false); }
  });

  /* ------------------------------------------------------------ assign */
  function openAssign(c, btn) {
    S.cameraId = c.camera_id; S.lastFocus = btn;
    const names = (c.classes || []).map((k) => k.class_name);
    $("assignCurrent").textContent = names.length ? "Đang gán cho: " + names.join(", ") : "Chưa gán cho lớp nào.";
    modal("assignModal").show();
  }
  async function assign(unassign) {
    const btn = unassign ? $("assignRemove") : $("assignSave");
    FG.busy(btn, true, "Đang lưu…");
    try {
      await FG.api("/api/settings/cameras/" + encodeURIComponent(S.cameraId) + "/assign-class",
                   { body: { class_id: Number($("assignClass").value), unassign: !!unassign } });
      modal("assignModal").hide(); FG.toast(unassign ? "Đã bỏ gán lớp." : "Đã gán camera cho lớp.", "success"); await load();
    } catch (e) { FG.toast(e.message, "danger"); }
    finally { FG.busy(btn, false); }
  }
  $("assignForm").addEventListener("submit", (ev) => { ev.preventDefault(); assign(false); });
  $("assignRemove").addEventListener("click", () => assign(true));

  /* --------------------------------------------------------- discovery */
  function setDiscovery(text) { $("discoveryStatus").textContent = text; }

  async function discover() {
    $("discoveryPanel").hidden = false;
    $("discoveryRefresh").hidden = true; $("discoveryStop").hidden = false;
    $("discoveryResults").innerHTML = "";
    setDiscovery("Đang tìm camera trong mạng LAN…");
    FG.busy($("camDiscover"), true, "Đang tìm…");
    try {
      S.job = (await FG.api("/api/settings/cameras/discover", { body: {} })).job;
      poll();
    } catch (e) {
      setDiscovery(e.message); FG.busy($("camDiscover"), false);
      $("discoveryStop").hidden = true; $("discoveryRefresh").hidden = false;
    }
  }

  async function poll() {
    clearTimeout(S.pollTimer);
    if (!S.job) return;
    try {
      S.job = (await FG.api("/api/settings/cameras/discovery/" + encodeURIComponent(S.job.job_id))).job;
    } catch (e) { setDiscovery(e.message); return finishDiscovery(); }
    renderResults();
    if (S.job.status === "RUNNING") {
      setDiscovery("Đang tìm… " + (S.job.count ? "đã thấy " + S.job.count + " thiết bị" : (S.job.stage || "")));
      S.pollTimer = setTimeout(poll, 1000);
    } else finishDiscovery();
  }

  function finishDiscovery() {
    FG.busy($("camDiscover"), false);
    $("discoveryStop").hidden = true; $("discoveryRefresh").hidden = false;
    const n = S.job ? S.job.count : 0;
    const rtspOff = n && S.job.results.some((r) => r.status === "RTSP_DISABLED");
    setDiscovery(n ? "Đã tìm thấy " + n + " thiết bị" + (S.job.status === "STOPPED" ? " (đã dừng tìm)" : "") + "."
      + (rtspOff ? " Thiết bị “chưa bật RTSP”: xem hướng dẫn trên thẻ." : "")
      : "Không tìm thấy camera. Camera phải cùng mạng LAN với máy chạy FocusGuard.");
  }

  const DISCOVERY_TONE = { CONNECTABLE: "success", AUTH_REQUIRED: "warning", RTSP_DISABLED: "warning" };
  const DISCOVERY_ICON = { CONNECTABLE: "fa-circle-check", AUTH_REQUIRED: "fa-key", RTSP_DISABLED: "fa-video-slash" };
  // 554 closed, SDK port 8000 open: an EZVIZ/Hikvision camera whose LAN RTSP is off (never the vendor cloud)
  const RTSP_OFF_HELP = '<p class="fg-caption mb-0">Camera EZVIZ: mở ứng dụng EZVIZ › <b>Cài đặt</b> (Settings) › ' +
    "<b>Xem trực tiếp qua LAN</b> (LAN Live View) › chọn camera › <b>Cài đặt dịch vụ cục bộ</b> (Local Service Settings) " +
    "› bật RTSP, rồi bấm <b>Tìm lại</b>. Mẫu chỉ hỗ trợ đám mây EZVIZ thì không dùng được với FocusGuard.</p>";

  function renderResults() {
    const list = (S.job && S.job.results) || [];
    $("discoveryResults").innerHTML = list.map((r) => {
      const tone = DISCOVERY_TONE[r.status] || "danger";
      const icon = DISCOVERY_ICON[r.status] || "fa-ban";
      const rtspOff = r.status === "RTSP_DISABLED";
      const compatible = r.status !== "NO_COMPATIBLE_STREAM" && !rtspOff;
      return '<article class="fg-cam-card is-found">' +
        '<div class="fg-cam-head"><span class="fg-cam-icon" aria-hidden="true"><i class="fa-solid fa-video"></i></span>' +
        '<h3 class="fg-cam-name">' + esc(r.display_name) + "</h3></div>" +
        '<div class="fg-row fg-cam-badges">' + FG.badge(r.status_label, tone, icon) + "</div>" +
        '<dl class="fg-cam-meta">' +
        (r.vendor || r.model ? "<div><dt>Hãng / mẫu</dt><dd>" + esc([r.vendor, r.model].filter(Boolean).join(" ")) + "</dd></div>" : "") +
        "<div><dt>Địa chỉ IP</dt><dd>" + esc(r.host) + "</dd></div></dl>" +
        (rtspOff ? RTSP_OFF_HELP : "") +
        '<div class="fg-cam-actions">' + (compatible
          ? '<button type="button" class="btn btn-primary btn-sm" data-act="connect" data-id="' + esc(r.discovery_id) + '"><i class="fa-solid fa-link" aria-hidden="true"></i>Kết nối</button>'
          : '<span class="fg-caption">' + (rtspOff ? "Chưa có luồng RTSP trong LAN" : "Không có luồng video trong LAN") +
            "</span>") + "</div></article>";
    }).join("");
  }

  $("camDiscover").addEventListener("click", discover);
  $("discoveryRefresh").addEventListener("click", discover);
  $("discoveryStop").addEventListener("click", async () => {
    if (!S.job) return;
    try { S.job = (await FG.api("/api/settings/cameras/discovery/" + encodeURIComponent(S.job.job_id) + "/stop", { body: {} })).job; }
    catch (e) { /* the poll shows the final state */ }
    poll();
  });

  /* ------------------------------------------------- connect / test / save */
  function show(id, on) { $(id).hidden = !on; }

  function openConnect(mode, device, cam) {
    S.mode = mode; S.device = device || null; S.cameraId = cam ? cam.camera_id : null; S.testToken = null;
    stopConnectPreview();
    $("connectForm").reset();
    $("connectError").hidden = true; $("camNameError").hidden = true;
    $("camResult").innerHTML = ""; show("camPreviewBox", false);
    $("camSave").disabled = true; $("camPreview").disabled = true;
    const webcam = mode === "webcam", creds = mode === "credentials";
    show("camWebcamField", webcam); show("camUserField", !webcam); show("camSecretField", !webcam);
    show("camNameField", !creds); show("camDefaultField", !creds && !IS_ADMIN);
    $("camAdvanced").hidden = webcam || creds || mode === "discovered" ? true : false;
    $("camAdvanced").open = mode === "manual";
    if (mode === "discovered" && device) {
      $("connectTitle").textContent = "Kết nối " + (device.display_name || "camera");
      $("connectDevice").textContent = [device.vendor, device.model, device.host].filter(Boolean).join(" · ");
      $("camName").value = device.device_name || [device.vendor, device.model].filter(Boolean).join(" ") || "";
      $("camUser").value = device.vendor === "EZVIZ" || device.vendor === "Hikvision" ? "admin" : "";
    } else if (mode === "webcam") {
      $("connectTitle").textContent = "Dùng webcam của máy chủ";
      $("connectDevice").textContent = "Webcam cắm vào máy đang chạy FocusGuard.";
      $("camName").value = "Webcam máy chủ";
    } else if (creds) {
      $("connectTitle").textContent = "Cập nhật xác thực: " + cam.name;
      $("connectDevice").textContent = "Nhập lại tên đăng nhập và mật khẩu / mã xác minh, rồi kiểm tra kết nối.";
      $("camUser").value = cam.username || "";
      $("camSave").innerHTML = '<i class="fa-solid fa-floppy-disk" aria-hidden="true"></i>Lưu xác thực';
    } else {
      $("connectTitle").textContent = "Thêm camera thủ công";
      $("connectDevice").textContent = "Nhập địa chỉ IP của camera trong mạng LAN.";
    }
    if (!creds) $("camSave").innerHTML = '<i class="fa-solid fa-floppy-disk" aria-hidden="true"></i>Lưu camera';
    openModal("connectModal");
    setTimeout(() => (creds ? $("camSecret") : webcam ? $("camName") : (mode === "manual" ? $("camHost") : $("camSecret"))).focus(), 350);
  }

  function testPayload() {
    const p = { username: $("camUser").value.trim(), secret: $("camSecret").value };
    if (S.mode === "webcam") return { source_type: "WEBCAM", device_index: Number($("camIndex").value), name: $("camName").value.trim() };
    if (S.mode === "discovered" && S.device) p.discovery_id = S.device.discovery_id;
    if (S.mode === "manual") {
      const url = $("camUrl").value.trim();
      if (url) p.url = url; else p.host = $("camHost").value.trim();
      p.protocol = $("camProtocol").value;
      if ($("camPort").value) p.port = Number($("camPort").value);
      if ($("camPath").value.trim()) p.stream_path = $("camPath").value.trim();
      if ($("camProfile").value.trim()) p.profile = $("camProfile").value.trim();
    }
    return p;
  }

  function resultHTML(r) {
    const okk = r.status === "CONNECTED";
    const rows = [["Độ phân giải", res(r.resolution)], ["Tốc độ khung hình", fps(r.measured_fps)],
                  ["Độ trễ kết nối", r.latency_ms !== null && r.latency_ms !== undefined ? Math.round(r.latency_ms) + " ms" : null],
                  ["Hãng / mẫu", [r.vendor, r.model].filter(Boolean).join(" ") || null], ["Profile", r.profile]].filter((x) => x[1]);
    return '<div class="fg-banner" data-tone="' + (okk ? "success" : "danger") + '">' +
      '<i class="fa-solid ' + (okk ? "fa-circle-check" : "fa-triangle-exclamation") + '" aria-hidden="true"></i><div>' +
      '<div class="fg-banner-title">' + esc(okk ? "Đã kết nối" : r.status_label) + "</div>" +
      (r.reason ? "<div>" + esc(r.reason) + "</div>" : "") + (!okk && r.help ? "<div>" + esc(r.help) + "</div>" : "") +
      (rows.length ? '<dl class="fg-cam-meta mt-2">' + rows.map((x) => "<div><dt>" + esc(x[0]) + "</dt><dd>" + esc(x[1]) + "</dd></div>").join("") + "</dl>" : "") +
      "</div></div>";
  }

  $("camTest").addEventListener("click", async () => {
    $("connectError").hidden = true;
    stopConnectPreview();
    S.testToken = null; $("camSave").disabled = true; $("camPreview").disabled = true;
    $("camResult").innerHTML = '<p class="fg-caption mb-0"><span class="spinner-border spinner-border-sm" aria-hidden="true"></span> Đang kiểm tra kết nối…</p>';
    FG.busy($("camTest"), true, "Đang kiểm tra…");
    try {
      const url = S.mode === "credentials" ? "/api/settings/cameras/" + encodeURIComponent(S.cameraId) + "/test" : "/api/settings/cameras/test";
      const r = (await FG.api(url, { body: testPayload() })).result;
      $("camResult").innerHTML = resultHTML(r);
      if (r.status === "CONNECTED" && r.test_token) {
        S.testToken = r.test_token; $("camSave").disabled = false; $("camPreview").disabled = false;
        $("camSecret").value = "";              /* the server holds it behind the test token */
      }
    } catch (e) {
      $("camResult").innerHTML = ""; $("connectError").hidden = false; $("connectError").querySelector("span").textContent = e.message;
    } finally { FG.busy($("camTest"), false); }
  });

  $("camSecretToggle").addEventListener("click", () => {
    const input = $("camSecret"), showIt = input.type === "password";
    input.type = showIt ? "text" : "password";
    $("camSecretToggle").setAttribute("aria-pressed", showIt ? "true" : "false");
    $("camSecretToggle").setAttribute("aria-label", showIt ? "Ẩn mật khẩu" : "Hiện mật khẩu");
    $("camSecretToggle").innerHTML = '<i class="fa-solid ' + (showIt ? "fa-eye-slash" : "fa-eye") + '" aria-hidden="true"></i>';
  });

  $("connectForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!S.testToken) return;
    const btn = $("camSave");
    FG.busy(btn, true, "Đang lưu…");
    try {
      if (S.mode === "credentials") {
        await FG.api("/api/settings/cameras/" + encodeURIComponent(S.cameraId), { method: "PATCH", body: { test_token: S.testToken } });
        FG.toast("Đã cập nhật xác thực camera.", "success");
      } else {
        const name = $("camName").value.trim();
        $("camNameError").hidden = !!name;
        if (!name) { $("camName").focus(); FG.busy(btn, false); return; }
        await FG.api("/api/settings/cameras", { body: { test_token: S.testToken, name, is_default: $("camDefault").checked } });
        FG.toast("Đã lưu camera “" + name + "”.", "success");
      }
      S.testToken = null;
      modal("connectModal").hide();
      await load();
    } catch (e) {
      $("connectError").hidden = false; $("connectError").querySelector("span").textContent = e.message;
    } finally { FG.busy(btn, false); }
  });

  /* ------------------------------------------------------------ preview */
  function healthText(h) {
    return [h.label, res(h.resolution), fps(h.measured_fps),
            h.latency_ms !== null && h.latency_ms !== undefined ? "độ trễ " + Math.round(h.latency_ms) + " ms" : null,
            "còn " + h.seconds_left + " giây"].filter(Boolean).join(" · ");
  }

  async function startPreview(body, url, img, info, setId, timerKey) {
    info.textContent = "Đang kết nối…";
    const pv = (await FG.api(url, { body })).preview;
    setId(pv.preview_id);
    img.src = pv.stream_url + "?t=" + Date.now();
    const tick = async () => {
      try {
        const h = (await FG.api("/api/settings/cameras/preview/" + encodeURIComponent(pv.preview_id) + "/health")).health;
        info.textContent = healthText(h);
        S[timerKey] = setTimeout(tick, 1500);
      } catch (e) { info.textContent = "Đã kết thúc xem thử."; img.removeAttribute("src"); }
    };
    S[timerKey] = setTimeout(tick, 800);
  }

  $("camPreview").addEventListener("click", async () => {
    if (!S.testToken) return;
    stopConnectPreview();
    show("camPreviewBox", true);
    FG.busy($("camPreview"), true, "Đang mở…");
    try {
      await startPreview({ test_token: S.testToken }, "/api/settings/cameras/preview/start", $("camPreviewImg"),
                         $("camPreviewInfo"), (id) => (S.previewId = id), "previewTimer");
    } catch (e) { $("camPreviewInfo").textContent = e.message; }
    finally { FG.busy($("camPreview"), false); }
  });

  function stopPreviewId(id) {
    if (id) FG.api("/api/settings/cameras/preview/" + encodeURIComponent(id) + "/stop", { body: {} }).catch(() => {});
  }
  function stopConnectPreview() {
    clearTimeout(S.previewTimer); stopPreviewId(S.previewId); S.previewId = null;
    $("camPreviewImg").removeAttribute("src");
  }
  function stopModalPreview() {
    clearTimeout(S.modalTimer); stopPreviewId(S.modalPreviewId); S.modalPreviewId = null;
    $("previewImg").removeAttribute("src");
  }

  async function previewSaved(c) {
    $("previewTitle").textContent = "Xem thử: " + c.name;
    $("previewInfo").textContent = "Đang kết nối…";
    openModal("previewModal");
    try {
      await startPreview({}, "/api/settings/cameras/" + encodeURIComponent(c.camera_id) + "/preview/start", $("previewImg"),
                         $("previewInfo"), (id) => (S.modalPreviewId = id), "modalTimer");
    } catch (e) { $("previewInfo").textContent = e.message; }
  }
  $("previewStop").addEventListener("click", () => { stopModalPreview(); $("previewInfo").textContent = "Đã ngắt kết nối xem thử."; });

  /* ------------------------------------------------------------ entry */
  $("camManual").addEventListener("click", () => openConnect("manual"));
  $("camManual2").addEventListener("click", () => openConnect("manual"));
  $("camWebcam").addEventListener("click", () => openConnect("webcam"));
  window.addEventListener("pagehide", () => { stopConnectPreview(); stopModalPreview(); });
  load();
})();
