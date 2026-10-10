**KỊCH BẢN THUYẾT TRÌNH 5 PHÚT**

FocusGuard • Bảng A THCS

> **Mục tiêu:** Học sinh nói tự nhiên, hiểu ý; không đọc như thuộc lòng. Tổng thời lượng mục tiêu: 4:30–4:50 để có biên an toàn.

## 0:00–0:35 • Mở bài

“Trong một lớp học, giáo viên phải vừa giảng vừa quan sát rất nhiều học sinh. Có những lúc một bạn dùng điện thoại, ngủ gật hoặc rời chỗ nhưng giáo viên khó phát hiện ngay. Nhóm em xây dựng FocusGuard để hỗ trợ giáo viên nhận biết những tín hiệu đó bằng camera, nhưng không thay giáo viên đưa ra kết luận.”

## 0:35–1:20 • Giải pháp

“Camera phát hiện người và điện thoại. Sau đó hệ thống dùng đặc trưng khuôn mặt để biết đó là học sinh nào đã đăng ký. Chúng em không nhìn một khung hình rồi kết luận ngay mà theo dõi theo thời gian: thấy điện thoại từ 1 giây, nhắm mắt từ 1,5 giây, quay đầu từ 2 giây mới tính; còn khuất camera dưới 10 giây thì chưa bị trừ điểm.”

## 1:20–2:10 • Phần AI và phần nhóm xây dựng

“Chúng em dùng bốn công nghệ có sẵn: YOLOv8 để phát hiện người và điện thoại, ByteTrack để theo dõi một người qua các khung hình, MediaPipe để lấy mốc khuôn mặt, InsightFace để nhận ra khuôn mặt. Chúng em không tự xây các mô hình này. Phần riêng của dự án là cách giữ danh tính ổn định, cách biến tín hiệu thành hành vi kéo dài theo thời gian, bộ tính điểm, phân quyền và giao diện. Phần mã này được viết với sự hỗ trợ của trợ lý lập trình AI và chúng em đã kê khai trong Prompt Log. Điểm tập trung là công thức công khai, không phải AI đọc được suy nghĩ của học sinh.”

## 2:10–3:05 • Sản phẩm

“Giáo viên đăng nhập, mở trang Giám sát và bấm Bắt đầu buổi học. Khi một hành vi kéo dài đủ lâu, mục Cần chú ý ngay hiện tên học sinh, hành vi và số giây, ví dụ ‘Dùng điện thoại, 8 giây’. Học sinh chỉ xem dữ liệu của mình; phụ huynh chỉ xem con mình. Nếu camera mất tín hiệu, hệ thống giữ nguyên điểm chứ không coi là học sinh mất tập trung.”

## 3:05–4:05 • Kiểm thử

“Hệ thống có 184 bài kiểm thử tự động đều đạt, 6 bài kiểm thử cơ sở dữ liệu Firestore trên máy giả lập đều đạt, và 3 bài kiểm thử phần cứng đều đạt. Chúng em đã chạy camera thật trên MacBook Air M3, khoảng 7,5 khung hình mỗi giây. Các tình huống dùng điện thoại, rời chỗ, ngắt camera đã chạy được. Nhưng mới chỉ thử với một người và chưa có bộ dữ liệu lớp học thật được gán nhãn, nên chúng em chưa công bố độ chính xác; hồ sơ ghi rõ là chưa đánh giá.”

## 4:05–4:45 • An toàn & kết

“FocusGuard không nhận diện cảm xúc, không đo hướng nhìn và không ghi video. Hệ thống chỉ là tín hiệu hỗ trợ giáo viên, không dùng để tự động kỷ luật hay chấm hạnh kiểm. Bước tiếp theo của chúng em là đánh giá trên dữ liệu có sự đồng ý và có người gán nhãn, và cải thiện việc phát hiện điện thoại vì hiện còn đứt đoạn. Chúng em muốn AI trong lớp học phải hữu ích, minh bạch và có trách nhiệm.”

# Những câu tuyệt đối không nói

- “AI của em đo chính xác mức độ tập trung của học sinh.”
- “Nhóm em tự xây YOLO/InsightFace.”
- “Hệ thống đã đạt độ chính xác X%” khi chưa có bộ dữ liệu thật gán nhãn.
- “184 bài kiểm thử đạt nghĩa là AI chính xác.” (Kiểm thử chỉ chứng minh mã chạy đúng thiết kế.)
- “Toàn bộ mã do chúng em tự gõ.” (Phải nói rõ có dùng trợ lý lập trình AI.)

# Mẹo trình bày

- Mỗi thành viên nói một phần nếu đội có 2–3 người.
- Khi nói công nghệ, luôn nối với “dùng để làm gì”, không đọc danh sách tên model.
- Nếu giám khảo hỏi sâu, trả lời phần đã kiểm chứng trước, hạn chế nói suy đoán.
