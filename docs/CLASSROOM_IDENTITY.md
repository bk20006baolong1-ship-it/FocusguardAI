# Danh tính học sinh trên camera lớp học: gắn tên, mẫu nhận diện, sửa tên

Mã: `focusguard/classroom_identity.py` (điều phối), `focusguard/identity.py` (IdentityManager),
`focusguard/face_profiles.py` (mẫu nhiều vector), `focusguard/identity_routes.py` (API), `static/js/classroom_identity.js`.
Mọi ngưỡng là giá trị mặc định **chưa hiệu chỉnh trên dữ liệu lớp thật** (`UNCALIBRATED_DEFAULT`).

## 1. Nguyên tắc

* Danh tính gắn với **người (person track)**, không chỉ một khung mặt: khi học sinh cúi xuống hay bị che mặt, track
  vẫn giữ tên.
* **Trạng thái** (UNKNOWN / CANDIDATE / CONFIRMED / UNCERTAIN / CONFLICT, cờ `review_required`) tách khỏi **nguồn**:
  `AUTO_FACE`, `AUTO_RECOVERED`, `TEACHER_TAG`, `TEACHER_CORRECTED`.
* Giáo viên là người quyết định cuối cùng trong buổi học. Bằng chứng khuôn mặt **không bao giờ** tự đổi tên giáo viên
  đã gắn; mâu thuẫn kéo dài (≥ 6 lần nhận, ≥ 2 s) chỉ bật **“Cần xem lại”**.
* Một học sinh — một người; một người — một học sinh (máy chủ chặn trùng, 409).
* Không đoán: người chưa xác định được **đếm** (“Chưa xác định”), không bị gán thành một học sinh cụ thể. Học sinh
  vừa mất danh tính không bị tính “rời chỗ” khi một người chưa xác định **có thể là em đó** (đang ở đúng track đó hoặc
  xuất hiện sau khi mất); người có mặt từ trước (ví dụ giáo viên) không che cho học sinh đã ra khỏi lớp.

## 2. Khởi tạo danh sách (CLASSROOM_INITIALIZATION)

Bắt đầu buổi học với “Khởi tạo danh sách bằng camera” → pha `INITIALIZING`: phát hiện, theo dõi, gắn tên, tạo mẫu;
**không** chấm điểm, **không** sự kiện hành vi, **không** chốt vắng mặt; chỉ ghi nhận có mặt khi học sinh được xác
nhận thật. “Hoàn tất danh sách & bắt đầu” → `MONITORING`. Có thể vào lại pha khởi tạo giữa giờ (chấm điểm tạm dừng,
không trừ điểm ai).

Giao diện: khung bấm được trên hình camera (màu **và** chữ: “Giáo viên gắn”, “AI: …”, “Gợi ý: …”, “Chưa xác định”,
“cần xem lại”), danh sách lớp (người đã gắn, chưa gán, trạng thái mẫu, điểm danh), xung đột, cảnh báo camera quá xa.

## 3. Gắn tên (teacher tag)

`POST /api/teacher/classroom/tag {class_id, track_id, student_id}` — chỉ giáo viên **được phân công** lớp đó.
`confirmed_by`, `confirmed_at`, `identity_source` do máy chủ quyết định (giá trị trong request bị bỏ qua). Ràng buộc
được ghi vào `identity_bindings` (lịch sử: sửa tên kết thúc ràng buộc cũ và tạo ràng buộc mới, sự kiện AI thô cũ
**không** bị viết lại). Gắn tên ≠ đồng ý sinh trắc: không có đồng ý nhận diện khuôn mặt → chỉ dùng trong buổi, không
tạo vector.

## 4. Mẫu nhận diện nhiều vector (multi-template profile)

* Bảng `face_profiles`: `face_profile_id, student_id, embedding, quality_score, pose_bucket, source, active,
  class_session_id, binding_id, created_at, updated_at, last_used_at`. **Không lưu ảnh**.
* Tối thiểu hữu ích 3 mẫu (`PARTIAL`), mục tiêu 5 mẫu trên ≥ 2 hướng mặt (`READY`), tối đa 10 mẫu hoạt động (bỏ mẫu
  chất lượng thấp nhất ở hướng đông nhất). Trạng thái: `NOT_ENROLLED`, `NEEDS_MORE_SAMPLES`, `PARTIAL`, `READY`,
  `NEEDS_REVIEW` (có mẫu nhưng đồng ý không còn hiệu lực). Giao diện: “Mẫu nhận diện: 3/5 · Đủ dùng, nên thêm mẫu”.
* **Chuyển đổi**: `users.face_embedding` (bản cũ, một vector trung bình) được chuyển thành một mẫu `LEGACY` khi khởi
  động (SQLite và Firestore, chạy lại không nhân đôi, giữ thời điểm đăng ký); không mất dữ liệu.
* Đăng ký bằng ảnh của quản trị viên: mỗi ảnh dùng được là một mẫu (không còn lấy trung bình).

## 5. Enrollment burst (sau khi gắn tên, nếu có đồng ý)

Theo dõi track vừa gắn ~6 s (không chặn giáo viên gắn người khác), thu tối đa 24 ứng viên. Mỗi ứng viên phải đạt:
kích thước khuôn mặt ≥ 64 px, độ tin cậy phát hiện ≥ 0,6, độ nét (phương sai Laplacian), độ sáng 45–215, tương phản,
góc mặt |yaw| ≤ 35°, |pitch| ≤ 25°, không bị che (điện thoại), khuôn mặt thuộc đúng track, không trùng gần như y hệt
(cos ≥ 0,97). Sau đó: loại mẫu lệch khỏi đồng thuận của chính đợt thu (khuôn mặt người khác lọt vào), nếu học sinh đã
có hồ sơ thì loại mẫu mâu thuẫn với hồ sơ; chọn tối đa 6 mẫu **đa dạng** (ưu tiên hướng mặt mới, ít giống mẫu đã có,
rồi chất lượng). Ảnh khung hình chỉ nằm trong bộ nhớ trong khung đó.

## 6. So khớp nhiều mẫu

Điểm của một học sinh = mẫu khớp nhất; nếu có ≥ 2 mẫu, mẫu nhì phải cách không quá `template_agreement` (một mẫu lạ
không tự mang được kết quả). Vẫn giữ: ngưỡng tương đồng, khoảng cách giữa **học sinh** (margin), xác nhận theo thời
gian, một học sinh không thể có hai track.

## 7. Học dần qua các buổi (progressive enrollment)

Chỉ khi **tất cả** đúng: đồng ý nhận diện còn hiệu lực; danh tính CONFIRMED; nguồn giáo viên, hoặc AUTO với độ giống
≥ 0,55 và margin ≥ 2× mức tối thiểu; track ổn định ≥ 5 s; không xung đột / cần xem lại; chất lượng ≥ 0,70; giống hồ sơ
của chính học sinh nhưng đủ khác (≤ 0,90); tối đa 2 mẫu mỗi buổi. Mẫu ở trạng thái `PENDING` trong bộ nhớ, chỉ được
lưu (`CONFIRMED`) khi **giáo viên kết thúc buổi học**; nếu luồng camera đóng giữa giờ (tải lại trang, rời trang) các
mẫu đang chờ bị bỏ (`pipeline_closed`), vì sau đó không còn sửa tên được trên chúng. **Không bao giờ** học từ UNKNOWN /
CANDIDATE / UNCERTAIN / CONFLICT. Đồng ý bị thu hồi trong lúc đang ghi → mẫu vừa ghi bị xóa lại.

## 8. Sửa tên (re-tag / correction)

Bấm vào người → “Đổi học sinh”: ràng buộc trong buổi đổi ngay (`TEACHER_CORRECTED`), ràng buộc sai kết thúc; mẫu
`PENDING` thu theo danh tính sai bị `REJECTED`; mẫu burst tạo từ **mọi** ràng buộc của học sinh sai trên track đó bị
xóa (kể cả khi đã gắn lại cùng tên nhiều lần); học sinh sai không bao giờ được tự gắn lại vào track đó; điểm danh chỉ
dựa trên ràng buộc sai được thu hồi (sổ bằng chứng điểm danh, [ATTENDANCE.md](ATTENDANCE.md)) — check-in đúng
trước đó được giữ nguyên; nếu học sinh đúng có đồng ý, bắt đầu thu mẫu mới. Thao tác gắn / sửa tên đến giữa hai bước xử lý một khung
hình được áp dụng ngay cho khung hình đó (không dùng quyết định cũ). Sau khi sửa, bằng chứng khuôn mặt chỉ về học sinh giáo viên đã loại không bị tính là mâu thuẫn.

## 9. Ràng buộc thuộc về buổi học (camera kết nối lại, tải lại trang)

Mã: `focusguard/session_identity.py` (`SessionIdentityStore`, gắn với SessionRuntime của buổi học).

* Ràng buộc danh tính thuộc về **buổi học**, không thuộc về luồng camera. Luồng camera / tracker có thể khởi động lại
  bất kỳ lúc nào (mất kết nối, tải lại trang, đóng xem thử, dựng lại pipeline) — mã track mới bắt đầu lại.
* Ràng buộc do giáo viên gắn (và những gì khôi phục từ đó) khi mất track được **tách khỏi track** (`detached_at`),
  bản ghi `identity_bindings` vẫn mở trong suốt buổi; kết thúc buổi học → mọi ràng buộc kết thúc (`session_closed`).
* **Không bao giờ** dùng lại mã track cũ cho người mới. Ràng buộc chỉ được nối tiếp sang một track mới khi có bằng
  chứng liên tục **an toàn** (`_restore`, mặc định chưa hiệu chỉnh): người mới xuất hiện đúng chỗ học sinh ngồi
  (IoU ≥ 0,5), là **người duy nhất** ở chỗ đó, không khớp chỗ của học sinh khác, không có khuôn mặt giống học sinh nào
  khác (khi lớp có học sinh đăng ký khuôn mặt: khuôn mặt phải **được thấy** và không khớp ai — mặt bị che thì chờ),
  xuất hiện sau khi mất dấu, ổn định ≥ 6 khung và ≥ 1,5 s, trong vòng 120 s **kể từ khi camera có hình lại** (thời
  gian không có hình không phải bằng chứng). Không đủ điều kiện → học sinh
  “chờ thấy lại” (vẫn giữ tên giáo viên gắn), giáo viên gắn lại. Nguồn: `AUTO_RECOVERED` + “khôi phục theo vị trí”;
  bản ghi mới có `derived_from_binding_id` + `recovery_method = SEAT_CONTINUITY`; ghi `IDENTITY_SESSION_RESTORED`.
* Không cần đồng ý sinh trắc: chỉ dùng **vị trí** người trong khung hình — không ảnh, không vector cho học sinh không
  có đồng ý. Học sinh có mẫu nhận diện được nhận lại bằng khuôn mặt như trước.
* Liên kết “cùng một người” (`derived_from_binding_id`, lưu để truy vết): cùng track (`SAME_TRACK`), tracker đổi mã
  tại đúng chỗ cũ (`TRACK_CONTINUITY`), khôi phục theo vị trí (`SEAT_CONTINUITY`). Khi giáo viên nói một ràng buộc là
  sai, chỉ thu hồi những gì **chứng minh được** là phụ thuộc: chính ràng buộc đó và các ràng buộc trước đó **trên
  cùng track** (cùng mã tracker = cùng một người). Liên kết sang track khác chỉ là suy luận (vị trí / thời gian); ràng
  buộc trước đó có xác nhận riêng (khuôn mặt hoặc giáo viên) nên không bị thu hồi — nếu nó cũng sai, giáo viên sửa
  điểm danh trực tiếp.
* Che chắn “rời chỗ” khi camera dựng lại: chỉ áp dụng cho học sinh còn được theo dõi lúc luồng cũ dừng, và tối đa
  120 s; học sinh đã ra khỏi lớp trước đó không được che.
* Giới hạn: hai học sinh đổi chỗ đúng trong lúc camera mất hình có thể được khôi phục nhầm theo vị trí; giao diện ghi
  rõ “khôi phục theo vị trí”, giáo viên sửa (sửa chỉ thu hồi bằng chứng của lần khôi phục đó).

## 10. Nhật ký (audit)

`IDENTITY_TEACHER_TAGGED`, `IDENTITY_TEACHER_CORRECTED`, `IDENTITY_TAG_REMOVED`, `IDENTITY_AUTO_CONFIRMED` (một lần
mỗi học sinh mỗi buổi), `IDENTITY_PROFILE_SAMPLE_ADDED`, `IDENTITY_PROFILE_SAMPLE_REJECTED` (số lượng + lý do),
`FACE_PROFILE_READY`, `ATTENDANCE_TEACHER_CORRECTED`, `IDENTITY_SESSION_RESTORED`. Không bao giờ có ảnh, vector,
mật khẩu trong metadata.

## 11. Giới hạn (trung thực)

* Chưa đo độ chính xác nhận diện nhiều góc trên lớp thật (`REAL_CLASSROOM_EVIDENCE_PENDING`); các ngưỡng chưa hiệu
  chỉnh. Luồng trình duyệt dùng **lớp học mô phỏng** (`focusguard/classroom_sim.py`, SYNTHETIC).
* Hồ sơ đăng ký sai (mẫu của người khác) cho đề xuất sai với độ tin cậy cao; “xác nhận hàng loạt” không phát hiện
  được — giáo viên sửa, và mỗi lần sửa loại mẫu đang thu theo danh tính sai. Quản trị viên có thể xóa và đăng ký lại.
* Khôi phục theo vị trí sau khi camera mất hình chỉ an toàn khi học sinh ngồi yên chỗ; các ngưỡng (IoU, thời gian)
  chưa hiệu chỉnh trên lớp thật. Không chắc chắn → không khôi phục, giáo viên gắn lại.
