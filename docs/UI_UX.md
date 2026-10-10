# Giao diện và trải nghiệm người dùng

Tài liệu này mô tả giao diện sau đợt nâng cấp UI/UX: kết quả rà soát màn hình, hệ thống thiết kế,
cách hiển thị trạng thái, và những gì đã được kiểm chứng. Công nghệ không đổi: Flask + Jinja + CSS/JS thuần,
Bootstrap (chỉ dùng lưới, modal, offcanvas), Chart.js, Socket.IO. Không thêm thư viện mới.

Nguyên tắc xuyên suốt: **giao diện chỉ hiển thị điều máy chủ chứng minh được**. Trình duyệt không tự tính
trạng thái AI, điểm danh, điểm số hay quyền hạn.

## 1. Rà soát màn hình

Cột cuối ghi vấn đề chính của giao diện cũ đã được xử lý trong nhánh này.

| Màn hình | Vai trò | Thao tác chính | Đang tải | Trống | Lỗi | Mobile | Trợ năng | Vấn đề chính đã sửa |
|---|---|---|---|---|---|---|---|---|
| Đăng nhập `/login` | Tất cả | Đăng nhập | Nút hiện "Đang đăng nhập…", khóa gửi lặp | — | Thông báo chung, giữ tên đăng nhập, HTTP 401 | Một cột | Nhãn, lỗi gắn `aria-describedby`, hiện/ẩn mật khẩu | Trang in sẵn mật khẩu demo; gửi lặp được; đăng nhập sai vẫn trả HTTP 200 |
| Giám sát `/teacher/dashboard` | Giáo viên | Bắt đầu / kết thúc buổi học | Khung xương | "Chưa có buổi học", "Không có học sinh nào cần chú ý" | Thử lại; giữ dữ liệu cũ và báo mất kết nối | Thẻ xếp dọc, nút rộng toàn màn | Thẻ học sinh là nút, có `aria-label` đầy đủ | Không cho biết ai cần chú ý, vì sao, từ lúc nào; bắt đầu/kết thúc không có khóa chống bấm lặp; camera lỗi không có hướng dẫn |
| Học sinh `/teacher/students` | Giáo viên | Mở chi tiết học sinh | Khung xương | Lớp chưa có học sinh / không tìm thấy | Thử lại | Bảng thành thẻ | Hàng có thể chọn bằng bàn phím | Không có ô tìm kiếm và trang chi tiết học sinh thống nhất |
| Điểm danh `/teacher/attendance` | Giáo viên | Xem, tải CSV | Khung xương | "Chưa có buổi học nào để điểm danh" | Thử lại | Bảng thành thẻ | Trạng thái có chữ + biểu tượng | Không xem lại được buổi đã kết thúc |
| Cảnh báo `/teacher/alerts` | Giáo viên | Lọc nhật ký | Khung xương | "Chưa có buổi học đang diễn ra" | Thử lại | Danh sách | `aria-live` | Thông báo lỗi kỹ thuật thô có thể lọt vào nhật ký của giáo viên |
| Phân tích `/teacher/analytics` | Giáo viên | Đọc xu hướng | Khung xương | "Chưa đủ dữ liệu…" kèm điều kiện cần | Thử lại | Một cột | Biểu đồ có `aria-label`, số liệu có chữ | Thiếu dữ liệu chỉ ghi "N/A", không nói cần gì để có số liệu |
| Lịch sử buổi học `/teacher/reports` | Giáo viên | Xem chi tiết, in, CSV | Khung xương | "Chưa có buổi học nào đã kết thúc" | Thử lại | Bảng thành thẻ | Hàng chọn được bằng bàn phím | Không xem lại được tổng kết từng buổi học đã kết thúc |
| Cài đặt `/teacher/settings` | Giáo viên | Lưu tên, ngưỡng | Nút "Đang lưu…" | — | Lỗi cạnh trường | Một cột | Nhãn + trợ giúp | Không có kiểm tra tại chỗ, không đọc lại được ngưỡng đã lưu |
| Phiên học `/` | Học sinh | Bắt đầu / kết thúc phiên học | "Đang tải…" | "Chưa có phiên học" | Thử lại camera | Xếp dọc | Điểm có `aria-label` | Camera lỗi không có trạng thái và hướng dẫn riêng; bấm Bắt đầu lần hai làm khởi động lại phiên |
| Thống kê `/stats` | Học sinh | Đọc xu hướng | Khung xương | "Chưa đủ dữ liệu…" | Thử lại | Một cột | Ô theo giờ có chữ số | Thiếu dữ liệu chỉ ghi "N/A", không nói cần gì để có số liệu |
| Lịch sử `/reports` | Học sinh | Xem phiên, in, CSV | Khung xương | "Em chưa có phiên học nào" | Thử lại | Bảng thành thẻ | Hàng chọn được bằng bàn phím | Không có chi tiết hành vi của từng phiên và nút tải CSV thống nhất |
| Cài đặt `/settings` | Học sinh | Bật/tắt âm báo | — | — | Thử lại | Một cột | Công tắc có nhãn | Ngưỡng nhận diện hiển thị bằng thuật ngữ kỹ thuật (EAR) khó hiểu với học sinh |
| Tổng quan `/parent/dashboard` | Phụ huynh | Xem kết quả của con | — (kết xuất phía máy chủ) | "Tài khoản chưa được liên kết", "Chưa có…" | Trang lỗi chung | Responsive thật | Bảng có nhãn | Dùng khung điện thoại giả lập thay cho trang responsive thật |
| Tài khoản `/admin/accounts` | Quản trị | Thêm / sửa / xóa, đăng ký khuôn mặt | Khung xương | "Không có tài khoản nào khớp" | Lỗi hiện trong biểu mẫu, giữ nguyên dữ liệu đã nhập | Bảng thành thẻ | Nút biểu tượng có `aria-label` theo tên | Không tạo được tài khoản phụ huynh từ giao diện; xóa không nêu hậu quả; thao tác thất bại vẫn trả HTTP 200 |
| Lớp học `/admin/classes` | Quản trị | Thêm / đổi tên / xóa | Khung xương | "Chưa có lớp học nào" | Lỗi trong biểu mẫu | Bảng thành thẻ | Như trên | Xóa được lớp đang có buổi học |
| Đăng ký khuôn mặt (hộp thoại) | Quản trị | Chụp 3–5 ảnh, lưu | "Đang kiểm tra…" | — | Từng lỗi camera có hướng dẫn riêng | Một cột | Trạng thái `role=status` | Không có giải thích quyền riêng tư, không cần xác nhận đồng ý |
| Trang lỗi 403 / 404 / 405 / 413 / 500 | Tất cả | Về trang chính / thử lại | — | — | Thông điệp tiếng Việt, không lộ chi tiết kỹ thuật | Có | Tiêu đề rõ | Trang lỗi mặc định của máy chủ bằng tiếng Anh |

`/mobile` (khung điện thoại giả lập) đã bỏ; đường dẫn vẫn hoạt động và chuyển về trang chính.

## 2. Hệ thống thiết kế

Toàn bộ nằm trong `static/css/app.css` (token → thành phần → responsive). Không còn CSS viết trong template.

* **Chữ**: font hệ thống (hiển thị dấu tiếng Việt chuẩn trên mọi máy, không tải font). Thang cỡ: tiêu đề trang 24,
  tiêu đề mục 18, tiêu đề thẻ 15, nội dung 15, chú thích 13, số liệu 28.
* **Khoảng cách**: 4 · 8 · 12 · 16 · 24 · 32 · 48. **Bo góc**: 6 · 10 · 14 · 20. **Đổ bóng**: 2 mức.
* **Màu ngữ nghĩa**: chính, thành công, cảnh báo, nguy hiểm, thông tin, trung tính; có bộ token riêng cho nền tối.
* **Màu hành vi** (cố định ở mọi màn hình, luôn đi kèm biểu tượng và chữ, không dựa vào màu đơn thuần):

| Trạng thái | Màu | Biểu tượng |
|---|---|---|
| Tập trung | xanh lá | dấu tích |
| Dùng điện thoại | đỏ | điện thoại |
| Buồn ngủ | tím | trăng |
| Quay đi chỗ khác | hổ phách | mũi tên hai chiều |
| Rời chỗ | xanh lơ | người đi |
| Tạm khuất / Chưa nhận diện / Chưa có dữ liệu | xám | mắt gạch / dấu hỏi |
| Mất tín hiệu camera | xám đậm | camera gạch |

* **Thành phần dùng chung**: khung trang (thanh bên theo vai trò, thanh trên, trạng thái kết nối), thẻ, thẻ số liệu,
  huy hiệu trạng thái, bảng (tự thành thẻ dưới 992px), nút và tab phân đoạn, biểu mẫu, trạng thái trống/lỗi/đang tải,
  thông báo nổi, hộp xác nhận, khung camera, danh sách cần chú ý, thẻ học sinh.
* **JS**: `static/js/core.js` (gọi API, thông báo, hộp xác nhận, định dạng, từ vựng hành vi, kết nối realtime),
  `teacher.js`, `student.js`, `admin.js`. Mọi chuỗi do người dùng nhập đều qua `esc()` trước khi vào HTML.

## 3. Trạng thái hiển thị

**Buổi học**: bắt đầu và kết thúc là thao tác idempotent ở máy chủ (bấm đúp hay mở hai tab chỉ tạo một buổi);
đổi hình thức khi đang học bị từ chối (409); kết thúc luôn có hộp xác nhận nêu hậu quả.

**Camera** (`focusguard/camera_state.py`, suy ra ở máy chủ): Chưa bật · Chưa mở khung hình · Đang khởi động ·
Đang hoạt động · Mất tín hiệu · Không dùng được. Mỗi trạng thái có nhãn, hướng dẫn và nút thử lại; không có vòng quay
vô hạn (khởi động quá 30 giây sẽ đổi sang hướng dẫn xử lý). Khi camera lớp học chưa phân tích, các ô "Cần chú ý" và
"Trong khung hình" hiển thị "—" thay vì "0 / cả lớp đang ổn".

**Học sinh**: Ngoại tuyến · Chưa nhận diện / Chưa thấy · Tạm khuất · Rời chỗ · Tập trung · Dùng điện thoại ·
Buồn ngủ · Quay đi chỗ khác · Mất tín hiệu camera · Vắng mặt (sau khi buổi học kết thúc).

**Cần chú ý ngay**: mỗi dòng gồm tên, hành vi, thời gian kéo dài tính từ mốc bắt đầu do máy chủ ghi
(ví dụ "Dùng điện thoại · 14 giây"), gợi ý xử lý, và độ tin cậy khi có.

**Điểm tập trung**: không có dữ liệu thì hiển thị "Chưa có", không bao giờ là 0. Mọi nơi hiển thị điểm đều có câu
giải thích: điểm được tính từ các hành vi quan sát được theo thời gian và là chỉ số tham khảo.

**Kết nối realtime**: Đang kết nối · Trực tiếp · Mất kết nối, đang thử lại. Khi mất kết nối trang giữ dữ liệu cuối
cùng và tự đọc lại trạng thái từ máy chủ.

## 4. Responsive và trợ năng

* Đã xem ở 1440×900, 1280×800, 768×1024, 390×844: không cuộn ngang; thanh bên thành menu trượt dưới 992px.
* Vùng chạm tối thiểu 44px trên màn hình cảm ứng; bảng thành thẻ để nút thao tác không bị đẩy khỏi màn hình.
* Liên kết "Bỏ qua điều hướng", vòng focus rõ, `aria-current` cho mục đang mở, `aria-live` cho vùng cập nhật,
  nút chỉ có biểu tượng đều có `aria-label`, danh sách trực tiếp cập nhật tại chỗ nên không mất focus bàn phím.
* Tôn trọng `prefers-reduced-motion`; có giao diện tối; in được tổng kết buổi học và lịch sử.

## 5. Kiểm chứng

* `tests/test_ui_contract.py` (41 test) và các test UI trong `tests/test_review_regressions.py`: mọi trang của từng
  vai trò mở được, không có liên kết chết, sai vai trò bị chuyển hướng, phiên hết hạn có thông báo, đăng nhập sai
  không lộ tài khoản có tồn tại hay không, bắt đầu/kết thúc idempotent, trạng thái camera, lỗi không lộ chi tiết nội bộ.
* Đã đi thử bằng trình duyệt cả bốn vai trò trên máy chủ cục bộ (đăng nhập, bắt đầu/kết thúc buổi học, chi tiết học sinh,
  lịch sử, phân tích, thêm/xóa tài khoản, hộp đăng ký khuôn mặt, trang 404, truy cập sai vai trò).
* Các trạng thái trực tiếp trên trang Giám sát được xem bằng cách đưa quan sát theo kịch bản vào đúng
  `SessionRuntime` thật (công cụ QA ngoài repo); luồng camera thật trên giao diện mới **chưa** được chạy lại vì
  phiên làm việc này không được macOS cấp quyền camera. Trạng thái "Không dùng được camera" đã được thấy trực tiếp.

## 6. Hạn chế đã biết

* Camera chỉ phân tích khi trang Giám sát (giáo viên) hoặc trang Phiên học (học sinh) đang mở; các trang khác có
  dải thông báo nhắc điều này. Đây là giới hạn kiến trúc hiện tại (luồng MJPEG gắn với người xem).
* Ngưỡng "điểm cần chú ý" của giáo viên lưu trong bộ nhớ máy chủ, mất khi khởi động lại.
* Chưa có đặt lại mật khẩu tự phục vụ; trang đăng nhập hướng dẫn liên hệ quản trị viên.
* Chưa kiểm thử với trình đọc màn hình thật và chưa đo tương phản bằng công cụ tự động.
* Chưa chạy lại kiểm chứng camera thật (docs/REAL_CAMERA_RESULTS.md) trên giao diện mới.
