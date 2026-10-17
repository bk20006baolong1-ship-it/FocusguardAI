# Báo cáo đánh giá FocusGuard trên lớp học thật — MẪU

> Phần **[TỰ ĐỘNG]** được `python scripts/finalize_real_classroom_evaluation.py <id>` sinh ra trong
> `evaluation/reports/real-classroom/<id>/evaluation-summary.md` (số liệu lấy từ `evaluation-report.json`,
> `confusion-matrix.json`, `identity-report.json`, `performance-report.json`). Phần **[NGƯỜI VIẾT]** do đội dự án điền.
> Chỉ công bố số liệu khi báo cáo ghi `REAL_WORLD_EVIDENCE: YES`. `NOT_ENOUGH_DATA` nghĩa là chưa đủ dữ liệu, không phải 0%.
> Không đưa tên học sinh, ảnh, video hay dữ liệu khuôn mặt vào báo cáo này.

* Mã buổi đánh giá (evaluation_session_id): `…`
* Commit / phiên bản chính sách / bộ kịch bản: `…` / `…` / `btc-2026.1`
* Ngày, địa điểm (trường, lớp — không ghi tên học sinh): …

## 1. Môi trường [TỰ ĐỘNG + NGƯỜI VIẾT]

| Hạng mục | Giá trị |
|---|---|
| Vị trí camera (`camera_position`) | |
| Góc / độ cao (`camera_angle`) | |
| Độ phân giải (`resolution`) | |
| FPS mục tiêu / FPS thực đo (`fps_target` / `actual_fps`) | |
| Thiết bị (máy + camera) | |
| Ánh sáng (`lighting_condition`) | |
| Thời lượng | |
| Sơ đồ chỗ ngồi, khoảng cách camera – bàn cuối [NGƯỜI VIẾT] | |

## 2. Người tham gia [TỰ ĐỘNG + NGƯỜI VIẾT]

| Hạng mục | Giá trị |
|---|---|
| Học sinh có đồng ý phạm vi đánh giá | |
| Học sinh có nhãn thực tế | |
| Người lạ (kịch bản 09) | có / không |
| Số người quan sát; cách thống nhất khi hai người lệch nhau > 2 giây [NGƯỜI VIẾT] | |
| Thời gian có nhãn (giây-học sinh) | |

## 3. Độ phủ kịch bản [TỰ ĐỘNG]

| Kịch bản | Đã chạy | Có nhãn | Ghi chú [NGƯỜI VIẾT] |
|---|---|---|---|
| 01 Nghe giảng … 15 Lớp đông | | | |

## 4. Chỉ số theo tín hiệu [TỰ ĐỘNG]

Hai góc nhìn: *raw* (mọi tín hiệu AI) và *sau khi áp dụng hoạt động lớp*. PHONE, DROWSY, HEAD_AWAY, AWAY:
Precision, Recall, F1, TP, FP, FN, báo nhầm/giờ, độ trễ phát hiện, sai số thời lượng.

## 5. Ma trận nhầm lẫn [TỰ ĐỘNG]

Hàng = nhãn thực tế, cột = trạng thái AI, đơn vị giây (lấy mẫu mỗi giây).

## 6. Báo nhầm (false positives) [TỰ ĐỘNG + NGƯỜI VIẾT]

* Theo nhãn thực tế và theo hoạt động lớp: [TỰ ĐỘNG]
* 3–5 trường hợp tiêu biểu, nguyên nhân dự đoán (góc camera, ánh sáng, che khuất…) [NGƯỜI VIẾT]

## 7. Bỏ sót (false negatives) [TỰ ĐỘNG + NGƯỜI VIẾT]

## 8. Lỗi danh tính [TỰ ĐỘNG + NGƯỜI VIẾT]

Độ chính xác khi gán, số lần đổi danh tính, tỉ lệ UNKNOWN, khung hình trùng danh tính, người lạ bị nhận nhầm, tỉ lệ
phục hồi sau khi biến mất. Mô tả từng lần gán nhầm (kịch bản, thời điểm, track) [NGƯỜI VIẾT].

## 9. FPS và độ trễ [TỰ ĐỘNG]

`benchmark_kind` phải là `FULL_DETECTOR_CAMERA` (đo trên máy lớp học, gồm YOLOv8 + InsightFace + MediaPipe). Không
trộn với `RUNTIME_BENCHMARK` (`benchmark.json`, không chạy mô hình).

## 10. Giáo viên xem lại [TỰ ĐỘNG + NGƯỜI VIẾT]

Số sự kiện đã xem lại, tỉ lệ xác nhận đúng / báo nhầm / không chắc (chỉ khi ≥ 5 lượt xem lại). Nhận xét của giáo viên
về những sự kiện bị đánh dấu báo nhầm [NGƯỜI VIẾT].

## 11. Hiệu chỉnh [TỰ ĐỘNG + NGƯỜI VIẾT]

`current_config` → `recommended_config`, `delta`, độ tin cậy theo số sự kiện thực tế. Quyết định áp dụng hay không,
ai duyệt, ngày áp dụng [NGƯỜI VIẾT]. Công cụ **không** tự áp dụng.

## 12. Quyền riêng tư [TỰ ĐỘNG + NGƯỜI VIẾT]

* Báo cáo đã quét: `ARTIFACT_PRIVACY_CHECK: PASS` [TỰ ĐỘNG]
* Số phiếu đồng ý / thu hồi; Evidence Mode bật hay tắt; đã xóa dữ liệu thô `evaluation/private/<id>/` ngày … [NGƯỜI VIẾT]

## 13. Giới hạn [NGƯỜI VIẾT]

* Số buổi, số học sinh, một camera / một lớp; điều kiện ánh sáng đã thử và chưa thử.
* Độ lệch giữa người quan sát; các kịch bản chưa chạy.

## 14. Kết luận [NGƯỜI VIẾT]

Chỉ kết luận trong phạm vi cấu hình đã đo. Tín hiệu AI không phải kết luận sư phạm; giáo viên là người quyết định.

Người lập báo cáo: ……… Người duyệt: ……… Ngày: ………
