# Đồng ý, minh chứng, lưu trữ và xóa dữ liệu

Vì FocusGuard dùng vector đặc trưng khuôn mặt (dữ liệu sinh trắc học), mọi xử lý khuôn mặt cần sự đồng ý của
học sinh/phụ huynh, có thời hạn lưu, có thể thu hồi, xóa, và mọi truy cập nhạy cảm đều có nhật ký. Toàn bộ do máy chủ
quyết định; trình duyệt chỉ hiển thị và gửi yêu cầu.

Câu chữ chính sách chỉ viết ở **một nơi**: `focusguard/privacy/policy.py` (phiên bản `privacy-2026.10-1`). Các trang
(thanh bên, camera, đăng nhập, phụ huynh, trang Quyền riêng tư) nhận chuỗi này từ máy chủ.

## 1. Đồng ý (consent)

Bảng `consents`: `consent_id, student_id, guardian_name, guardian_relationship, student_is_adult,
scope_face_recognition, scope_event_evidence, scope_evaluation, policy_version, accepted_at, expires_at, revoked_at,
revoked_by, revoke_reason, created_by, created_at`.

| Trạng thái | Khi nào |
|---|---|
| `NOT_PROVIDED` | chưa có bản ghi |
| `ACTIVE` | bản ghi mới nhất đã chấp nhận, chưa hết hạn, chưa thu hồi |
| `EXPIRED` | quá `expires_at` (mặc định `CONSENT_VALIDITY_DAYS` = 365 ngày) |
| `REVOKED` | đã thu hồi |

* Học sinh THCS chưa đủ tuổi: bắt buộc họ tên và quan hệ của phụ huynh/người giám hộ.
* Ai ghi nhận: quản trị viên (sau khi nhận bản đồng ý giấy/điện tử). `created_by` lấy từ phiên đăng nhập.
* **Đăng ký khuôn mặt** (`/api/admin/register_face`) bị từ chối (403) nếu không `ACTIVE` với phạm vi nhận diện khuôn
  mặt. Cờ `consent: true` gửi từ trình duyệt bị bỏ qua; hộp đăng ký đọc trạng thái từ máy chủ.
* **Nhận diện**: camera lớp học chỉ nạp vector của học sinh có đồng ý `ACTIVE` (`SessionManager.recognition_gallery`).
  Vector cũ chưa có đồng ý không được dùng, được đánh dấu trên trang Quyền riêng tư để quản trị viên xử lý.

## 2. Thu hồi

Quản trị viên hoặc phụ huynh của chính học sinh đó bấm **Thu hồi đồng ý** (có hộp xác nhận). Máy chủ:

1. ghi `revoked_at`, `revoked_by`;
2. dừng nhận diện ngay (nạp lại danh sách khuôn mặt của camera đang chạy);
3. xóa vector khuôn mặt — **mọi mẫu** trong `face_profiles` (`FACE_DELETED`); camera đang chạy hủy đợt thu mẫu
   (enrollment burst) và các mẫu `PENDING` của học sinh đó (`IDENTITY_PROFILE_SAMPLE_REJECTED`, lý do
   `consent_not_active`);
4. xóa đoạn minh chứng của học sinh nếu `REVOKE_DELETES_EVIDENCE=true` (mặc định);
5. ghi `CONSENT_REVOKED`.

Ngoài ra máy chủ hủy mọi lần ghi minh chứng đang chờ của học sinh đó (không có đoạn nào được ghi sau khi thu hồi).

Dữ liệu học tập (sự kiện, điểm, điểm danh) **không** bị xóa khi thu hồi; chúng tuân theo thời hạn lưu ở mục 4.

**Đồng ý mới thay đồng ý cũ.** Ghi nhận một đồng ý mới không có phạm vi *Nhận diện khuôn mặt* → vector bị xóa ngay
(`FACE_DELETED`, lý do `consent_scope_removed`); không có phạm vi *Đoạn minh chứng* → minh chứng bị xóa (theo
`REVOKE_DELETES_EVIDENCE`). Ngày hết hạn ghi tay tối đa 10 năm. **Xóa tài khoản học sinh** hoặc **đổi vai trò** khỏi
học sinh → vector và minh chứng bị xóa (có nhật ký); bản ghi đồng ý và nhật ký được giữ làm hồ sơ.

## 3. Evidence Mode (đoạn minh chứng)

**Mặc định FocusGuard không lưu video. Evidence Mode là tùy chọn, chỉ lưu đoạn minh chứng ngắn quanh sự kiện khi
được phép, giới hạn quyền truy cập và tự động xóa theo retention policy.**

```
EVENT_EVIDENCE_ENABLED=false
EVENT_EVIDENCE_PRE_SECONDS=3
EVENT_EVIDENCE_POST_SECONDS=5
EVENT_EVIDENCE_RETENTION_HOURS=24
EVENT_EVIDENCE_FPS=2
EVENT_EVIDENCE_DIR=instance/evidence
```

* **Tắt** (mặc định): chỉ lưu thông tin sự kiện; camera không đưa khung hình nào vào bộ đệm.
* **Bật**: khung hình JPEG đã mã hóa nằm trong bộ đệm vòng **trong RAM** (giới hạn số giây và số khung). Khi một sự kiện
  bắt đầu với học sinh có đồng ý phạm vi *Đoạn minh chứng*, máy chủ lưu **một** đoạn từ 3 giây trước lúc bắt đầu tới
  5 giây sau lúc xác nhận. **Chỉ khung hình đã cắt theo khung người của đúng học sinh đó** được lưu, lấy từ ảnh camera
  **trước khi** vẽ tên/trạng thái/điểm lên hình; khung không có khung người của học sinh (ví dụ khi rời chỗ) bị bỏ, nên
  không bao giờ lưu cả lớp. Đồng ý được kiểm tra lại lúc lưu. Không ghi video liên tục. Phiên tự học cá nhân không bao
  giờ lưu minh chứng.
* Lưu trữ: tên tệp UUID ngẫu nhiên, thư mục phía máy chủ ngoài `static/` (git-ignore), không có URL công khai; tên tệp
  được kiểm tra định dạng và đường dẫn phải nằm trong thư mục (chống path traversal).
* Truy cập: chỉ **giáo viên của lớp** xem qua `/api/teacher/evidence/<id>` (header `no-store`), mỗi lần xem ghi
  `EVIDENCE_VIEWED`. Học sinh khác, giáo viên lớp khác, phụ huynh không xem được; quyền xem của phụ huynh mặc định
  **tắt** và không có đường dẫn nào cho phụ huynh. Quản trị viên xóa được nhưng không xem.
* Xóa: nút **Xóa ngay** (giáo viên) hoặc trang Quyền riêng tư (quản trị viên), ghi `EVIDENCE_DELETED`; hết hạn thì
  máy chủ tự xóa (đoạn đã hết hạn không bao giờ được phát nhưng vẫn xóa tay được).
* Tệp thiếu, bị cắt cụt hoặc hỏng: máy chủ trả 410, gỡ bản ghi và ghi `EVIDENCE_DELETED` (lý do `file_unreadable`);
  không bao giờ trả một phần khung hình.

## 4. Thời hạn lưu trữ

| Biến | Mặc định | Dữ liệu |
|---|---|---|
| `FACE_EMBEDDING_RETENTION_DAYS` | 365 | vector khuôn mặt: mỗi mẫu trong `face_profiles` tính từ lúc tạo mẫu |
| `EVENT_METADATA_RETENTION_DAYS` | 365 | sự kiện AI, ảnh chụp điểm (snapshot), xác nhận của giáo viên |
| `EVENT_EVIDENCE_RETENTION_HOURS` | 24 | đoạn minh chứng |
| `EVALUATION_DATA_RETENTION_DAYS` | 90 | buổi đánh giá, nhãn thực tế, nhật ký số liệu thô |
| `RETENTION_SWEEP_MINUTES` | 10 | chu kỳ dọn dữ liệu của máy chủ |

Máy chủ chạy `RetentionService` ngay sau khi khởi động và sau đó mỗi `RETENTION_SWEEP_MINUTES` (đồng ý hết hạn có hiệu
lực với nhận diện chậm nhất sau một chu kỳ); có thể chạy tay bằng nút **Dọn dữ liệu hết hạn ngay** (nhật ký ghi người
bấm) hoặc `python scripts/privacy_retention.py` (cron). Vector khuôn mặt bị xóa khi đồng ý `EXPIRED`/`REVOKED` hoặc khi
đồng ý `ACTIVE` không có phạm vi khuôn mặt. Xác nhận của giáo viên mà sự kiện gốc đã bị xóa, và các đoạn hoạt động lớp
cũ cũng bị xóa. Nhật ký kiểm toán và bản ghi đồng ý được giữ lại (không chứa dữ liệu sinh trắc học).

Không tự xóa: vector cũ **không có bản ghi đồng ý** nào (đăng ký trước khi có tính năng đồng ý) — không dùng để nhận
diện, được đánh dấu trên trang Quyền riêng tư để quản trị viên xóa; tóm tắt buổi học (điểm, điểm danh — không có dữ
liệu sinh trắc) — xóa cùng tài khoản hoặc theo yêu cầu.

### Mẫu nhận diện nhiều vector và gắn tên của giáo viên

* Hồ sơ khuôn mặt là nhiều mẫu (`face_profiles`, tối đa 10 mẫu hoạt động, không lưu ảnh) — xem
  [CLASSROOM_IDENTITY.md](CLASSROOM_IDENTITY.md). Vector cũ `users.face_embedding` được chuyển thành một mẫu `LEGACY`.
* **Giáo viên gắn tên ≠ đồng ý sinh trắc.** Không có đồng ý `ACTIVE` với phạm vi nhận diện khuôn mặt → tên chỉ dùng
  trong buổi học (ràng buộc track ↔ học sinh), **không** tạo hay lưu vector nào; giao diện ghi “Chỉ trong buổi
  (không có đồng ý nhận diện)”.
* Có đồng ý: đợt thu mẫu ~6 s sau khi gắn tên và học dần (tối đa 2 mẫu / buổi, chỉ lưu khi kết thúc buổi) — mỗi mẫu
  ghi `source`, `class_session_id`, `binding_id` để có thể xóa đúng mẫu khi giáo viên sửa tên.
* Ràng buộc danh tính (`identity_bindings`) không chứa vector; theo hạn lưu sự kiện (`EVENT_METADATA_RETENTION_DAYS`).
  Ràng buộc thuộc về buổi học: tên giáo viên gắn được giữ khi camera kết nối lại và chỉ được nối tiếp **theo vị trí**
  người trong khung hình — không cần và không dùng dữ liệu sinh trắc của học sinh không có đồng ý.
* Sổ bằng chứng điểm danh (`attendance_evidence`: track, ràng buộc, thời điểm; không có ảnh / vector) theo hạn lưu sự
  kiện; bằng chứng bị thu hồi khi giáo viên sửa tên được giữ lại với lý do (không ghi đè lịch sử).

## 5. Nhật ký kiểm toán

Bảng `audit_log`: `actor_id, actor_role, student_id, action, timestamp, metadata`. Hành động:
`CONSENT_GRANTED, CONSENT_REVOKED, FACE_ENROLLED, FACE_DELETED, EVIDENCE_CREATED, EVIDENCE_VIEWED, EVIDENCE_DELETED,
POLICY_CHANGED, EVENT_REVIEWED, DATA_EXPORTED, RETENTION_RUN, ACTIVITY_CONTEXT_CHANGED`.
`metadata` được làm sạch: bỏ mọi khóa chứa embedding/vector/image/frame/password/secret/token (trừ số đếm nguyên như
`frame_count`), bỏ dãy số dài, thay chuỗi trông như dữ liệu mã hoá (data: URI, base64 dài) bằng `<encoded data removed>`,
cắt chuỗi dài. `POLICY_CHANGED` được ghi khi quản trị viên đổi ngưỡng và một lần khi phiên bản chính sách đồng ý đổi.
Chỉ quản trị viên xem nhật ký (bộ lọc liệt kê mọi hành động).

## 6. Trang "Dữ liệu & quyền riêng tư" (quản trị viên)

Mỗi học sinh: trạng thái đồng ý, phạm vi, **phiên bản chính sách** của bản đồng ý (cảnh báo nếu chính sách đã đổi), có
vector khuôn mặt hay không (và lần đăng ký, ngày tự xóa), số đoạn minh chứng, ngày hết hạn đồng ý. Thao tác: **Ghi nhận đồng ý**, **Thu hồi đồng ý**, **Xóa face embedding**,
**Xóa evidence**, **Xuất JSON/CSV**. Thao tác xóa luôn có hộp xác nhận nêu hậu quả.

## 7. Xuất dữ liệu

`GET /api/export/student/<id>?format=json|csv&section=events|reviews|consents|annotations`: sự kiện của học sinh
(kèm trạng thái xem lại), xác nhận của giáo viên, bản ghi đồng ý, nhãn đánh giá. **Không bao giờ** xuất vector khuôn
mặt (chỉ `face_embedding_exists`), nội dung đoạn minh chứng hay mật khẩu. Quyền: quản trị viên, giáo viên của lớp,
phụ huynh được liên kết (không kèm ghi chú của giáo viên, ghi chú nội bộ của nhà trường hay mã nhân viên). Học sinh
không xuất được. Mỗi lần xuất ghi `DATA_EXPORTED`. CSV vô hiệu hoá công thức bảng tính (giá trị bắt đầu bằng `=`, `+`,
`-`, `@` được thêm dấu `'`).

## 8. Bảo vệ vector khuôn mặt

Không trả qua API người dùng chung, không gửi tới trình duyệt, không ghi log (`scripts/security_check.py` chặn
`print/log(...embedding)` trong mã chạy production), không xuất file; chỉ repository và thao tác quản trị có quyền.

Kiểm thử: `tests/test_consent_privacy.py`, `tests/test_evidence.py`, `tests/test_security_btc.py`,
`tests/test_privacy_hardening.py`, `tests/repo_contract.py` (SQLite + Firestore emulator).
