# BỘ CÂU HỎI PHẢN BIỆN – FOCUSGUARD

## 1. FocusGuard có thật sự đo được “tập trung” không?
Không. Hệ thống không biết học sinh đang nghĩ gì. Nó chỉ ghi nhận hành vi nhìn thấy được (dùng điện thoại, nhắm mắt lâu, quay đi, rời chỗ) và tính một chỉ số theo quy tắc công khai để giáo viên tham khảo.

## 2. Vì sao không dùng model AI dự đoán focus?
Nhóm ưu tiên kết quả giải thích được và tránh tuyên bố quá khả năng dữ liệu. Khi chưa có dataset thật gán nhãn, mô hình dự đoán sẽ khó kiểm chứng.

## 3. YOLOv8 do nhóm tự làm không?
Không. YOLOv8 là mô hình có sẵn, nhóm không huấn luyện lại. Nhóm dùng nó để phát hiện người và điện thoại, rồi nối với phần riêng của dự án: giữ danh tính, phân tích hành vi theo thời gian, tính điểm, phân quyền và kiểm thử.

## 3b. Mã nguồn có phải do các em tự viết hết không?
Không. Phần lớn mã được viết với trợ lý lập trình AI; lịch sử commit và Prompt Log ghi rõ điều đó. Việc của nhóm là nêu yêu cầu, đọc hiểu, chạy thử với camera thật, phát hiện lỗi và yêu cầu sửa có kiểm thử. Chỉ trả lời về những phần mình thật sự hiểu và giải thích được.

## 4. YOLO, ByteTrack và InsightFace khác nhau thế nào?
YOLO trả lời "trong ảnh này có người, có điện thoại ở đâu". ByteTrack trả lời "người ở khung hình này có phải người ở khung hình trước không" và gắn một số theo dõi tạm. InsightFace trả lời "khuôn mặt này giống học sinh nào đã đăng ký". IdentityManager của dự án mới là nơi quyết định đó là học sinh nào.

## 4b. Vì sao số theo dõi (track_id) không phải là mã học sinh?
Vì số theo dõi chỉ là số tạm của camera. Khi học sinh bị che hoặc ra khỏi khung hình rồi quay lại, camera có thể cấp số mới. Trong lần chạy thật, một người đã đổi từ số 1 sang số 19. Nếu coi số đó là học sinh thì điểm sẽ bị gắn nhầm hoặc bị đặt lại.

## 4c. Vì sao phải chờ đủ thời gian mới tính là hành vi?
Vì một khung hình có thể sai: chớp mắt trông giống nhắm mắt, liếc nhanh trông giống quay đi. Chờ đủ 1 đến 2 giây liên tục giúp bỏ qua những khoảnh khắc đó, giống như giáo viên không nhắc một bạn chỉ vì bạn ấy chớp mắt.

## 5. Nếu hai học sinh đi cắt nhau thì sao?
IdentityManager có khóa danh tính, xác nhận theo thời gian, chống trùng và conflict/recovery. Kịch bản hai người thật vẫn cần thêm volunteer để đánh giá đầy đủ.

## 6. Tại sao chớp mắt không bị báo buồn ngủ?
Chớp mắt rất ngắn. Hệ thống chỉ ghi "Buồn ngủ" khi mắt nhắm liên tục từ 1,5 giây. Ngưỡng "mắt nhắm" cũng tính theo độ mở mắt bình thường của từng bạn, vì mắt mỗi người mở to nhỏ khác nhau.

## 7. Điện thoại lướt qua camera có bị trừ điểm không?
Không. Phải thấy điện thoại liên tục từ 1 giây mới tính. Ngược lại, nếu camera mất dấu điện thoại trong chốc lát (dưới 1 giây) thì vẫn coi là cùng một lần dùng.

## 7b. Phát hiện điện thoại có luôn đúng không?
Không. Với webcam laptop và mô hình YOLOv8 nhỏ, việc phát hiện còn đứt đoạn: trong buổi chạy chụp ảnh hồ sơ, hệ thống ghi 6 lần ngắn (tổng 22,3 giây) và nhiều lúc cúi nhìn điện thoại được ghi là "Quay đi chỗ khác". Đây là hạn chế đã nêu trong hồ sơ.

## 8. Camera mất thì điểm có giảm không?
Không. Khi mất tín hiệu camera, hệ thống không biết học sinh đang làm gì nên giữ nguyên điểm. Trừ điểm lúc đó là kết luận khi không có bằng chứng.

## 9. Người lạ vào lớp thì sao?
Theo thiết kế, nếu khuôn mặt không đủ giống học sinh nào đã đăng ký thì hệ thống để là "chưa nhận diện" thay vì đoán. Phần này đã có kiểm thử tự động, nhưng chưa chạy với người lạ thật trước camera vì cần thêm người thử.

## 10. Có lưu ảnh/video học sinh không?
Không ghi video và không lưu khung hình camera. Ảnh đăng ký chỉ nằm trong bộ nhớ lúc trích đặc trưng rồi bỏ. Hệ thống chỉ lưu một dãy số đặc trưng của khuôn mặt, và quản trị viên xóa được.

## 11. Embedding có nhạy cảm không?
Có. Vì vậy cần consent phù hợp, kiểm soát quyền truy cập và khả năng xóa.

## 12. Độ chính xác hiện bao nhiêu?
Chưa có con số đáng tin cậy nên nhóm không công bố. Muốn có độ chính xác phải có video lớp học thật, được đồng ý, và có người ghi lại từng giây học sinh đang làm gì để so sánh. Nhóm chưa có bộ dữ liệu đó, nên hồ sơ ghi là chưa đánh giá (NOT_EVALUATED).

## 13. 184 bài kiểm thử chứng minh gì?
Chứng minh mã chạy đúng thiết kế: quy tắc tính điểm, ngưỡng thời gian, phân quyền, giao diện. Chúng không chứng minh AI nhận đúng bao nhiêu phần trăm trong lớp học thật.

## 14. Camera thật đã thử chưa?
Có. Một người thử trên MacBook Air M3, khoảng 7,5 khung hình mỗi giây; bảy trên chín kịch bản đã đạt, hai kịch bản cần thêm người. Đó là kiểm tra hệ thống chạy được, không dùng để suy ra độ chính xác.

## 15. Điểm tập trung tính thế nào?
Bắt đầu từ 100. Mỗi giây tập trung cộng 0,5; dùng điện thoại trừ 2; buồn ngủ trừ 1,5; quay đi hoặc rời chỗ trừ 1. Khi tạm khuất, chưa có dữ liệu hoặc mất camera thì giữ nguyên. Điểm luôn nằm trong khoảng 0 đến 100. Đây là quy tắc do dự án đặt ra, không phải kết quả học máy.

## 16. Tại sao PHONE bị trừ nhiều hơn HEAD_AWAY?
Đây là lựa chọn của dự án ở thời điểm này, không phải chân lý khoa học; các hệ số có thể chỉnh lại khi có dữ liệu thật.

## 17. Giáo viên có thể xem lớp khác không?
Không theo thiết kế; authz kiểm tra quyền sở hữu lớp ở server.

## 18. Client có thể hack điểm 100 không?
Không theo contract; client push trạng thái AI bị server từ chối.

## 19. Tại sao có cả Firestore và SQLite?
SQLite thuận tiện local/demo; Firestore là backend tùy chọn. Repository layer tách persistence khỏi business logic.

## 20. Sản phẩm có thay giáo viên không?
Không. Giáo viên luôn là người quyết định.

## 21. Điểm mới của nhóm là gì?
Không phải một mô hình AI mới. Điểm riêng là cách ghép các mô hình có sẵn thành một sản phẩm trung thực: giữ danh tính ổn định, chỉ tính hành vi kéo dài, một bộ tính điểm công khai, phân biệt "mất camera" với "mất tập trung", và không hiển thị số liệu khi chưa có dữ liệu.

## 22. Nếu BTC yêu cầu đổi UX trong thời gian ngắn?
Hiểu change-request → xác định screen/contract → sửa bounded → test regression → demo lại.

## 23. Hạn chế lớn nhất?
Chưa có đánh giá trên dữ liệu thật có gán nhãn; chưa thử nhiều người và người lạ với camera thật; phát hiện điện thoại còn đứt đoạn; dữ liệu khuôn mặt là dữ liệu nhạy cảm nên cần quản lý nghiêm túc.

## 24. Bước tiếp theo quan trọng nhất?
Làm một đợt đánh giá nhỏ có sự đồng ý và có người gán nhãn, để báo cáo trung thực tỷ lệ phát hiện đúng và cảnh báo sai; đồng thời cải thiện phát hiện điện thoại.

## Cách luyện
- Trả lời 15–30 giây/câu.
- Hiểu ý, không học thuộc chữ.
- Nếu chưa biết hoặc chưa kiểm chứng, nói rõ thay vì đoán.
