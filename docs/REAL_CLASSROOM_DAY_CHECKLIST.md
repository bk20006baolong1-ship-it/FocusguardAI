# Checklist ngày đánh giá trên lớp học thật

Một trang, in ra mang theo. Chi tiết: [REAL_CLASSROOM_EVALUATION_PROTOCOL.md](REAL_CLASSROOM_EVALUATION_PROTOCOL.md).

## TRƯỚC BUỔI HỌC (1 tuần → 1 ngày trước)

- [ ] **Đồng ý:** phiếu của phụ huynh đã ký cho từng học sinh tham gia ([mẫu](CONSENT_FIELD_PACK.md)). Quản trị viên
      ghi nhận trên **Quản trị › Dữ liệu & quyền riêng tư**, chọn phạm vi *Nhận diện khuôn mặt* + *Tham gia đánh giá thử
      nghiệm* (chỉ chọn *Đoạn minh chứng* nếu nhà trường bật Evidence Mode).
- [ ] **Người tham gia:** ≥ 5 học sinh đã đồng ý (khuyến nghị); 1 người chưa đăng ký khuôn mặt (có đồng ý xuất hiện)
      cho kịch bản người lạ; 2 người quan sát.
- [ ] **Đăng ký khuôn mặt** cho học sinh đã đồng ý (Quản trị › Tài khoản). Máy chủ từ chối nếu chưa có đồng ý.
- [ ] **Máy:** `pip install -r requirements.txt`, `python tools/download_models.py`, `python tools/field_test.py --simulate`
      chạy hết không lỗi (diễn tập, dữ liệu tổng hợp).
- [ ] **Lưu trữ:** còn ≥ 500 MB trống; `evaluation/private/` không bị đồng bộ lên dịch vụ đám mây công cộng.
- [ ] **Quyền riêng tư:** thông báo cho cả lớp; camera đặt công khai; không quay lén.

## LẮP ĐẶT (30 phút trước)

- [ ] **Vị trí:** trên bảng (`FRONT_ABOVE_BOARD`) hoặc góc trước lớp (`FRONT_CORNER`), cao 2–2,5 m.
- [ ] **Góc:** nghiêng xuống 15–25°, thấy mặt từ bàn đầu tới bàn cuối.
- [ ] **Độ phân giải:** ≥ 1280×720 (công cụ đo và ghi lại).
- [ ] **Ánh sáng:** tránh ngược sáng từ cửa sổ; ghi lại điều kiện (DAYLIGHT / MIXED / ARTIFICIAL / LOW_LIGHT / BACKLIT).
- [ ] `python tools/run_classroom_evaluation.py check` → `HARDWARE_OK`.
- [ ] Camera IP: `python tools/ip_camera_check.py --camera-name <tên camera> --seconds 30 --test-reconnect` → `CONNECTED`
      (ghi lại độ phân giải, FPS, `FRAME_AGE_MS`; không chụp màn hình có mật khẩu / địa chỉ).
- [ ] Khởi tạo danh sách: bấm từng người trên hình → chọn tên; đến khi “Chưa xác định: 0”.

## CHẠY (45–60 phút)

```bash
python tools/field_test.py --class-id <id lớp> --teacher <tên đăng nhập giáo viên> [--camera-name <tên camera>]
```

Công cụ dẫn từng bước và hiện hướng dẫn của từng kịch bản. Enter để bắt đầu, Enter để kết thúc, `s` bỏ qua, `q` dừng.

| # | Kịch bản | # | Kịch bản |
|---|---|---|---|
| 01 | Nghe giảng | 09 | Người lạ đi qua |
| 02 | Chép bài | 10 | Che mặt |
| 03 | Nhìn bảng (20 giây đầu: cả lớp nhìn bảng) | 11 | Điện thoại che mặt |
| 04 | Thảo luận nhóm | 12 | Đổi góc camera |
| 05 | Dùng điện thoại | 13 | Đổi track (che khuất ngắn) |
| 06 | Giả lập buồn ngủ | 14 | Ra ngoài rồi quay lại |
| 07 | Rời chỗ > 10 giây | 15 | Lớp đông, hoạt động hỗn hợp |
| 08 | Hai học sinh đi cắt nhau | | |

- [ ] **Người quan sát** ghi nhãn theo thời gian (đồng hồ của công cụ, giây tính từ lúc bắt đầu) cho từng học sinh:
      `FOCUSED, WRITING, LOOKING_AT_BOARD, GROUP_DISCUSSION, PHONE_DISTRACTION, DROWSY, AWAY, UNKNOWN`.
      Không chắc → `UNKNOWN` (không tính).
- [ ] Kịch bản 07–15: ghi **số track** hiện trên khung hình → học sinh (người lạ: `STRANGER`).

## SAU BUỔI HỌC (cùng ngày)

- [ ] Điền vào hai mẫu công cụ đã tạo trong `evaluation/private/<id>/`:
      `observer-annotations-template.csv` (cột `label`) và `observer-identity-template.csv`.
- [ ] **Tổng hợp báo cáo:**
      `python scripts/finalize_real_classroom_evaluation.py <id> --annotations-csv <file> --teacher <giáo viên> --identity-csv <file> --calibrate`
      → `evaluation/reports/real-classroom/<id>/` phải in `ARTIFACT_PRIVACY_CHECK: PASS`.
- [ ] **Hiệu chỉnh:** đọc `calibration` trong báo cáo; chỉ áp dụng khi giáo viên/ban dự án đồng ý (không tự áp dụng).
- [ ] **Xem lại sự kiện:** giáo viên đánh dấu Xác nhận đúng / Báo nhầm / Không chắc trên trang **Sự kiện**.
- [ ] **Đoạn minh chứng** (nếu có bật): xem hoặc xóa trong 24 giờ; sau đó tự xóa.
- [ ] **Dọn dữ liệu tạm:** giữ báo cáo (đã quét) để nộp; dữ liệu thô `evaluation/private/<id>/` tự xóa sau 90 ngày —
      xóa sớm hơn nếu không còn cần. Không sao chép dữ liệu thô sang USB/đám mây công cộng.
- [ ] Điền phần con người trong [mẫu báo cáo](../evaluation/templates/real_classroom_report.md) (giới hạn, kết luận) và
      cập nhật hồ sơ thi **chỉ** với số liệu có `REAL_WORLD_EVIDENCE: YES`.
