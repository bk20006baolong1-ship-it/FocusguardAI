# Đối chiếu góp ý của Ban Tổ chức (BTC) — FocusGuard, Bảng A THCS

Trạng thái chung: **`CODE_COMPLETE_CLOUD_VERIFIED_REAL_CLASSROOM_EVIDENCE_PENDING`**.

* **Cloud status** = đã kiểm chứng bằng chạy trực tiếp trên máy chủ đám mây (clone mới, Python 3.12):
  `bash scripts/ci_check.sh`, `bash scripts/firestore_emulator_check.sh`, đi thử giao diện bằng Playwright, chạy thử quy
  trình thực địa bằng `python tools/field_test.py --simulate`. Dữ liệu dùng ở đây là SYNTHETIC/REPLAY (sinh tự động).
* **Real-world status** = cần camera thật, lớp học thật, sự đồng ý thật và nhãn thực tế của người quan sát.
  Chưa thực hiện: **REAL_CLASSROOM_EVIDENCE_PENDING**.
* Số test chứng minh mã chạy đúng thiết kế; **không phải** độ chính xác của AI. Chỉ số trên dữ liệu tổng hợp **không phải**
  độ chính xác thực tế. Benchmark runtime **không phải** tốc độ của YOLOv8/InsightFace.

## A. Lớp học thật (camera lớp, nhiều học sinh, nhìn bảng, chép bài, thảo luận, di chuyển)

| Góp ý | Triển khai | Test | Bằng chứng | Cloud | Thực tế | Còn cần |
|---|---|---|---|---|---|---|
| Đánh giá trên camera lớp học thật | Buổi đánh giá có metadata camera (`evaluation_sessions`: vị trí, góc, độ phân giải, FPS mục tiêu, **FPS thực đo** `actual_fps`, thiết bị, sĩ số, ánh sáng, hoạt động, thời gian, ghi chú, xác nhận đồng ý) — `focusguard/evaluation/real_classroom/service.py`, `models.py` | `tests/test_real_classroom_evaluation.py`, `tests/test_field_evaluation.py`, hợp đồng `contract_field_evaluation_end_to_end` | `evaluation-report.json` của mỗi buổi | CLOUD_VERIFIED | PENDING | Một buổi thật có đồng ý |
| Công cụ thực địa không cần thao tác phức tạp | `python tools/field_test.py` (= `tools/run_classroom_evaluation.py start`): kiểm tra trước → đồng ý → camera → tạo buổi → kịch bản → mẫu bảng nhãn → kết thúc → tổng hợp — `focusguard/evaluation/real_classroom/field.py` | `test_simulated_field_test_runs_every_step`, `test_field_test_without_camera_is_hardware_required`, `test_cli_start_simulate_and_hardware_required` | Không có camera → `STATUS: HARDWARE_REQUIRED` (không bao giờ PASS giả) | CLOUD_VERIFIED (mô phỏng) | PENDING | Máy có webcam |
| Nhìn bảng / chép bài / thảo luận nhóm / di chuyển | Kịch bản 01–04, 07, 14, 15 trong bộ 15 kịch bản (`scenarios.py`), hoạt động lớp do giáo viên chọn | `test_scenario_pack_covers_every_btc_scenario_with_instructions`, `test_context_policy_branches` | Báo cáo theo kịch bản (`scenarios.csv`) | CLOUD_VERIFIED | PENDING | Tình nguyện viên |
| Nhãn thực tế (ground truth) tách khỏi kết quả AI | Bảng `evaluation_annotations` (annotation_id, evaluation_session_id, student_id, label, started_at, ended_at, annotated_by, created_at, note); nhập trên web hoặc CSV; người ghi nhãn lấy từ phiên đăng nhập | `test_observer_csv_import_enforces_consent_and_class`, `contract_evaluation` | Sự kiện AI gốc không bị sửa | CLOUD_VERIFIED | PENDING | Hai người quan sát |

## B. Độ bền khi nhiều người

| Góp ý | Triển khai | Test | Bằng chứng | Cloud | Thực tế | Còn cần |
|---|---|---|---|---|---|---|
| Hai học sinh đi cắt nhau | IdentityManager (danh tính theo track, xác nhận nhiều khung) + kịch bản stress + kịch bản thực địa 08 | `tests/test_multiperson_stress.py`, `test_simulation_exercises_every_scenario_and_the_metrics` | `stress.json` (10/10 SYNTHETIC) | CLOUD_VERIFIED (SYNTHETIC) | PENDING | 2 tình nguyện viên |
| Người lạ | Người chưa đăng ký không bao giờ được gán thành học sinh; kịch bản 09; chỉ số `stranger_false_accept_rate` | như trên + `identity_report` | `identity-report.json` | CLOUD_VERIFIED (SYNTHETIC) | PENDING | 1 người chưa đăng ký (có đồng ý xuất hiện) |
| Che mặt / điện thoại che mặt | Danh tính giữ hoặc UNKNOWN, không gán nhầm; kịch bản 10, 11; `unknown_rate` | như trên | `identity-report.json` | CLOUD_VERIFIED (SYNTHETIC) | PENDING | Camera thật |
| Đổi track / ra rồi vào lại | Phục hồi danh tính; kịch bản 13, 14; `recovery_rate`, `identity_switch_count` | như trên | `identity-report.json` | CLOUD_VERIFIED (SYNTHETIC) | PENDING | Camera thật |
| Đổi góc camera | Kịch bản 12; hiệu chỉnh hướng đầu (`head_pose` trong báo cáo hiệu chỉnh) | `test_calibration_*` | `calibration.json` | CLOUD_VERIFIED (công cụ) | PENDING | Camera thật |
| Lớp đông, hiệu năng | `scripts/benchmark_classroom.py` (5/10/20[/40] học sinh, `RUNTIME_BENCHMARK`); FPS/độ trễ của mô hình đo bởi công cụ thực địa (`FULL_DETECTOR_CAMERA`) | `tests/test_tools_and_benchmark.py` | `benchmark.json`, `performance-report.json` | Runtime: CLOUD_VERIFIED | Mô hình: HARDWARE_REQUIRED | Máy lớp học |

## C. Cảnh báo sai và hiệu chỉnh

| Góp ý | Triển khai | Test | Bằng chứng | Cloud | Thực tế | Còn cần |
|---|---|---|---|---|---|---|
| Hiểu tín hiệu theo hoạt động lớp | 7 hoạt động (LECTURE, WRITING, LOOKING_AT_BOARD, GROUP_DISCUSSION, SELF_STUDY, EXAM, TRANSITION) do giáo viên chọn, AI không tự đoán; `BehaviorInterpretationPolicy` + `behavior_policy.json` (một chỗ); tín hiệu quan sát tách khỏi cách hiểu (`observed type` / `interpreted_state` / `penalty_multiplier`) | `tests/test_context_policy.py`, `test_context_policy_branches`, `test_no_context_scores_exactly_like_before_the_policy`, `test_no_camera_code_ever_sets_the_activity_context` | Cúi đầu khi chép bài = EXPECTED_IN_CONTEXT, không trừ điểm | CLOUD_VERIFIED | PENDING | Nhãn thực tế |
| Chỉ số thực tế | Precision, Recall, F1, ma trận nhầm lẫn, báo nhầm, báo nhầm/giờ, bỏ sót, độ trễ phát hiện, sai số thời lượng, độ chính xác danh tính, đổi danh tính, tỉ lệ UNKNOWN, phục hồi, FPS, P50/P95; `NOT_ENOUGH_DATA` thay vì 0% — `real_classroom/metrics.py` | `tests/test_real_classroom_evaluation.py` | `scripts/finalize_real_classroom_evaluation.py <id>` → 5 tệp báo cáo + CSV | CLOUD_VERIFIED (REPLAY/SYNTHETIC) | PENDING | Buổi thật có nhãn |
| Hiệu chỉnh ngưỡng và hệ số phạt | `scripts/calibrate_behavior.py --evaluation-session <id>`: quét PHONE/DROWSY/HEAD_AWAY/AWAY, góc yaw/pitch, hệ số phạt theo hoạt động; xuất `current_config`, `recommended_config`, `delta`, `evidence`, `coverage`, `confidence`, `sample_count`; **không tự áp dụng** | `test_calibration_reports_coverage_confidence_and_sample_count`, `tests/test_tools_and_benchmark.py` | `calibration.json`, `calibration.md` | CLOUD_VERIFIED (công cụ) | PENDING (`UNCALIBRATED_DEFAULT`) | Dữ liệu thật + người duyệt |

## D. Giáo viên xem lại

| Góp ý | Triển khai | Test | Bằng chứng | Cloud | Thực tế | Còn cần |
|---|---|---|---|---|---|---|
| Lịch sử sự kiện, Xác nhận đúng / Báo nhầm / Không chắc, ghi chú | Trang **Sự kiện**; `event_reviews` là bản ghi riêng, sự kiện AI gốc không đổi; người xem lại lấy từ phiên đăng nhập — `focusguard/review.py` | `tests/test_teacher_review.py`, `tests/test_review_regressions.py` | Đi thử Playwright: lưu "Không chắc" từ cửa sổ xem lại | CLOUD_VERIFIED | PENDING (tỉ lệ báo nhầm do giáo viên xác nhận) | Giáo viên dùng trên lớp |
| Quyền | Giáo viên của lớp: được; giáo viên lớp khác, học sinh, phụ huynh, quản trị viên: bị chặn | `tests/test_security_btc.py`, `test_teacher_of_another_class_cannot_read_class_analytics` | 403 | CLOUD_VERIFIED | — | — |
| "Cần giáo viên xem lại" (không xếp hạng tiêu cực) | Hàng chờ theo số sự kiện chưa xem lại, số sự kiện gần đây, tổng thời lượng; lời lẽ trung lập; trang Phân tích liệt kê theo **tên**, không có thứ hạng | `test_no_page_ranks_or_judges_students`, `test_todays_scores_are_listed_by_name_without_positions` | — | CLOUD_VERIFIED | — | — |
| Kết quả xem lại được dùng | Thống kê sự kiện bỏ các sự kiện giáo viên xác nhận báo nhầm (có số lượng đã loại); tổng kết buổi hiện trạng thái xem lại | `test_false_positive_reviews_are_excluded_from_event_statistics` | — | CLOUD_VERIFIED | — | — |
| Đoạn minh chứng (Evidence Mode) | Tắt mặc định; vòng đệm RAM, vài giây trước/sau sự kiện; UUID; ngoài `static/`; chỉ giáo viên của lớp; xem/xóa đều ghi nhật ký; không tự phát; tự xóa sau 24 giờ; phụ huynh không xem được; **chỉ khung hình đã cắt theo đúng học sinh, lấy từ ảnh chưa vẽ tên/điểm**; kiểm tra lại đồng ý khi lưu; tệp thiếu/hỏng → 410 và gỡ | `tests/test_evidence.py`, `tests/test_privacy_hardening.py` | Đi thử Playwright: cửa sổ minh chứng | CLOUD_VERIFIED | PENDING | Bật trên máy thật nếu nhà trường muốn |

## E. Quyền riêng tư

| Góp ý | Triển khai | Test | Bằng chứng | Cloud | Thực tế | Còn cần |
|---|---|---|---|---|---|---|
| Đồng ý của phụ huynh theo phạm vi | `consents` (consent_id, student_id, guardian_name, guardian_relationship, 3 phạm vi, policy_version, accepted_at, expires_at ≤ 10 năm, revoked_at, created_by); trạng thái NOT_PROVIDED / ACTIVE / EXPIRED / REVOKED do **máy chủ** tính | `tests/test_consent_privacy.py`, `test_explicit_consent_expiry_is_capped` | Đi thử Playwright: ghi nhận + thu hồi | CLOUD_VERIFIED | PENDING | Phiếu đồng ý thật ([mẫu](CONSENT_FIELD_PACK.md)) |
| Sinh trắc học | Không có đồng ý phạm vi khuôn mặt → không đăng ký, không nhận diện; thu hồi → xóa vector + minh chứng + ghi nhật ký; đồng ý mới bỏ phạm vi → xóa dữ liệu của phạm vi đó; xóa tài khoản / đổi vai trò → xóa vector; vector không bao giờ qua API, log hay tệp xuất | `test_enrollment_blocked_without_consent`, `test_revoke_deletes_embedding_and_evidence_and_is_audited`, `test_new_consent_without_face_scope_deletes_embedding`, `test_deleting_a_student_account_removes_face_data_with_audit` | — | CLOUD_VERIFIED | — | — |
| Thời hạn lưu, xóa tự động | Vector 365 ngày, sự kiện 365 ngày, minh chứng 24 giờ, dữ liệu đánh giá 90 ngày; quét khi khởi động và mỗi 10 phút; xóa nhận xét mồ côi | `test_retention_*` | Nhật ký `RETENTION_RUN` (ghi người chạy) | CLOUD_VERIFIED | — | — |
| Trang Dữ liệu & quyền riêng tư | Trạng thái đồng ý, phiên bản chính sách của từng học sinh, có vector hay không, số minh chứng, lần đăng ký, thời hạn; ghi nhận / thu hồi / xóa vector / xóa minh chứng (có hộp xác nhận) | `test_privacy_dashboard_*`, `test_privacy_rows_show_each_students_consent_policy_version` | Đi thử Playwright | CLOUD_VERIFIED | — | — |
| Nhật ký | CONSENT_GRANTED, CONSENT_REVOKED, FACE_ENROLLED, FACE_DELETED, EVIDENCE_VIEWED, EVIDENCE_DELETED, POLICY_CHANGED, EVENT_REVIEWED (+ EVIDENCE_CREATED, DATA_EXPORTED, RETENTION_RUN, ACTIVITY_CONTEXT_CHANGED); không chứa ảnh, vector, mật khẩu, khoá, token | `test_audit_never_stores_biometric_vectors_or_secrets`, `test_audit_keeps_counts_but_not_encoded_data` | — | CLOUD_VERIFIED | — | — |
| Báo cáo công khai không lộ dữ liệu | `scripts/artifact_privacy_check.py` (tự kiểm bằng mẫu xấu cài sẵn) chạy trong `ci_check.sh`, công cụ tổng hợp và công cụ thực địa | `test_artifact_privacy_scanner_self_test_and_cli` | `ARTIFACT_PRIVACY_CHECK: PASS` | CLOUD_VERIFIED | — | — |
| Bảo mật | Phân quyền theo quyền sở hữu, chống IDOR, CSRF (SameSite + kiểm tra Origin), chống chèn công thức CSV, thoát HTML, chống path traversal | `tests/test_security*.py`, `tests/test_authorization.py`, `test_cross_site_writes_are_rejected`, `test_csv_export_neutralises_formulas` | `scripts/security_check.py` PASS | CLOUD_VERIFIED | — | — |
| Firestore cùng hợp đồng với SQLite | Mọi bảng mới dùng chung lớp `DomainTablesMixin` | `tests/repo_contract.py` chạy trên cả hai | `bash scripts/firestore_emulator_check.sh` | CLOUD_VERIFIED (emulator) | Production chưa kiểm tra | Dự án Firebase thật (nếu dùng) |

## F. Camera lớp học, danh tính và điểm danh

| Góp ý / yêu cầu | Triển khai | Test | Bằng chứng | Cloud | Thực tế | Còn cần |
|---|---|---|---|---|---|---|
| Dùng camera lớp (IP / EZVIZ), không chỉ webcam | `focusguard/cameras/` (CameraSource, LatestFrameReader, RTSP/ONVIF tối giản, tìm trong LAN, kết nối dễ), **Cài đặt › Camera** | `tests/test_cameras.py`, `tests/test_camera_security.py`, `contract_cameras` | máy chủ RTSP mô phỏng; `tools/ip_camera_check.py` → `IP_CAMERA_HARDWARE_REQUIRED` khi không có camera | CLOUD_VERIFIED (mô phỏng) | `EZVIZ_HARDWARE_VERIFICATION_PENDING` | Camera EZVIZ C1C/H1C trong LAN |
| Mất tín hiệu camera không bị coi là rời chỗ | `NO_SIGNAL` / `RECONNECTING`, đóng băng điểm, `BehaviorAnalyzer.suspend()` | `test_camera_loss_never_becomes_away`, `test_episode_open_when_the_camera_dies_ends_at_the_last_frame` | — | CLOUD_VERIFIED | PENDING | Rút dây camera trong buổi thật |
| Gắn danh tính cho cả lớp | Khởi tạo danh sách (`INITIALIZING`), giáo viên bấm người → chọn tên, sửa tên, xác nhận hàng loạt đề xuất chắc chắn | `tests/test_classroom_identity.py`, walkthrough `classroom_identity_*` (lớp mô phỏng) | `ui-walkthrough` | CLOUD_VERIFIED (SYNTHETIC) | PENDING | Lớp thật |
| Nhận diện bền khi đổi góc mặt | Hồ sơ nhiều mẫu (`face_profiles`), lọc chất lượng, chọn đa dạng theo hướng mặt, học dần có kiểm soát | `tests/test_classroom_identity.py`, `contract_face_profiles` | — | CLOUD_VERIFIED (SYNTHETIC) | PENDING (`UNCALIBRATED_DEFAULT`) | Buổi thật + bảng danh tính |
| Điểm danh tự động không nhầm vắng | `NOT_SEEN`/`PRESENT`/`LATE`/`ABSENT`, người chưa xác định không biến thành vắng, giáo viên sửa được khóa | `tests/test_classroom_identity.py`, `contract_identity_bindings_and_attendance` | — | CLOUD_VERIFIED | PENDING | So với điểm danh tay |
| Gắn tên ≠ đồng ý sinh trắc | Không có đồng ý → chỉ trong buổi, không tạo vector; thu hồi → xóa mọi mẫu, hủy mẫu đang chờ | `tests/test_classroom_identity.py`, `tests/test_consent_privacy.py` | — | CLOUD_VERIFIED | — | — |

## Phần chỉ con người làm được

1. Phiếu đồng ý thật của phụ huynh/học sinh và ghi nhận trên trang Quyền riêng tư.
2. Một buổi (đề xuất ≥ 5 học sinh) với camera thật chạy `python tools/field_test.py`, đủ 15 kịch bản.
3. Hai người quan sát điền bảng nhãn và bảng danh tính; `scripts/finalize_real_classroom_evaluation.py`.
4. Camera EZVIZ / IP thật trong LAN: `python tools/ip_camera_check.py --camera-name <tên>` (hiện
   `EZVIZ_HARDWARE_VERIFICATION_PENDING`).
5. Duyệt đề xuất hiệu chỉnh trước khi áp dụng; cập nhật hồ sơ chỉ với số liệu từ gói `REAL` đạt điều kiện.
