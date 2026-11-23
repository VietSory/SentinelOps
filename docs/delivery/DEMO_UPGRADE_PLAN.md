# Kế hoạch nâng cấp demo — cảnh báo sớm, duyệt không đếm ngược, email

Owner duyệt ngày **2026-11-24**. Mục tiêu là một buổi demo thuyết phục, chứng minh
SentinelOps uy tín và hiệu quả. Đây không phải kế hoạch hoàn thiện cho vận hành
thương mại; các khoảng cách đó được ghi riêng ở
[PRODUCTION_GAPS.md](PRODUCTION_GAPS.md) và chưa làm trong đợt này.

## 1. Hiện trạng

Demo hiện tại: chạy script gây lỗi → Grafana hiện lỗi → mở incident → bấm Duyệt →
hệ thống rollback Payment → Grafana trở lại bình thường.

Ba điểm còn thiếu:

1. **Chưa có cảnh báo trước khi lỗi xảy ra.** XGBoost đã làm được việc này trên dữ
   liệu local: 20/20 lượt suy giảm dần được báo trước khoảng 270 giây
   ([báo cáo layer 2](../reports/LAYER2_LOCAL_ALIBABA_DE38FC6B.md)). Nhưng khi chạy
   thật, model chỉ được gọi lúc incident đã mở
   (`ModelIncidentEvidence.capture`, `src/sentinelops/workers/model_signals.py:101`).
   Health worker không dùng output model (`src/sentinelops/workers/health.py:220`).
   Vì vậy chưa có gì để hiện lên Grafana, UI hay email trước thời điểm vi phạm SLO.
2. **Đề xuất rollback có đồng hồ 15 phút.** Quá hạn thì proposal chuyển `escalated`
   (`src/sentinelops/remediation/controller.py:331`,
   `src/sentinelops/remediation/pod_memory.py:387`,
   `src/sentinelops/remediation/proposal_api.py:118`). Ngoài ra RCA chỉ được tính
   một lần và phải mới hơn 900 giây lúc thực thi
   (`src/sentinelops/remediation/authority.py:122-124`), nên chỉ xoá đồng hồ UI là
   chưa đủ.
3. **Không có kênh báo người trực ngoài trình duyệt.** Console cũ chỉ có thông báo
   trình duyệt khi tab đang mở (`src/sentinelops/ui/assets/proposalwatch.js`).
   Không có email hay tin nhắn, nên người trực phải ngồi canh UI.

## 2. Kịch bản demo mục tiêu

Một câu chuyện liền mạch, khoảng 10–15 phút:

| Bước | Người xem thấy | Thời điểm dự kiến |
| --- | --- | --- |
| 1. Hệ thống khỏe | Grafana xanh; panel mới "Nguy cơ trong 5 phút tới" ở mức Bình thường | T0 |
| 2. Bấm "Phát hành Payment v2" trên Fault Console | Deployment đổi image; bản v2 làm Payment chậm dần | T0 |
| 3. Cảnh báo sớm | Độ trễ vẫn dưới ngưỡng, Grafana vẫn xanh, nhưng panel nguy cơ chuyển "Sắp vi phạm"; email "Cảnh báo sớm"; UI hiện thẻ cảnh báo kèm lý do (độ trễ đang tăng, vừa có bản phát hành mới) | T0 + 2–3 phút |
| 4. Vi phạm SLO | Incident mở; RCA xếp Payment hạng 1 và chỉ ra bản v2; đề xuất rollback; email "Cần duyệt" | T0 + 6–8 phút |
| 5. Chờ duyệt | Proposal hiện "Chờ duyệt từ hh:mm", không còn đồng hồ | tới khi người trực bấm |
| 6. Duyệt | Rollback về image tốt; Grafana xanh lại; email "Đã hồi phục" | vài phút sau khi duyệt |
| 7. Tổng kết | "Cảnh báo trước X phút · phát hiện sau Y · khôi phục sau Z", lấy từ số đo của chính lượt chạy | cuối lượt |

Thời điểm ở bảng là mục tiêu cần chỉnh trong lúc tập dượt (mục 4.5).

## 3. Các lựa chọn đã chốt

Theo khuyến nghị trong kế hoạch, owner duyệt ngày 2026-11-24:

- Kịch bản liền mạch như mục 2. Nếu tập dượt không đạt, dùng phương án hai màn ở mục 6.
- Email gửi qua AWS SNS (nội dung dạng chữ). Chỉ gửi ở mức "Sắp vi phạm"; mức
  "Theo dõi" chỉ hiện trên Grafana và UI.
- "Rollback phòng ngừa" (duyệt ngay khi có cảnh báo, trước khi vi phạm) để sau khi
  phần chính chạy ổn.

Còn chờ owner: địa chỉ email nhận (cấp ngoài repo) và ngày dựng lại AWS.

## 4. Hạng mục

Mỗi hạng mục: Codex triển khai, rồi qua hai lượt review (tự review và review độc lập)
trước khi lên `main`. Mọi thao tác ghi lên AWS cũng qua hai lượt review.

### 4.1 Bộ chấm điểm rủi ro mỗi phút (2–3 ngày)

- Với mỗi bucket health đã được chấp nhận, chấm IF và XGBoost bằng đúng model
  release đang triển khai và đúng hàm suy luận mà incident evidence đang dùng
  (`collect_model_signals`). Không huấn luyện lại, không đổi threshold.
- Kết quả theo từng service: trạng thái `normal` / `watch` / `degrading` và điểm rủi ro.
- Đưa ra ba nơi: metric cho panel Grafana "Nguy cơ trong 5 phút tới", API cho thẻ
  cảnh báo trên UI, và một email mỗi đợt nguy cơ.
- Chỉ gửi email khi `degrading` kéo dài 2 bucket liên tiếp; không gửi lặp trong cùng
  một đợt; gửi kèm dòng "đã hết nguy cơ" khi trạng thái trở lại `normal`.
- Cảnh báo chỉ để thông báo. Cảnh báo không mở incident và không cho phép hành động
  tự động.

### 4.2 Duyệt không đếm ngược và email (1,5–2 ngày)

- Bỏ hạn 15 phút. Proposal ở trạng thái chờ tới khi người trực duyệt hoặc từ chối.
- Proposal tự đóng, không chuyển `escalated`, khi incident tự hồi phục, khi Payment
  không còn chạy image lỗi, hoặc khi có một lần deploy mới hơn.
- Lúc bấm Duyệt, controller kiểm lại hiện trạng trước khi rollback: Payment vẫn đang
  chạy đúng image lỗi đã ghi trong bằng chứng thay đổi, và health mới nhất vẫn đang
  vi phạm. Điều kiện này thay cho yêu cầu RCA phải mới hơn 900 giây. Mọi guard khác
  giữ nguyên.
- UI thay đồng hồ đếm ngược bằng "Chờ duyệt từ hh:mm".
- Email khi có proposal mới và khi có kết quả (hồi phục hoặc cần kỹ sư).
- Receipt cũ có `expires_at` vẫn đọc được. Áp dụng cả cho runbook OOM.
- Test an toàn: duyệt sau 2 giờ khi tình trạng không đổi thì rollback; incident đã
  tự hồi phục thì không rollback; có deploy mới hơn thì proposal đóng; bấm Duyệt hai
  lần chỉ có một rollback.

### 4.3 Kịch bản và công cụ demo (khoảng 2 ngày)

- Image "Payment v2 chậm dần", dựng theo cùng cách với image lỗi hiện có
  (`docker/Dockerfile.payment-regression`, `scripts/payment_regression.py`), nhưng
  độ trễ tăng dần theo thời gian thay vì nhảy bậc.
- Nút "Phát hành Payment v2" trên Fault Console.
- Nút "Đặt lại demo" một chạm: đưa Payment về image tốt, tắt fault, chờ hệ thống khỏe.
- Màn tổng kết lượt chạy: các mốc cảnh báo, vi phạm, incident, duyệt và hồi phục, hiện
  trên UI và làm annotation trên Grafana.

### 4.4 Sửa lỗi lộ ra khi trình chiếu (khoảng ½ ngày)

- Thêm `/ready` vào allowlist của CloudFront/ALB (`infra/aws/edge.tf:10`). Hiện UI
  luôn hiện "Unknown" ở hai chấm Incident store và Model readiness.
- Đổi nhãn "confidence %" kèm giải thích (mục 5). Hiện có hai con số mang nhãn này:
  điểm RCA của từng giả thuyết (`web/src/live/adapters.ts:139`) và mức khớp runbook
  của đề xuất (`web/src/live/adapters.ts:94`). Cả hai là điểm 0–1 nhân 100, không
  phải xác suất. Nhãn mới: "Điểm RCA" và "Mức khớp runbook".

### 4.5 Tập dượt và tiêu chí đạt (khoảng 2 ngày)

Chạy local trước để chỉnh tốc độ chậm dần, sau đó dựng lại AWS và chạy thật.
Demo đạt khi có đủ các điều sau:

- Cảnh báo đến trước vi phạm SLO ít nhất 3 phút ở 3/3 lượt.
- Vi phạm xảy ra trong vòng 10 phút sau khi phát hành v2. Rollback chỉ được đề xuất
  khi deploy nằm trong cửa sổ 600 giây trước lúc vi phạm
  (`change_window_seconds`, `src/sentinelops/remediation/domain.py:80`).
- Không có cảnh báo nhầm trong 1 giờ hệ thống khỏe.
- 3 lượt liên tiếp chạy trọn kịch bản mà không phải sửa tay.
- Mỗi lượt lưu số đo: thời gian báo trước, thời gian phát hiện, thời gian hồi phục.

## 5. Cách trình bày "Điểm RCA" với ban giám khảo

**Điểm RCA là gì.** Điểm tổng hợp bằng chứng, thang 0–1, gồm 5 thành phần có trọng
số cố định trong cấu hình đã review (`configs/rca_policy.json`):

| Thành phần | Trọng số | Câu hỏi nó trả lời |
| --- | --- | --- |
| Độ bất thường | 0,25 | Service lệch khỏi bình thường bao nhiêu? Lấy từ điểm Isolation Forest khi có model |
| Thứ tự thời gian | 0,20 | Service nào hỏng trước? |
| Quan hệ phụ thuộc và trace | 0,25 | Lỗi lan theo đường gọi nào? |
| Log | 0,15 | Log có ghi nhận lỗi tương ứng không? |
| Thay đổi gần đây | 0,15 | Có deploy ngay trước khi lỗi xuất hiện không? |

**Khi nào hệ thống dám kết luận.** Chỉ khi cả ba điều kiện đạt: điểm từ 0,75 trở
lên, bỏ xa ứng viên thứ hai ít nhất 0,15, và độ phủ bằng chứng từ 75% trở lên. Thiếu
một điều kiện, hệ thống ghi "chưa kết luận", chuyển kỹ sư và không đề xuất tự sửa.

**Con số trên thẻ đề xuất rollback** là "Mức khớp runbook": triệu chứng của incident
khớp với runbook trong catalog tới đâu. Hệ thống chỉ đề xuất khi RCA không ở trạng
thái "chưa kết luận", mức khớp từ 0,80 trở lên và bỏ xa runbook thứ hai ít nhất 0,15
(`src/sentinelops/remediation/controller.py:129`).

**Lời thoại gợi ý, khoảng 30 giây:**

> "Con số này không phải xác suất kiểu 'đúng 82%'. Nó là điểm cộng có trọng số của
> năm loại bằng chứng, và mỗi loại đều hiện riêng để người trực kiểm tra. Hệ thống
> chỉ kết luận khi điểm vượt 0,75 và bỏ xa nguyên nhân thứ hai ít nhất 0,15. Khi
> bằng chứng chưa đủ, nó nói 'chưa kết luận' thay vì đoán. Chúng tôi không gọi đây là
> phần trăm tin cậy, vì muốn nói vậy phải hiệu chỉnh trên rất nhiều sự cố thật. Đó là
> bước tiếp theo khi có đủ dữ liệu."

**Vì sao cách này thuyết phục hơn "% tin cậy".**

- Câu hỏi dễ gặp nhất là "94% này tính thế nào?". Hiện từng thành phần và ngưỡng
  là trả lời trước khi bị hỏi.
- Khả năng "chưa kết luận" là điểm mạnh về an toàn: hệ thống không tự sửa khi không
  chắc.
- Trung thực về giới hạn tạo uy tín hơn một con số đẹp nhưng không giải thích được.

**Câu hỏi có thể gặp:**

- *"Trọng số lấy ở đâu?"* — Cố định trong cấu hình đã review, không đổi theo từng
  incident và không chỉnh sau khi xem kết quả.
- *"AI nằm ở đâu?"* — Isolation Forest đo độ bất thường và góp vào điểm RCA. XGBoost
  dự báo nguy cơ vi phạm trong 5 phút tới; đó là cảnh báo sớm trong kịch bản này.
- *"Nếu điểm thấp thì sao?"* — Hệ thống chuyển kỹ sư và không đề xuất tự sửa. Nên
  chuẩn bị sẵn một ảnh chụp incident "chưa kết luận" để minh hoạ.

## 6. Rủi ro và phương án lùi

| Rủi ro | Xử lý |
| --- | --- |
| Model mới được đánh giá với kiểu chậm dần trong 20 phút. Bản v2 phải chậm nhanh hơn để vi phạm trong 10 phút, nên cảnh báo có thể đến sát giờ | Đo ở bước tập dượt local. Không đạt thì tách hai màn: màn 1 cảnh báo sớm bằng đúng kiểu chậm dần 20 phút đã đánh giá; màn 2 rollback bằng image lỗi hiện có |
| Cảnh báo nhầm khi hệ thống khỏe | Chỉ gửi email khi `degrading` 2 bucket liên tiếp; chạy 1 giờ khỏe để kiểm |
| Thời gian báo trước khi chạy thật ngắn hơn số đo local, vì có độ trễ thu số liệu | Ghi số đo thật của từng lượt; dùng số đó trong lời thoại, không dùng số local |
| AWS đã teardown ngày 22/11/2026 | Dựng lại theo [REBUILD](../../scripts/aws_demo/REBUILD.md) trước buổi tập |

## 7. Thứ tự và ước lượng

1. Song song: 4.1 bộ chấm rủi ro và 4.2 duyệt không đếm ngược.
2. 4.3 kịch bản và 4.4 sửa lỗi trình chiếu.
3. 4.5 tập dượt local, dựng lại AWS, chạy 3 lượt liên tiếp.

Tổng khoảng 5–7 ngày làm việc.
