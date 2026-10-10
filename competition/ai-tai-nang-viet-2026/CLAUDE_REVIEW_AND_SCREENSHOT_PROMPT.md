# Prompt Claude Code – Audit hồ sơ + chụp UI thật + cập nhật hồ sơ

Bạn đang làm việc trên repository:
`hoangvan06121982nt-coder/focusguardAI`

Mục tiêu: kiểm tra **toàn bộ hồ sơ AI Tài Năng Việt 2026 – Bảng A THCS** trong:
`competition/ai-tai-nang-viet-2026/`

Sau đó:
1. xác minh mọi claim kỹ thuật với source/test hiện tại;
2. chạy verification cần thiết;
3. chụp screenshot giao diện thật từ phiên bản UI mới nhất;
4. đề xuất/cập nhật vị trí ảnh trong hồ sơ;
5. không tạo claim hoặc dữ liệu giả.

## A. Git truth trước tiên
- Fetch remote.
- Ghi lại `main` SHA hiện tại.
- Ghi lại branch/SHA bạn đang review.
- Không dựa vào handoff cũ nếu repo đã thay đổi.
- Nếu có PR UI/UX mới đang mở, xác định PR/branch mới nhất và audit trên đúng head đó.
- Không merge PR.

## B. Audit từng claim trong hồ sơ
Đọc:
- `competition/ai-tai-nang-viet-2026/01_FocusGuard_HoSo_BangA_8trang_SOURCE.md`
- `02_KichBan_ThuyetTrinh_5Phut.md`
- `03_KichBan_Video_Demo_3Phut.md`
- `04_PromptLog_MinhChung_HuongDan.md`
- `05_Bo_CauHoi_PhanBien.md`

Đối chiếu với:
- `README.md`
- `SUMMARY.md`
- `docs/COMPETITION_ARCHITECTURE.md`
- `docs/PRIVACY_AND_AI_SAFETY.md`
- `docs/REAL_CAMERA_RESULTS.md`
- `.github/workflows/ci.yml`
- implementation thật trong `app.py`, `classroom_ai.py`, `camera_ai.py`, `repository.py`, `focusguard/**`
- test hiện tại và git history.

Tạo bảng audit:
| Claim | Evidence | Status | Action |
|---|---|---|---|
Status chỉ dùng: `VERIFIED`, `STALE`, `UNPROVEN`, `WRONG`.

Không giữ claim `STALE/UNPROVEN/WRONG` trong hồ sơ mà không sửa.

## C. Các claim đặc biệt phải kiểm tra
- 184 tests pass / 0 fail / 9 skip.
- Firestore emulator 6/6.
- hardware 3/3.
- camera thật khoảng 7.5 FPS trên MacBook Air M3.
- kịch bản camera 1–5, 8, 9 PASS.
- scenario 6–7 chưa đủ volunteer.
- real-world accuracy = NOT_EVALUATED.
- không emotion recognition.
- không gaze tracking.
- không ghi video.
- chỉ lưu face embedding theo contract hiện tại.
- client state bị server từ chối.
- authz theo ownership.
- FocusEngine rates.
- ngưỡng PHONE/DROWSY/HEAD_AWAY/AWAY.
- camera mất → freeze score, runtime không bị kết thúc.

Nếu source mới đã thay đổi các con số/ngưỡng này thì cập nhật hồ sơ theo source mới, không giữ số cũ.

## D. Chạy test
Chạy tối thiểu:
```bash
bash scripts/ci_check.sh
```

Nếu môi trường hỗ trợ:
```bash
bash scripts/firestore_emulator_check.sh
```

Không fake hardware result nếu không có camera/model/hardware.
Nếu hardware không chạy được, ghi `HARDWARE_NOT_REVERIFIED_IN_THIS_RUN`, và chỉ dẫn lại evidence đã commit trước đó nếu hợp lệ.

## E. Kiểm tra UI/UX thật
Audit các role:
- teacher
- student
- parent
- admin

Các luồng ưu tiên:
1. login;
2. teacher dashboard;
3. class selection;
4. start live session;
5. live camera/status;
6. behavior event;
7. end session;
8. summary/history;
9. analytics;
10. empty/loading/error states;
11. responsive desktop/tablet/mobile nếu UI mới có thay đổi.

Không được chỉ đọc HTML/CSS rồi kết luận đẹp.
Phải chạy app/browser và xem giao diện render thật nếu môi trường cho phép.

## F. Chụp screenshot thật
Tạo/cập nhật:
`competition/ai-tai-nang-viet-2026/screenshots/`

Ưu tiên:
- `teacher-dashboard.png`
- `live-monitoring.png`
- `behavior-event-phone.png`
- `session-summary.png`
- `analytics-empty-or-real.png`

Yêu cầu:
- screenshot từ app thật;
- không mock số liệu;
- không dựng bằng Photoshop/Figma;
- không lộ secret;
- nếu có khuôn mặt/người thật cần dùng dữ liệu có consent hoặc che/crop hợp lý;
- nếu không thể tạo một state thật, KHÔNG giả state đó; ghi rõ thiếu.

Mỗi screenshot phải ghi trong báo cáo:
- branch;
- commit SHA;
- route/page;
- viewport;
- dữ liệu/state sử dụng;
- claim nào trong hồ sơ được ảnh minh chứng.

## G. Chọn ảnh đưa vào hồ sơ
Đề xuất tối đa 4–5 ảnh, ưu tiên:
1. dashboard giáo viên;
2. live session;
3. event cần chú ý;
4. session summary;
5. analytics.

Hồ sơ tối đa 8 trang nên không nhồi ảnh.
Ảnh phải giúp giám khảo hiểu sản phẩm trong 5 giây.

## H. Review nội dung dưới góc nhìn Bảng A THCS
Đánh dấu:
- đoạn quá kỹ thuật;
- đoạn học sinh khó giải thích;
- câu có nguy cơ bị xem là phóng đại AI;
- nội dung lặp;
- thuật ngữ cần đổi sang tiếng Việt dễ hiểu;
- câu phản biện mà học sinh cần luyện thêm.

Không “làm trẻ con hóa” sản phẩm, nhưng phải đảm bảo học sinh THCS có thể thật sự hiểu và bảo vệ.

## I. Không được làm
- Không invent accuracy.
- Không đổi NOT_EVALUATED thành con số.
- Không nói đội tự xây YOLO/InsightFace/MediaPipe.
- Không tạo screenshot giả.
- Không dùng prompt log dựng lại.
- Không commit secret.
- Không sửa AI/CV semantics chỉ để khớp hồ sơ.
- Không merge main.
- Không xóa evidence cũ nếu chưa hiểu lý do.

## J. Output cuối
Trả:
1. current main SHA;
2. reviewed SHA;
3. claim audit table;
4. tests run + exact results;
5. UI pages verified;
6. screenshots created;
7. hồ sơ đã sửa những gì;
8. claim nào còn BLOCKED;
9. files changed;
10. commit SHA;
11. PR URL;
12. recommendation: READY / NOT_READY_FOR_SUBMISSION.

Nếu `NOT_READY`, nêu đúng các blocker còn lại.

Mục tiêu cuối cùng:
**Hồ sơ phải phản ánh chính xác sản phẩm đang chạy, có screenshot thật, không phóng đại AI, và đủ rõ để học sinh THCS tự bảo vệ trước giám khảo.**
