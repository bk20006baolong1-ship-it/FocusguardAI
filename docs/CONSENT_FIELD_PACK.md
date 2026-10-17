# Bộ mẫu đồng ý cho buổi đánh giá trên lớp học thật

> Đây là **mẫu văn bản tham khảo** do đội dự án soạn để mô tả đúng những gì FocusGuard làm. Đây **không phải tư vấn
> pháp lý**. Nhà trường cần rà soát theo quy định hiện hành (ví dụ quy định về bảo vệ dữ liệu cá nhân) và quy chế của
> trường trước khi dùng.

Các mẫu dưới đây khớp với cách hệ thống lưu và kiểm tra đồng ý: ba phạm vi riêng biệt, có thời hạn, thu hồi được bất cứ
lúc nào, máy chủ kiểm tra (không dựa vào giao diện). Thông tin chi tiết: [CONSENT_AND_RETENTION.md](CONSENT_AND_RETENTION.md).

---

## 1. Thư thông báo và phiếu đồng ý của phụ huynh / người giám hộ

**Kính gửi Quý phụ huynh,**

Đội dự án FocusGuard (cuộc thi AI Tài Năng Việt 2026) đề nghị cho con/em tham gia **một buổi thử nghiệm có giám sát**
tại lớp ……… ngày ……… để kiểm tra độ chính xác của hệ thống.

**Mục đích.** Đo xem hệ thống có ghi nhận đúng các tín hiệu quan sát được (ví dụ dùng điện thoại, nhắm mắt lâu, quay
đi chỗ khác, rời chỗ) hay không, và giảm báo nhầm. Kết quả dùng để **cải thiện hệ thống**, **không** dùng để đánh giá,
xếp loại hay kỷ luật học sinh. AI không đọc suy nghĩ, không đo cảm xúc, không quyết định học sinh có vi phạm hay không.

**Dữ liệu thu thập** (chọn phạm vi ở cuối phiếu):

| Phạm vi | Dữ liệu | Thời hạn lưu | Ai được xem |
|---|---|---|---|
| Nhận diện khuôn mặt | Một **vector số** đặc trưng khuôn mặt (không lưu ảnh) để camera nhận ra học sinh | Tối đa 365 ngày, xóa ngay khi thu hồi | Không ai xem trực tiếp; chỉ máy chủ dùng để nhận diện |
| Tham gia đánh giá thử nghiệm | Số liệu theo thời gian (góc đầu, độ mở mắt, tín hiệu AI, nhãn của người quan sát); **không ảnh, không video** | 90 ngày | Đội dự án, giáo viên của lớp |
| Đoạn minh chứng (tùy chọn) | Vài giây hình ảnh **chỉ cắt quanh con/em**, quanh một sự kiện AI ghi nhận | 24 giờ rồi tự xóa | Chỉ giáo viên của lớp; phụ huynh không xem được qua hệ thống |

**Quyền của phụ huynh và học sinh.** Không đồng ý không ảnh hưởng tới việc học. Có thể **thu hồi bất cứ lúc nào** (báo
giáo viên/nhà trường hoặc bấm *Thu hồi đồng ý* trên trang phụ huynh): hệ thống ngừng nhận diện ngay, xóa vector khuôn
mặt và các đoạn minh chứng, ghi nhật ký. Có thể yêu cầu **xuất** hoặc **xóa** dữ liệu của con/em.

**Báo cáo công khai** (hồ sơ thi) chỉ có số liệu tổng hợp và mã số; **không** có tên, ảnh, video hay dữ liệu khuôn mặt.

---

**PHIẾU ĐỒNG Ý**

Họ tên học sinh: ……………………………… Lớp: ……… 

Họ tên phụ huynh/người giám hộ: ……………………………… Quan hệ với học sinh: ………………

Tôi đã đọc thông tin trên và đồng ý cho con/em tham gia với các phạm vi sau (đánh dấu ✓):

- [ ] Nhận diện khuôn mặt
- [ ] Tham gia đánh giá thử nghiệm (không ảnh, không video)
- [ ] Đoạn minh chứng ngắn (tùy chọn; chỉ khi nhà trường bật chế độ này)

Thời hạn đồng ý: đến ngày ……… (để trống: 1 năm). Tôi biết có thể thu hồi bất cứ lúc nào.

Ngày ……… Chữ ký phụ huynh/người giám hộ: ………………

---

## 2. Lời đồng ý của học sinh (assent) — đọc cho học sinh trước buổi

> "Hôm nay lớp mình thử một hệ thống camera. Camera chỉ ghi nhận những việc nhìn thấy được, như cầm điện thoại hay
> quay đi chỗ khác. Nó **không** biết em nghĩ gì và **không** chấm điểm hạnh kiểm. Có hai người quan sát ghi lại để so
> sánh với máy. Em có thể **không tham gia**, hoặc **dừng bất cứ lúc nào** mà không bị ảnh hưởng gì. Nếu em đồng ý, hãy
> ký tên dưới đây."

| Họ tên học sinh | Đồng ý tham gia (✓) | Chữ ký |
|---|---|---|
| | | |

---

## 3. Phiếu thu hồi đồng ý

Họ tên học sinh: ……………… Lớp: ……… — Tôi thu hồi đồng ý đã ký ngày ………

Phạm vi thu hồi: [ ] tất cả [ ] nhận diện khuôn mặt [ ] đánh giá thử nghiệm [ ] đoạn minh chứng

Ngày ……… Chữ ký: ………………

*Nhà trường ghi nhận trên **Quản trị › Dữ liệu & quyền riêng tư › Thu hồi đồng ý** (hoặc ghi nhận đồng ý mới chỉ với
các phạm vi còn giữ: dữ liệu của phạm vi bị bỏ được xóa ngay).*

---

## 4. Ghi chú cho người tổ chức

* Ghi nhận trên hệ thống **đúng** các phạm vi phụ huynh đã đánh dấu; máy chủ sẽ chặn mọi việc ngoài phạm vi.
* Học sinh không đồng ý vẫn học bình thường; ngồi ngoài khung hình camera nếu có thể. Hệ thống không gán nhãn và không
  đưa học sinh chưa đồng ý vào gói dữ liệu đánh giá.
* Người lạ ở kịch bản 09 phải là người **đã đồng ý xuất hiện** trong khung hình (không đăng ký khuôn mặt).
* Lưu phiếu giấy theo quy định của trường; không chụp phiếu đưa lên kho mã nguồn.
