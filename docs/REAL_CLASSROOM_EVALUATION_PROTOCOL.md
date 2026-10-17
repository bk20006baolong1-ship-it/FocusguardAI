# Quy trình đánh giá trên lớp học thật

Tài liệu này trả lời góp ý của Ban Tổ chức (lớp học trực tiếp, nhiều học sinh, cảnh báo sai, hiệu chỉnh).
Đây là **quy trình cần con người và camera thật**. Cloud CI chỉ kiểm chứng công cụ và logic trên dữ liệu
SYNTHETIC/REPLAY; nó **không** đo được độ chính xác thực tế. Mọi con số độ chính xác chỉ được công bố sau khi
hoàn thành quy trình này, có sự đồng ý và có nhãn thực tế (ground truth).

Trạng thái hiện tại: **REAL_CLASSROOM_EVIDENCE_PENDING** — bộ công cụ đã hoàn thành và chạy thử (mô phỏng) trên cloud;
buổi đánh giá thật **HARDWARE_REQUIRED / HUMAN_GROUND_TRUTH_REQUIRED** (chưa thực hiện).

Bản rút gọn để in mang theo: [REAL_CLASSROOM_DAY_CHECKLIST.md](REAL_CLASSROOM_DAY_CHECKLIST.md). Mẫu đồng ý:
[CONSENT_FIELD_PACK.md](CONSENT_FIELD_PACK.md). Mẫu báo cáo: `evaluation/templates/real_classroom_report.md`.

## 0. Nguyên tắc

* Không ghi hình lén. Camera đặt công khai, học sinh và phụ huynh được thông báo trước.
* Chỉ học sinh có bản ghi đồng ý **còn hiệu lực** với phạm vi *Tham gia đánh giá thử nghiệm* mới được gán nhãn
  và có mặt trong gói dữ liệu (máy chủ kiểm tra, không dựa vào giao diện).
* Công cụ chỉ lưu **số liệu** (EAR, góc đầu, độ tin cậy điện thoại, quyết định danh tính theo track, thời gian khung
  hình, sự kiện AI, hoạt động lớp). Không lưu ảnh, video hay vector khuôn mặt. Nếu nhà trường bật Evidence Mode thì
  áp dụng riêng chính sách ở `docs/CONSENT_AND_RETENTION.md`.
* Dữ liệu đánh giá nằm ở `evaluation/private/<id>/` (git-ignore) và bị xóa sau `EVALUATION_DATA_RETENTION_DAYS`.
* Tín hiệu AI không phải kết luận sư phạm. Kết quả đánh giá dùng để chỉnh hệ thống, không dùng để nhận xét học sinh.

## 1. Chuẩn bị (1 tuần trước)

1. Gửi thư thông báo + mẫu đồng ý cho phụ huynh (mục đích, dữ liệu thu, thời hạn lưu, quyền thu hồi).
2. Quản trị viên ghi nhận đồng ý ở **Quản trị > Dữ liệu & quyền riêng tư > Ghi nhận đồng ý**
   (họ tên + quan hệ người giám hộ, phạm vi *Nhận diện khuôn mặt* và *Tham gia đánh giá thử nghiệm*).
3. Đăng ký khuôn mặt cho học sinh đã đồng ý (Quản trị > Tài khoản). Máy chủ từ chối nếu chưa có đồng ý.
4. Chuẩn bị 2 người quan sát (observer) và bảng thời gian (mẫu CSV ở mục 6).

## 2. Lắp đặt camera

| Hạng mục | Yêu cầu | Ghi vào metadata |
|---|---|---|
| Vị trí | Trên bảng (`FRONT_ABOVE_BOARD`) hoặc góc trước lớp (`FRONT_CORNER`), cao 2–2,5 m | `camera_position`, `camera_angle` |
| Góc | Nghiêng xuống 15–25°, thấy được mặt các bàn đầu tới bàn cuối | `camera_angle` |
| Độ phân giải | Tối thiểu 1280x720; ghi lại giá trị thực | `resolution` |
| FPS mục tiêu | Ghi FPS mong muốn; FPS thực đo bởi công cụ | `fps_target` |
| Thiết bị | Máy tính + camera (model) | `device` |
| Ánh sáng | Tự nhiên / hỗn hợp / đèn / thiếu sáng / ngược sáng | `lighting_condition` |
| Chỗ ngồi | Sơ đồ chỗ ngồi, số học sinh trong khung hình | `student_count`, `notes` |

**Camera IP (EZVIZ / RTSP / ONVIF):** lưu camera trong **Cài đặt › Camera** (tên ví dụ `classroom-front`), rồi kiểm
tra `python tools/ip_camera_check.py --camera-name classroom-front --seconds 30 --test-reconnect` (không có camera →
`IP_CAMERA_HARDWARE_REQUIRED`). Metadata buổi đánh giá chỉ ghi mã / tên / loại / hãng / model camera — không IP, không
mật khẩu. Xem [IP_CAMERA_SETUP.md](IP_CAMERA_SETUP.md). Khuôn mặt ở bàn cuối nên ≥ 64 px để tạo mẫu nhận diện.

**Khởi tạo danh sách (trước kịch bản 01):** giáo viên bắt đầu buổi với “Khởi tạo danh sách bằng camera”, bấm từng
người trên hình và chọn tên (chỉ học sinh có đồng ý nhận diện mới được tạo mẫu), rồi “Hoàn tất danh sách & bắt đầu”.
Ghi lại số lần gắn tên, số lần sửa tên và số người “Chưa xác định” còn lại — xem
[CLASSROOM_IDENTITY.md](CLASSROOM_IDENTITY.md). Điểm danh tự động so với điểm danh tay của giáo viên:
[ATTENDANCE.md](ATTENDANCE.md).

**Hiệu chỉnh hướng đầu (bắt buộc với camera đặt ở góc):** trong 1 phút đầu, cả lớp nhìn lên bảng 20 giây
(nhãn `LOOKING_AT_BOARD`) rồi cúi xuống vở 20 giây (nhãn `WRITING`). `scripts/calibrate_behavior.py` dùng hai
đoạn này để đề xuất `yaw_center_deg`, `pitch_center_deg` và dấu của trục cúi đầu (`pitch_down_positive`).

## 3. Người tham gia

* Đợt 1: **5–10 tình nguyện viên** đã đồng ý, một lớp, 2–3 buổi × 20–30 phút.
* Đợt 2 (nếu đợt 1 đạt): một lớp thật sĩ số đầy đủ, nhiều buổi, nhiều điều kiện ánh sáng.

## 4. Kịch bản (bộ `btc-2026.1`, 15 kịch bản, mỗi kịch bản 2–5 phút)

Định nghĩa đầy đủ (các bước cho tình nguyện viên, hướng dẫn cho người quan sát, nhãn thường gặp, chỉ số đo) nằm trong
`focusguard/evaluation/real_classroom/scenarios.py` và hiện ngay trên công cụ thực địa / trang **Đánh giá thực tế**.
Hệ thống chỉ ghi lại **khoảng thời gian** của từng kịch bản (bảng `evaluation_scenarios`) để tách chỉ số theo kịch bản.

| # | Kịch bản | Nhóm góp ý | Hoạt động đề xuất | Nhãn thường gặp | Đo gì |
|---|---|---|---|---|---|
| 01 | Nghe giảng | A | `LECTURE` | `FOCUSED` | báo nhầm/giờ |
| 02 | Chép bài | A | `WRITING` | `WRITING`, `LOOKING_AT_BOARD` | cúi đầu không bị tính là quay đi |
| 03 | Nhìn bảng (20 s đầu cả lớp nhìn bảng: hiệu chỉnh hướng đầu) | A | `LOOKING_AT_BOARD` | `LOOKING_AT_BOARD` | góc lệch camera không bị phạt |
| 04 | Thảo luận nhóm (quay sang bạn, đổi chỗ ngắn) | A | `GROUP_DISCUSSION` | `GROUP_DISCUSSION` | quay đầu, rời ghế ngắn không bị coi là mất tập trung |
| 05 | Dùng điện thoại (2–3 em, 10–30 s) | A | `LECTURE` | `PHONE_DISTRACTION` | precision/recall PHONE |
| 06 | Giả lập buồn ngủ (nhắm mắt 5–15 s) | A | `LECTURE` | `DROWSY` | precision/recall DROWSY; chớp mắt không báo |
| 07 | Rời chỗ > 10 s rồi quay lại | A | `LECTURE` | `AWAY` | AWAY + nhận lại đúng học sinh |
| 08 | Hai học sinh đi cắt nhau | B | `TRANSITION` | — (bảng danh tính) | đổi danh tính, không gán nhầm |
| 09 | Người lạ (chưa đăng ký, đã đồng ý xuất hiện) | B | `TRANSITION` | — (bảng danh tính) | không bao giờ nhận là học sinh |
| 10 | Che mặt (tay, khẩu trang) 5–10 s | B | `LECTURE` | `FOCUSED` | giữ danh tính hoặc UNKNOWN |
| 11 | Điện thoại che mặt 10–20 s | B | `LECTURE` | `PHONE_DISTRACTION` | PHONE + danh tính khi bị che |
| 12 | Đổi góc camera giữa buổi | B | `LECTURE` | `FOCUSED`, `LOOKING_AT_BOARD`, `WRITING` | danh tính trước/sau, hướng đầu |
| 13 | Đổi track (một em đi ngang che bạn 2–5 s, 3 lần) | B | `LECTURE` | `FOCUSED` | phục hồi danh tính |
| 14 | Ra ngoài 30–60 s rồi quay lại | B | `LECTURE` | `AWAY` | AWAY + phục hồi danh tính |
| 15 | Lớp đông, hoạt động hỗn hợp (5 phút) | B | `LECTURE` (đổi khi lớp đổi) | đầy đủ | FPS, danh tính, báo nhầm/giờ |

Giáo viên/tester chọn hoạt động bằng tay (trang Giám sát, trang Đánh giá thực tế, hoặc công cụ thực địa đổi theo kịch bản
khi người vận hành đồng ý). AI không tự đoán hoạt động.

## 5. Chạy công cụ

```bash
pip install -r requirements.txt && python tools/download_models.py   # máy có webcam
python tools/field_test.py --simulate                               # diễn tập (SYNTHETIC, CSDL tạm), không cần camera
python tools/field_test.py --class-id 1 --teacher teacher           # buổi thật, webcam 0
python tools/field_test.py --class-id 1 --teacher teacher --camera-name classroom-front   # camera đã lưu
```

`tools/field_test.py` (hoặc `python tools/run_classroom_evaluation.py start`) dẫn từng bước:

1. **Kiểm tra trước:** Python, thư viện camera, tệp mô hình, camera, thư mục riêng tư (git-ignore), dung lượng, CSDL,
   trạng thái Evidence Mode.
2. **Đồng ý:** liệt kê học sinh có phạm vi *Tham gia đánh giá thử nghiệm* (máy chủ kiểm tra); người vận hành xác nhận phiếu
   giấy khớp danh sách. Không có ai → `STATUS: CONSENT_REQUIRED`, dừng.
3. **Camera:** đo độ phân giải, hỏi vị trí, góc, ánh sáng, thiết bị.
4. **Tạo buổi đánh giá** (`dataset_kind = REAL`, `consent_verified`).
5. **Kịch bản:** hiện hướng dẫn, Enter bắt đầu / Enter kết thúc / `s` bỏ qua / `q` dừng.
6. **Kết thúc buổi:** lưu FPS thực đo, quyết định danh tính theo track và thời gian mỗi khung (số liệu, không ảnh).
7. **Nhãn:** tạo `observer-annotations-template.csv` (cột nhãn để trống — nhãn gợi ý chỉ để tham khảo) và
   `observer-identity-template.csv` trong `evaluation/private/<id>/`; nhập ngay nếu đã có.
8. **Tổng hợp:** báo cáo ở `evaluation/reports/real-classroom/<id>/`, quét quyền riêng tư.
9. **Kết quả:** đường dẫn báo cáo, `REAL_WORLD_EVIDENCE: YES/NO`, `ARTIFACT_PRIVACY_CHECK`.

Không có webcam hoặc thư viện camera: `STATUS: HARDWARE_REQUIRED` và thoát mã 0 (`--strict` → mã 2); không có kết quả giả.

Cách khác (không cần dòng lệnh): giáo viên bắt đầu buổi học ở chế độ *Camera lớp học*, mở **Đánh giá thực tế**, điền
thông tin camera, xác nhận đồng ý, chọn kịch bản (có hướng dẫn) và gán nhãn ngay trên trang. Công cụ cũ
`tools/run_classroom_evaluation.py record` vẫn dùng được.

## 6. Ground truth (người quan sát)

* Hai người quan sát độc lập ghi bảng thời gian cho từng học sinh: `student_id,label,start_s,end_s,note`
  (giây tính từ lúc bắt đầu đánh giá). Nhãn: `FOCUSED, WRITING, LOOKING_AT_BOARD, GROUP_DISCUSSION,
  PHONE_DISTRACTION, DROWSY, AWAY, OFF_TASK_LOOKING_AWAY, UNKNOWN` (`UNKNOWN` = không chấm).
* Bảng danh tính (cho kịch bản 8–11): `track_id,student_id,start_s,end_s` (`STRANGER` cho người lạ), đọc số track
  trên khung hình xem trước.
* Hai người lệch nhau > 2 s ở ranh giới: thống nhất lại hoặc đánh `UNKNOWN`.

```bash
# một lệnh: nhập nhãn + bảng danh tính, tính chỉ số, hiệu chỉnh (đề xuất), viết báo cáo, quét quyền riêng tư
python scripts/finalize_real_classroom_evaluation.py <id> --annotations-csv observer.csv --teacher teacher \
    --identity-csv identity.csv --calibrate
# hoặc từng bước
python tools/run_classroom_evaluation.py import-annotations --evaluation-session <id> --teacher teacher --csv observer.csv
python tools/run_classroom_evaluation.py import-identity --evaluation-session <id> --csv identity.csv
python scripts/calibrate_behavior.py --evaluation-session <id> --grid full
```

`finalize` viết `evaluation-report.json`, `evaluation-summary.md`, `confusion-matrix.json`, `identity-report.json`,
`performance-report.json`, `metrics.csv`, `confusion-matrix.csv`, `scenarios.csv`: chỉ mã học sinh, nhãn, thời gian và
siêu dữ liệu sự kiện AI; ghi chú tự do không được đưa vào; tên, ảnh, video, vector khuôn mặt, đoạn minh chứng không bao
giờ có. Nhập nhãn qua CSV chịu cùng kiểm tra như trên web (đồng ý, đúng lớp, trong thời gian buổi).

Nhãn thực tế luôn lưu **song song** với kết quả AI (bảng `evaluation_annotations` / mục `annotations` của gói),
không bao giờ ghi đè sự kiện AI.

## 7. Chỉ số

Tính bởi `focusguard/evaluation/real_classroom/metrics.py`, cho PHONE, DROWSY, HEAD_AWAY, AWAY và danh tính:

* Precision, Recall, F1; ma trận nhầm lẫn theo thời gian (nhãn thực tế × trạng thái AI);
* số báo nhầm, **báo nhầm/giờ** (theo giờ-học-sinh có nhãn), bỏ sót; báo nhầm theo nhãn thực tế và theo hoạt động;
* độ trễ phát hiện (onset latency), sai số thời lượng;
* danh tính: độ chính xác khi gán, số lần đổi danh tính, tỉ lệ UNKNOWN, tỉ lệ nhận nhầm người lạ, tỉ lệ phục hồi;
* hiệu năng: FPS thực, độ trễ P50/P95 mỗi khung hình, độ trễ danh tính.

Hai góc nhìn: `raw` (mọi tín hiệu AI) và `context_aware` (chỉ tín hiệu còn là cảnh báo sau khi áp dụng hoạt động).

**Ngưỡng dữ liệu tối thiểu** (dưới ngưỡng → `NOT_ENOUGH_DATA`, không bao giờ hiển thị 0%): precision cần ≥ 3 sự
kiện AI trong thời gian có nhãn; recall ≥ 3 sự kiện thực tế; báo nhầm/giờ ≥ 0,25 giờ-học-sinh có nhãn; danh tính
≥ 50 khung hình có nhãn; hiệu năng ≥ 30 khung hình. Kết quả chỉ được coi là bằng chứng thực tế
(`is_real_world_evidence = true`) khi `dataset_kind = REAL`, đồng ý đã xác nhận và đủ dữ liệu.

## 8. Hiệu chỉnh và phê duyệt

`calibrate_behavior.py` đề xuất ngưỡng thời gian (PHONE/DROWSY/HEAD_AWAY/AWAY), ngưỡng góc đầu, tâm hướng nhìn và
hệ số phạt theo hoạt động; xuất `calibration-<thời gian>.json` + bản tóm tắt Markdown với `current_config`,
`recommended_config`, `delta`, `evidence`, `coverage` (thời gian có nhãn, số sự kiện thực tế), `confidence` theo tín
hiệu (LOW/MEDIUM/HIGH hoặc NOT_ENOUGH_DATA theo số sự kiện thực tế) và `sample_count`. Công cụ **không tự áp dụng**. Giáo viên/ban dự án xem bằng chứng, rồi mới
cập nhật biến `FG_*` hoặc `focusguard/policies/behavior_policy.json` (đổi `calibration_status`).

## 9. Báo cáo kết quả

Cập nhật `docs/REAL_CAMERA_RESULTS.md` và hồ sơ thi chỉ với số liệu từ gói `REAL` đạt điều kiện, kèm: số học sinh,
số buổi, tổng giờ có nhãn, điều kiện camera/ánh sáng, các chỉ số ở mục 7 và các trường hợp lỗi tiêu biểu.
