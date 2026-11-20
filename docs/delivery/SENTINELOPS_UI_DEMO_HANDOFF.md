# Bàn giao SentinelOps UI và kịch bản demo sự cố

Cập nhật ngày **19/11/2026**, giờ Việt Nam. Môi trường demo là EKS `sentinelops-dev`, region `ap-southeast-1`, AWS profile `console`, Kubernetes context `sentinelops-demo`, namespace `sentinelops-b-actions`.

## Link để mở

| Trang | Đường dẫn | Cách dùng |
| --- | --- | --- |
| Incident Workspace trên EKS | [Operator UI](https://dr76usfhbe2w8.cloudfront.net/ui/) | Bản thiết kế mới đã deploy. Chọn **Sign in with Cognito**, dùng tài khoản operator và MFA của mình. |
| Grafana | [Grafana](https://dr76usfhbe2w8.cloudfront.net/grafana/) | Đăng nhập Cognito. Mở [SLO](https://dr76usfhbe2w8.cloudfront.net/grafana/d/sentinelops-slo), [Business health and incidents](https://dr76usfhbe2w8.cloudfront.net/grafana/d/sentinelops-incidents) và [Worker telemetry](https://dr76usfhbe2w8.cloudfront.net/grafana/d/sentinelops-workers). |
| Fault Console trên EKS | [127.0.0.1:8765](http://127.0.0.1:8765/) | Port-forward tới pod. Có 8 thẻ với thao tác bị khóa, đọc fault mode thật của Payment và Inventory. |
| Fault Console local | [127.0.0.1:18765](http://127.0.0.1:18765/) | Preview theme trắng và tiếng Anh, dữ liệu mẫu, tất cả thao tác bị khóa. |
| Incident Workspace local | [127.0.0.1:18080/ui](http://127.0.0.1:18080/ui/) | Preview hai incident mẫu. Dán token mẫu bên dưới rồi bấm **Connect**. |
| Prometheus | [127.0.0.1:9090](http://127.0.0.1:9090/) | Port-forward đọc metric gốc. |
| Jaeger | [127.0.0.1:16686](http://127.0.0.1:16686/) | Port-forward xem trace và đường gọi Checkout → Payment. |

Token dành riêng cho **preview local**: `preview-sentinelops-token-local-only`.
Hai preview local trên máy bàn giao chạy qua herdr (`t7m` cho Incidents, `t7n` cho Fault Console). Hai preview và Operator UI public trả HTTP 200 khi kiểm tra để soạn file. Grafana public trả HTTP 302 để chuyển sang luồng đăng nhập. Địa chỉ `127.0.0.1` chỉ mở được từ máy có tiến trình hoặc port-forward tương ứng.

## Những gì đã làm

| Hạng mục | Kết quả |
| --- | --- |
| Fault Console | Xây 8 thẻ: Payment delay 950 và 2.000 ms, Payment 503, Inventory delay 950 ms, Inventory 503, cả hai 503, **Reset fault modes**, **Deploy regression image**. Console đọc lại fault mode mỗi 5 giây. Gói EKS chạy riêng ở chế độ chỉ đọc, qua loopback, với ServiceAccount không mount token. |
| Đồng bộ giao diện | Cả hai trang đổi sang theme trắng và tiếng Anh trong workspace làm việc. Bản Fault Console theme mới đã kiểm trên local. EKS `fault-console` vẫn ở image của lượt rollout chỉ đọc ban đầu. |
| Thiết kế lại Incident Workspace | Có số đếm theo bản ghi đã tải, trạng thái workspace, hàng đợi dạng thẻ, bộ lọc và **Reset**. Hồ sơ gom tóm tắt, bằng chứng, timeline, giải thích và action journal. Trên mobile có **Back to queue**. [Giao diện đang chạy](https://dr76usfhbe2w8.cloudfront.net/ui/) |
| Deploy Incident Workspace | Chỉ thay image của container `operator-ui` bằng lớp chứa năm file UI. Pod dùng chung được tạo lại, controller báo `READY`, cả 13 deployment Ready ở hậu kiểm. CloudFront trả đúng hash của UI mới, Cognito vẫn cấu hình OIDC và API incident yêu cầu xác thực. |
| Observability và demo live | CloudFront phục vụ Operator UI và Grafana. Có ba dashboard, business SLO rules, Jaeger trace và Prometheus trên PVC 20 GiB với retention cấu hình 15 ngày. KB1, KB2, KB3 và hai lượt B7 đã có kết quả live. [Bàn giao hạ tầng](HANDOVER_2026-11-19.md) · [Bằng chứng các kịch bản](DEMO_SCENARIOS_2026-11-19.md) |
| Rà soát qua herdr | `t7h` đối chiếu evidence console, `t7j` kiểm B9, `t7k` cập nhật runbook và đối soát sáu việc. |

Đã kiểm hai giao diện bằng Chrome với fixture, gồm desktop và mobile. Hậu kiểm thiết kế Incident Workspace kiểm các cỡ màn hình từ 320 đến 1440 px. Bộ kiểm Python cho console, fault endpoints và operator UI có **234 bài đạt**. Ba suite JavaScript về Cognito, proposals và workspace đều đạt.

Artifact EKS đã hậu kiểm:

- Incident Workspace: `sentinelops/control-plane@sha256:49314f1a4eefddf6f2bc16ceecb4c79afdef3d91875bc58729d3ffbb4b00c64f`.
- Fault Console chỉ đọc: `sentinelops/application@sha256:4e1a66c446b1ceab01cda52a2abb1ed9c29dad5164e5f0330362d5ae6fdf58c0`.

Bằng chứng chi tiết trên máy vận hành gồm báo cáo `docs/delivery/FAULT_CONSOLE_DEPLOY_2026-11-19.md`, gói `scripts/demo_console/deployment/`, hậu kiểm `.herdr/out/deploy/incident-ui-20261119/verified.json` và hai báo cáo `.herdr/out/hc-console-handoff.md`, `.herdr/out/hc-b9-review.md`. Các đường dẫn này cần workspace vận hành gốc.

## Khởi động trang bơm lỗi

### Mở bản EKS đang chạy

Cần `kubectl` và quyền `pods/portforward` trên đúng namespace. Nếu cổng 8765 đã có port-forward và trang mở được, dùng lại phiên đó. Nếu chưa, giữ lệnh này trong một terminal riêng:

```bash
AWS_PROFILE=console kubectl --context sentinelops-demo -n sentinelops-b-actions \
  port-forward --address 127.0.0.1 deployment/fault-console 8765:8765
```

Mở [Fault Console](http://127.0.0.1:8765/) bằng đúng IP và cổng. Backend kiểm Host và Origin theo `127.0.0.1:8765`. Dùng `Ctrl+C` để đóng port-forward. Kiểm nhanh gate và runner:

```bash
curl --noproxy '*' -fsS http://127.0.0.1:8765/api/status | jq '{
  enabled, busy, actions: (.actions | length),
  services: (.services | map_values({ok, mode, delay_ms})),
  runner: .deployment.state, configured: .deployment.configured
}'
```

Ở cấu hình bàn giao, kết quả mong đợi là `enabled=false`, `busy=false`, 8 thẻ, `runner=idle`, `configured=false`. `mode=healthy` chỉ xác nhận fault mode của instance trả lời GET. Kiểm sức khỏe nghiệp vụ qua readiness, metric và lưu lượng.

### Chạy source trên máy local

Từ gốc repo, cần Python 3.11 hoặc 3.12. Nếu chưa có `.venv`, tạo bằng `python3 -m venv .venv` và cài dependency bằng `.venv/bin/python -m pip install -e .`. Chạy ở cổng riêng để tránh đụng port-forward EKS và preview:

```bash
DEMO_CONSOLE_PORT=18766 \
DEMO_CONSOLE_ENABLE_FAULTS=false \
DEMO_CONSOLE_DEPLOY_SCRIPT= \
DEMO_CONSOLE_PAYMENT_URL=http://127.0.0.1:18081 \
DEMO_CONSOLE_INVENTORY_URL=http://127.0.0.1:18082 \
  .venv/bin/python -B -m scripts.demo_console
```

Mở [Fault Console local từ source](http://127.0.0.1:18766/). Hai URL upstream là các endpoint lab hoặc port-forward đã được operator xác minh. Khi thiếu upstream, giao diện vẫn mở và hiển thị **Unavailable**.

Trên máy bàn giao, nếu đã dừng hai preview dữ liệu mẫu và script tạm vẫn còn, có thể mở lại từ gốc repo bằng hai lệnh sau:

```bash
hc run --bg --outlive-parent --name sentinelops-fault-preview \
  .venv/bin/python -B /tmp/sentinelops-local-ui-preview.py fault 18765
hc run --bg --outlive-parent --name sentinelops-incidents-preview \
  .venv/bin/python -B /tmp/sentinelops-local-ui-preview.py incidents 18080
```

Script trong `/tmp` là tiện ích preview của máy này. Để chạy lại bài kiểm trình duyệt bằng fixture từ source, dùng `.venv/bin/python -m scripts.demo_console.check_ui` sau khi cài Playwright và Chromium. [Hướng dẫn console](../../scripts/demo_console/README.md)

## Flow demo khi có sự cố

**Đường trình diễn có thể dùng ngay** là mở incident mẫu trên local hoặc xem lại một incident đã ghi trên EKS. Chọn **KB1** để kể câu chuyện latency và recovery, hoặc **KB2** để minh họa lỗi 503 lan từ Payment sang Checkout. Với lượt gây lỗi mới trên EKS, dùng runbook dành cho operator trong phiên demo đã được phép. Fault Console EKS hiện chỉ quan sát. [Chọn lượt latency hoặc 503 trong runbook](../runbooks/DEMO_FLOW.md#3-bơm-lỗi-chọn-đúng-lượt)

### 1. Mở tab và kiểm baseline

Đăng nhập trước vào Incident Workspace và Grafana để sẵn sàng qua MFA. Trên Grafana mở **SLO** và **Business health and incidents**, chọn **Last 15 minutes**, refresh **5s**, giữ cả Checkout, Payment và Inventory. Trong Incident Workspace đặt **All states** và **All services** để thấy incident mới. Số trong ô **Incidents in view** chỉ tính các bản ghi đã tải theo bộ lọc.

Mở Prometheus, Jaeger và readiness trực tiếp nếu cần đối chiếu. Chạy các lệnh port-forward ở các terminal riêng:

```bash
AWS_PROFILE=console kubectl --context sentinelops-demo -n sentinelops-b-actions \
  port-forward --address 127.0.0.1 svc/prometheus 9090:9090

AWS_PROFILE=console kubectl --context sentinelops-demo -n sentinelops-b-actions \
  port-forward --address 127.0.0.1 svc/jaeger 16686:16686

AWS_PROFILE=console kubectl --context sentinelops-demo -n sentinelops-b-actions \
  port-forward --address 127.0.0.1 deployment/remediation-controller 18000:8000
```

Kiểm Deployment Ready, traffic **2 replica**, dữ liệu metric mới và có lưu lượng. Gọi [readiness trực tiếp](http://127.0.0.1:18000/ready) để xem các dependency. Public CloudFront đang trả 403 cho `/ready` và `/ready/incidents`, nên **Workspace status** trên UI public có thể hiện **Unknown** dù backend Ready.

### 2. Thấy tín hiệu và khoanh vùng ảnh hưởng

Ghi mốc bắt đầu UTC hoặc dùng khoảng thời gian của lượt lịch sử. Chọn một kịch bản mỗi lượt:

| Kịch bản | Điểm cần trình diễn | Kết quả đã có |
| --- | --- | --- |
| KB1: Payment chậm 950 ms | p95 Payment và Checkout tăng. Inventory dùng để đối chiếu. Trong Jaeger chọn một trace Checkout rồi mở span Payment cùng trace ID. | Lượt thật ghi p95 Payment khoảng **964 ms**, Checkout **968 ms**. Incident **high**, sau đó **recovered / CLOSED**, revision 4. |
| KB2: Payment trả 503 | Error ratio Payment và Checkout tăng, success rate và error budget giảm. Trong trace cùng request, Payment trả 503 và Checkout trả 502. | Breach xuất hiện sau khoảng **19 giây**, incident **critical** được publish sau khoảng **4,9 phút** ở lượt đã đo. |
| KB3: image regression | Giải thích vì sao cần bằng chứng đổi deployment để đi tiếp từ RCA đến đề xuất. | Lượt này thiếu receipt, RCA abstain và không sinh proposal. Image gốc đã được khôi phục khi kết thúc. |
| B7: image regression có receipt | Xem proposal lịch sử và giải thích đường ghi deployment change, model evidence và quyết định on-call. | **2/2** lượt sinh proposal. Cả hai lượt kết thúc bằng khôi phục image gốc trong quy trình demo. Chưa duyệt qua UI và chưa chứng minh executor tự rollback trên EKS. |

Các mốc trên là số đo lịch sử. Health breach dùng ngưỡng p95 **>750 ms** hoặc lỗi 5xx **>5%**. Dashboard SLO đặt mục tiêu p95 **<1.000 ms** và success **≥99,5%**. Incident cần đủ bucket và dữ liệu hợp lệ nên có thể xuất hiện sau tín hiệu trên dashboard. [Số đo, ID incident và trace](DEMO_SCENARIOS_2026-11-19.md)

### 3. Điều tra trên Incident Workspace

1. Bấm **Refresh**, chọn thẻ trong **Incident queue**, đọc severity, state, service ảnh hưởng và revision. Nếu mở KB1 lịch sử, lọc **Recovered** và **Payment**, rồi đối chiếu ID trong báo cáo kịch bản.
2. Vào **Overview** để xem **At a glance**, sau đó đọc **Root-cause hypotheses** và **Detection sources**. Ranking là mức hỗ trợ của bằng chứng. **Unknown** hoặc **abstain** thể hiện thiếu cơ sở kết luận.
3. Mở **Evidence** để xem log/metric/trace đã ghi và copy source reference. Dùng **Timeline** để thấy các sự kiện qua từng revision. Mở **Explanation** để tóm tắt đúng revision đang xem. Khi UI ghi **Deterministic fallback**, phần giải thích được tạo theo quy tắc cố định từ bằng chứng.
4. Xem **Runbook proposals** và **Actions**. Với KB1/KB2, thiếu điều kiện đổi deployment nên không chờ proposal rollback. Với proposal hợp lệ trong phiên được phép, đọc kỹ incident/revision, hai image digest, deadline và lý do trước **Approve** hoặc **Reject**. Controller kiểm lại điều kiện sau quyết định, còn kết quả thực thi nằm ở **Actions**.

### 4. Gỡ lỗi và xác minh hồi phục

Với một lượt fault mode đang chạy, operator dùng quy trình gỡ tương ứng trong runbook rồi đọc lại `mode=healthy` và `delay_ms=0`. Nếu demo bằng dữ liệu đã ghi, trình bày evidence gỡ lỗi và các revision sau đó. Giữ traffic, theo dõi p95 và 5xx về baseline, chờ đủ bucket hồi phục, rồi refresh để đọc revision mới, trạng thái **Recovered/CLOSED** và nguồn xác minh. Với lượt đổi image cần đối chiếu thêm digest, rollout Ready, action journal và telemetry. Một receipt duyệt hoặc mã thoát script mới chỉ là bằng chứng của bước tương ứng. [Runbook đầy đủ từ preflight đến cleanup](../runbooks/DEMO_FLOW.md)

## Việc còn mở

- **B9** đã sửa cleanup và qua kiểm offline. Lượt chạy thật và nút **Deploy regression image** vẫn khóa cho tới khi có quyền phục hồi độc quyền, watchdog độc lập và khóa theo dõi qua restart đã được diễn tập.
- **Freshness 900 giây** có trong source. Rollout giao diện giữ nguyên image của container controller, nên chưa có bằng chứng cấu hình 900 giây đã được áp dụng live. Dùng deadline và policy thực tế của phiên, vì TTL đề xuất và độ tươi của evidence là hai giới hạn riêng.
- **Admission** đã sửa CEL, đang ở **Audit/Warn**. Chế độ **Deny** còn chờ kiểm chứng và triển khai riêng. [Đánh giá bảo mật](SECURITY_POSTURE_2026-11-19.md)
- **Email cảnh báo** có thiết kế Prometheus → Alertmanager → SNS trong `.herdr/out/al1-email.md` trên máy vận hành. Snapshot bàn giao chưa thấy topic `sentinelops-dev-incidents` và chưa chứng minh email đến inbox. Dashboard worker có thể trống do thiếu metric đầu vào. [Tình trạng worker telemetry](HANDOVER_2026-11-19.md#2-ba-dashboard-và-cách-đọc)

[Repository SentinelOps](https://github.com/VietSory/SentinelOps) · [Bàn giao hạ tầng](HANDOVER_2026-11-19.md) · [Runbook demo](../runbooks/DEMO_FLOW.md)
