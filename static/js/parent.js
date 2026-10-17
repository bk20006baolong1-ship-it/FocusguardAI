/* Parent: revoke consent for the linked child (server checks the link). */
(function () {
  "use strict";
  const FG = window.FG;
  document.addEventListener("DOMContentLoaded", () => {
    const sec = document.getElementById("parentPrivacy"), btn = document.getElementById("parentRevoke");
    if (!sec || !btn) return;
    btn.addEventListener("click", async () => {
      if (FG.isBusy(btn)) return;
      const ok = await FG.confirm({
        title: "Thu hồi đồng ý?", body: "Camera lớp học sẽ dừng nhận diện khuôn mặt của con ngay.",
        consequences: ["Dữ liệu khuôn mặt của con bị xóa.", "Đoạn minh chứng liên quan bị xóa.",
                       "Muốn dùng lại cần gửi bản đồng ý mới cho nhà trường."],
        confirmLabel: "Thu hồi đồng ý", danger: true });
      if (!ok) return;
      FG.busy(btn, true, "Đang thu hồi…");
      try {
        const res = await FG.api("/api/consent/" + encodeURIComponent(sec.dataset.student) + "/revoke", { body: { reason: "parent_request" } });
        FG.toast(res.message, "success");
        setTimeout(() => window.location.reload(), 1200);
      } catch (e) { FG.toast(e.message, "danger"); FG.busy(btn, false); }
    });
  });
})();
