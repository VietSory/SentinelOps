# Kịch bản điều phối demo trực tiếp SentinelOps

Áp dụng cho branch `feat/grafana-incident-dashboard`, EKS `sentinelops-dev`, context `sentinelops-demo`, namespace `sentinelops-b-actions`. Mốc lịch sử bên dưới là **UTC ngày 19/11/2026**; giờ Việt Nam = UTC + 7. Các lệnh dành cho người vận hành chạy trong buổi demo; việc viết runbook không phải một lượt kiểm chứng live mới.

**Trạng thái bàn giao 19/11:** đã deploy riêng [`fault-console`](../delivery/FAULT_CONSOLE_DEPLOY_2026-11-19.md), chỉ đọc qua `http://127.0.0.1:8765`; mọi thao tác OFF, không runner B7/B9, token ServiceAccount, Service/Ingress hoặc RBAC cho console. 12 workload cũ giữ nguyên spec/image, tổng hiện **13 Deployment**. Chạy phần chuẩn bị/baseline dưới đây trước; các lượt gây lỗi và duyệt chỉ thực hiện trong phiên được cho phép riêng, không tự bật fault từ bước kiểm console.

CFG2 đã đổi **source** sang freshness 900 và báo 939 tests PASS, **chưa có bằng chứng controller live đã nhận thay đổi**. Digest/spec controller trong snapshot trước/sau deploy console không đổi. Trước lượt C phải xác minh policy thực sự đang chạy; không dùng cửa sổ 900 để duyệt nếu chưa xác minh. B9-fix có 90 offline PASS nhưng chưa E2E/watchdog độc lập, nên lượt C còn chờ review recovery/quyền rollback. Admission đã sửa CEL, vẫn **Audit/Warn + Ignore**, chưa Deny. Email mới có thiết kế AL1; GET của task cha **t6k** cho topic `sentinelops-dev-incidents` trả `[]`, AL2 bị ngắt; chưa xác nhận subscription/delivery, không chạy bootstrap/apply email từ runbook này.

> **QUAN TRỌNG NHẤT — NGƯỜI BẤM DUYỆT PHẢI ĐỨNG SẴN.**
> Owner chọn freshness **900 giây cho policy mới để operator có đủ thời gian phản ứng**; RCA freshness tính từ `RCA.as_of` và được kiểm lại **lúc executor thực thi**. Policy 180 giây giữ để đọc action lịch sử. Cửa sổ thao tác thường **còn ít hơn 900 giây khi proposal hiện ra**. **TTL proposal 15 phút không bảo đảm đủ 15 phút để thực thi từ lúc UI xuất hiện.** Đăng nhập/MFA, đọc quy trình và chuẩn bị CLI trước khi bơm lỗi; khi proposal mới xuất hiện, kiểm đúng bằng chứng rồi duyệt ngay. Không dành thời gian này để thuyết trình Jaeger hoặc thiết lập MFA.
>
> Với policy mới, hạn thực tế bị chặn bởi `RCA.as_of + 900 giây`, tuổi health/Deployment observation và `proposal.expires_at`; phải chừa thời gian cho controller poll/execute. Countdown proposal hoặc thời gian giữ lỗi của B9 không kéo dài freshness. Quá hạn có thể nhận `approval-after-evidence-freshness` dù UI vừa ghi nhận approval thành công. Không sửa timestamp/receipt để thử lại.

## 1. Chuẩn bị trước khi khán giả vào

Phân công một người điều phối màn hình, một operator chạy terminal và một on-call bấm **Duyệt**. Một người có thể kiêm nhiệm, nhưng từ lúc incident xuất hiện phải giữ sẵn tab proposal. Dành riêng một lượt cho latency, một lượt cho lỗi 503 và một lượt đổi image để duyệt; không bơm chồng các lỗi.

### Tài khoản và các tab

1. **Cognito phải đặt mật khẩu permanent và enroll MFA TOTP xong từ trước**, thử đăng nhập lại thành công. Không đợi proposal mới quét QR. Người bấm dùng tài khoản của mình, có quyền đọc incident và duyệt remediation; không đưa mật khẩu, QR, mã MFA hoặc bearer token vào tài liệu/terminal trình chiếu.
2. Mở [UI operator](https://dr76usfhbe2w8.cloudfront.net/ui/), đăng nhập Cognito, hoàn tất callback và **để nguyên tab đã đăng nhập**. Bỏ bộ lọc state/service để không che incident mới; thử mở một incident lịch sử. Kiểm lại phiên ngay trước lượt đổi image.
3. Mở sẵn [Grafana `sentinelops-slo`](https://dr76usfhbe2w8.cloudfront.net/grafana/d/sentinelops-slo), đăng nhập Cognito trước. Chọn **Last 15 minutes**, refresh **5s**, hiển thị Payment/Checkout/Inventory. Mở thêm [dashboard nghiệp vụ](https://dr76usfhbe2w8.cloudfront.net/grafana/d/sentinelops-incidents) để xem error ratio và breach.
4. Mở Jaeger qua port-forward ở dưới. Chuẩn bị terminal dự phòng duyệt tại mục 5, điền danh tính on-call trước; proposal ID chỉ lấy từ **lượt mới**.

### Terminal điều khiển: kiểm đúng 12 Deployment cũ, console tùy chọn và traffic 2 replica

Chạy tại thư mục gốc repo. Các terminal mới cũng cần `AWS_PROFILE=console`; hàm `k` chỉ tồn tại trong terminal đã khai báo.

```bash
export AWS_PROFILE=console
export AWS_REGION=ap-southeast-1
k() { kubectl --context sentinelops-demo -n sentinelops-b-actions "$@"; }

git branch --show-current
aws sts get-caller-identity --query Account --output text
k get deployments,pods,pvc
k get deployments -o json | jq -e '
  [.items[] | select(.metadata.name != "fault-console")] as $baseline |
  [.items[] | select(.metadata.name == "fault-console")] as $console |
  ($baseline | map(.metadata.name) | sort) == [
    "checkout-service", "grafana", "inventory-service", "jaeger",
    "otel-collector", "payment-service", "postgres", "prometheus",
    "remediation-controller", "sentinelops-health-worker",
    "sentinelops-incident-worker", "sentinelops-traffic"
  ] and
  ($console | length) <= 1 and
  all($baseline[];
    (.spec.replicas // 1) > 0 and
    (.status.observedGeneration // 0) >= .metadata.generation and
    (.status.updatedReplicas // 0) == (.spec.replicas // 1) and
    (.status.readyReplicas // 0) == (.spec.replicas // 1) and
    (.status.availableReplicas // 0) == (.spec.replicas // 1))'
k get deployment sentinelops-traffic -o json | jq -e '
  .spec.replicas == 2 and .status.updatedReplicas == 2 and
  .status.readyReplicas == 2 and .status.availableReplicas == 2'
k logs deployment/sentinelops-traffic --all-pods=true --tail=10
```

Kết quả cần có: đúng branch, account **`833925774773`**, hai phép kiểm `jq` trả **`true`/exit 0**, PVC cần dùng `Bound`, traffic có request mới. Phép kiểm tên/readiness áp dụng đủ **12 Deployment cũ**, chỉ cho phép thêm `fault-console` tùy chọn: tổng hợp lệ là **12 hoặc 13**, hiện tại là **13**. Thiếu workload cũ, thêm workload khác hoặc workload cũ chưa Ready đều không đạt. Console được kiểm riêng nếu trình chiếu, không thay thế baseline nghiệp vụ. **12 Deployment cũ Ready không có nghĩa 12 Pod**: traffic có 2 replica, Pod controller có 2 container. Nếu baseline khác, xử lý trước buổi; không bắt đầu bơm lỗi. Riêng traffic bị giảm replica, trong phiên demo đã được phép, trả về cấu hình demo rồi chạy lại phép kiểm:

```bash
k scale deployment/sentinelops-traffic --replicas=2
k rollout status deployment/sentinelops-traffic --timeout=180s
```

Kiểm tài khoản đã chuẩn bị (thay `nvtank` bằng username của người bấm nếu khác):

```bash
aws cognito-idp admin-get-user \
  --user-pool-id ap-southeast-1_JS1rLBJg3 --username nvtank \
  --query '{Enabled:Enabled,Status:UserStatus,MFA:UserMFASettingList}'
```

Cần `Enabled=true`, `Status=CONFIRMED`, MFA có `SOFTWARE_TOKEN_MFA`, và **thử login thật thành công**. Lệnh admin không thay thế bước enroll của người dùng.

### Console tùy chọn: chỉ đọc, mọi thao tác OFF

Nếu trình chiếu console, dùng port-forward sẵn có; chỉ mở lệnh sau ở terminal riêng nếu chưa có phiên trên cổng 8765:

```bash
AWS_PROFILE=console kubectl --context sentinelops-demo -n sentinelops-b-actions port-forward --address 127.0.0.1 deployment/fault-console 8765:8765
```

Mở **http://127.0.0.1:8765**, giữ nguyên loopback/port để khớp Host/Origin guard. Trong terminal điều khiển, kiểm GET duy nhất:

```bash
curl -fsS --noproxy '*' http://127.0.0.1:8765/api/status | jq -e '
  .enabled == false and .deployment.configured == false and
  .deployment.state == "idle" and .busy == false'
```

Cần `true`/exit 0; mọi nút gây lỗi, KHÔI PHỤC và deploy vẫn khóa. Nếu không đạt, dừng dùng console để kiểm tra, không tự đổi gate/runner. Trạng thái từng service phải là GET thật đúng tên; lỗi/404/timeout là chưa đọc được, không tự bật endpoint fault hoặc suy ra healthy. Fault `healthy` không xác minh image, telemetry hay toàn bộ replica. Không gửi POST để kiểm gate trong bước này. Nếu không dùng console, bỏ qua mục này và tiếp tục đầy đủ baseline Prometheus/Jaeger/controller bên dưới.

### Port-forward, readiness và baseline

Mỗi lệnh sau chạy ở **một terminal riêng**, giữ mở đến hết demo:

```bash
AWS_PROFILE=console kubectl --context sentinelops-demo -n sentinelops-b-actions port-forward --address 127.0.0.1 svc/prometheus 9090:9090
```

```bash
AWS_PROFILE=console kubectl --context sentinelops-demo -n sentinelops-b-actions port-forward --address 127.0.0.1 svc/jaeger 16686:16686
```

```bash
AWS_PROFILE=console kubectl --context sentinelops-demo -n sentinelops-b-actions port-forward --address 127.0.0.1 deployment/remediation-controller 8000:8000
```

Trong terminal điều khiển:

```bash
curl -fsS http://127.0.0.1:8000/ready | jq .
curl -fsS http://127.0.0.1:9090/-/ready
curl -fsS http://127.0.0.1:16686/api/v3/services | jq .

prom() {
  curl -fsS --get http://127.0.0.1:9090/api/v1/query \
    --data-urlencode "query=$1" | jq '{status, result: .data.result}'
}
prom 'sentinelops:http_request_rate:rate1m'
prom 'sentinelops:http_latency_p95_ms'
prom 'sentinelops:http_error_ratio:ratio5m'
prom 'sentinelops:error_budget_remaining:ratio6h'
prom 'sentinelops:slo_latency_breach'
prom 'sentinelops:slo_error_breach'
```

`/ready` trực tiếp cần HTTP 200, `incident_store`, `authentication`, `model` đều true. Các service phải có lưu lượng/dữ liệu mới; breach baseline bằng 0. Chờ các bucket khỏe được worker chấp nhận trước lượt đổi image; chỉ nhìn Pod Ready chưa đủ. Budget 6h có thể còn 0% từ lượt trước, nên ghi nhận baseline thật, không hứa mọi gauge sẽ xanh.

Kiểm policy/controller và image trước buổi; lưu image để đối chiếu sau demo:

```bash
k get deployment remediation-controller -o json | jq '
  .spec.template.spec.containers[] |
  select(.name == "remediation-controller") | {name, args}'
k exec deployment/remediation-controller -c remediation-controller -- \
  python -c 'from pathlib import Path; print("KILL_SWITCH=", Path("/artifacts/remediation/KILL_SWITCH").exists())'
k get deployment payment-service -o json | jq '{
  uid: .metadata.uid, generation: .metadata.generation,
  replicas: .spec.replicas, strategy: .spec.strategy,
  containers: [.spec.template.spec.containers[] | {name, image}]}'
DEMO_PAYMENT_IMAGE=$(k get deployment payment-service -o json | \
  jq -er '.spec.template.spec.containers[] | select(.name == "payment") | .image')
k logs deployment/sentinelops-health-worker --tail=15
k logs deployment/sentinelops-incident-worker --tail=15
```

Lượt duyệt cần controller có `--policy-enabled`, kill switch không bật, Payment 1 replica, known-good/model/stream/receipt hợp lệ. B7 ghi nhận policy bật lúc **09:58:36 UTC**, nhưng vẫn kiểm lại hiện tại. Image khỏe đã bàn giao là `833925774773.dkr.ecr.ap-southeast-1.amazonaws.com/sentinelops/application@sha256:e520e70255f4763de7c7ad4bb9053452e5b93ec0716fc5a5e1ea7a24caabb28c`. Nếu khác image/ledger đã review, dừng lượt đổi image để kiểm tra, không tự cập nhật ledger hoặc tắt guard.

### Chuẩn bị runner cho lượt proposal/Duyệt

Workspace vận hành có bộ **B9** tại `.herdr/out/deploy/`, mở rộng cách ghi receipt của B7 để **giữ image lỗi chờ duyệt**. Đây là bộ công cụ cục bộ đã chuẩn bị, không phải file được bảo đảm có trong một clone repo mới, và không phải bằng chứng B9 đã rollback thành công. B9-fix báo 90 offline PASS; hiện **từ chối fallback PATCH** do chưa có khóa chung với approval/controller. Timeout, Ctrl-C hoặc chưa có approval không bảo đảm tự khôi phục; SIGKILL/mất node chưa có watchdog độc lập. Owner **t43** cần chốt recovery/quyền rollback độc quyền và review trước lượt C; kiểm cú pháp PASS không mở gate chạy thật. Kiểm file/lỗi cú pháp mà chưa gây lỗi:

```bash
check_b9_files() {
  for f in b9_hold.sh b9_job.py b9_render.py b9_support.py b9_probe.py \
           b7_render.py b7_job.py b7_restore_patch.py b7-b5-template.json \
           b7-b5-review-configmap.json b7-configmap-run_runbook_demo.py; do
    if ! test -r ".herdr/out/deploy/$f"; then
      echo "THIEU: $f"
      return 1
    fi
  done
  bash -n .herdr/out/deploy/b9_hold.sh
}
check_b9_files
```

Nếu thiếu bộ B9 hoặc chưa hoàn tất review recovery/policy live, giữ baseline chỉ đọc và trình bày proposal B7 đã lưu; **không hứa có Duyệt live**. A/B cũng chỉ chạy khi được cho phép riêng. Không dùng `b7_run.sh` cũ cho lượt Duyệt: nó tự khôi phục image ngay sau `B7_EVIDENCE_PASS`, khiến target của proposal thay đổi. Không thay bằng `kubectl set image` đơn lẻ: đổi image không tự tạo deployment-change receipt.

## 2. Nhịp trình diễn và số đo được phép nói

Lời dẫn: “Chúng ta theo dõi từ lỗi thật, số liệu nghiệp vụ, trace, đến incident và quyết định xử lý. Số trên màn hình là của lượt hiện tại; số đối chiếu sau đây đã đo ngày 19/11.”

| Mốc tương đối | Điều phối chuyển màn hình / thao tác | Số đo thật và giới hạn |
| --- | --- | --- |
| Trước T0 | Chỉ baseline trên `sentinelops-slo`; on-call giữ tab UI | KB1: p95 Payment **5,74 ms**, Checkout **46,37 ms**, Inventory **4,83 ms**. |
| T0 | Operator bơm lỗi theo đúng một lượt A/B/C; ghi giờ UTC | Với C, T0 là lúc runner báo PATCH image, không phải lúc khởi động script. |
| Khoảng T+2 phút | Sang Grafana, so Payment và Checkout với Inventory | KB1: tiêm **02:04:55**, đo **02:06:57**, p95 Payment **963,82 ms (khoảng 963 ms)**, Checkout **968,08 ms (khoảng 968 ms)**; Inventory **4,83 ms**. |
| Trong lượt lỗi 503 | Chỉ error ratio, success rate và error budget | KB2 đo error ratio **5% → 13% → 29% → 45% → 61% → 78%**, Inventory **0%**. Error budget đã được operator báo cáo xuống **0%**; không có timestamp riêng được bàn giao. Không gán 78%/0% vào đúng phút thứ 2 hoặc vào lượt latency. |
| Khoảng T+5–6 phút | Quay lại UI, tải lại danh sách và mở incident mới | KB2 `PUBLISHED` sau **295,901 giây**; KB3 tạo incident sau **371,992 giây**. Đây là nhịp chờ để điều phối, không phải cam kết UI hiển thị đúng giây đó. KB1 có `PUBLISHED` khoảng **02:11–02:12**, tức khoảng 6–7 phút sau tiêm. |
| Sau incident/RCA, ở lượt C đủ bằng chứng | Mở **Đề xuất runbook**, báo “on-call sẵn sàng”, kiểm và duyệt ngay | B7 lần 1: PATCH **09:48:57.029**, incident **09:55:30.908** (khoảng 6 phút 34 giây), `RCA_READY` **09:55:37.573**, proposal **09:55:51.010** (khoảng 20 giây sau incident). Không suy rằng mỗi lượt sẽ giống hệt. |
| Sau Duyệt | Giữ UI/action và terminal controller; chờ xác minh | Chỉ kết luận tự khắc phục khi action **`recovered` + `telemetry_verified`**, image đúng known-good và có bằng chứng sức khỏe sau action. Receipt approval hoặc Ready 1/1 riêng lẻ chưa đủ. |

**Các số trên thuộc các lượt khác nhau.** KB1/KB2 dùng fault trong RAM, không có deployment change nên **không sinh proposal rollback**. B7 đổi image có recorder, capture v6/model/receipt và RCA không abstain mới sinh proposal; cả **2/2 lượt B7 có proposal nhưng chưa duyệt, chưa chạy rollback tự động** trong nguồn bàn giao. Giữ đúng phân biệt này khi thuyết trình.

## 3. Bơm lỗi: chọn đúng lượt

Các lệnh trong mục này dành cho phiên gây lỗi đã được owner cho phép sau baseline. Bản console đang deploy vẫn chỉ đọc; đọc runbook hoặc hoàn tất GET preflight không cấp phép bật fault/B7/B9.

### Lượt A — Latency 950 ms, Grafana → trace → incident

Khai báo hàm trong terminal điều khiển. Nó gọi API nội bộ trong Pod Payment, không mở endpoint fault ra Internet:

```bash
fault_payment() {
  k exec deployment/payment-service -c payment -- python -c '
import json, sys, urllib.request
body = json.dumps({"mode": sys.argv[1], "delay_ms": int(sys.argv[2])}).encode()
request = urllib.request.Request("http://127.0.0.1:8000/internal/faults",
    data=body, headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(request, timeout=10) as response:
    print(response.read().decode())
' "$1" "${2:-0}"
}

date -u +%Y-%m-%dT%H:%M:%SZ
fault_payment latency 950
```

Phải nhận JSON `mode=latency`, `delay_ms=950`. Nếu 404, fault injection chưa được bật cho môi trường demo: dừng lượt, không tự nới allowlist. Bắt đầu đồng hồ, làm theo bảng nhịp ở mục 2. Khoảng phút 2 xem p95; tranh thủ mục 4 trước khi incident xuất hiện. Khi UI có incident, xem affected services Payment/Checkout, severity, evidence và RCA. Lượt này **không chờ nút Duyệt**.

Kết thúc A bằng `fault_payment healthy 0`; giữ traffic, chờ worker ghi hồi phục và incident đóng rồi mới chuyển lượt khác.

### Lượt B — Payment 503, error ratio/budget → incident critical

Chỉ chạy sau khi A hồi phục; đặt lại đồng hồ:

```bash
date -u +%Y-%m-%dT%H:%M:%SZ
fault_payment error 0
```

Theo dõi error ratio Payment/Checkout tăng, success rate giảm và budget 6h. Số đo KB2 có breach đầu sau **19,246 giây**, incident sau khoảng **4,9 phút**; trace lỗi ghi Payment **503**, Checkout **502**. Không hứa tỷ lệ lỗi sẽ lên đúng 78% ở giây cố định. Budget 0% nghĩa là đã hết/vượt ngân sách của cửa sổ 6h, không phải bộ đếm chờ hồi phục.

Mở incident `critical`, giải thích lỗi Payment lan tới Checkout. Lượt này không có proposal rollback. Gỡ bằng `fault_payment healthy 0`, chờ khỏe/đóng incident trước lượt C.

### Lượt C — Đổi image có receipt → proposal → Duyệt

**Hiện còn pending, chưa chạy lượt C.** Chỉ dùng lệnh dưới sau khi owner cho phép riêng, đã review phương án recovery/quyền rollback độc quyền, xác minh policy live và mọi chuẩn bị mục 1 hoàn tất: cả ba service khỏe, không còn fault A/B, on-call đã đăng nhập và đứng sẵn. Trên máy vận hành có bộ B9 đã review, owner t43 chạy ở terminal riêng; không chạy trong pod console:

```bash
B9_EXECUTE=1 bash .herdr/out/deploy/b9_hold.sh 720
```

**Lệnh làm Payment chạy image lỗi thật.** `720` là hạn giữ/chờ tổng tính từ trước PATCH, **không phải 720 giây để duyệt sau khi proposal xuất hiện**. Runner mở baseline, PATCH có kiểm điều kiện và ghi receipt, giữ lỗi, in incident/proposal/revision khi thấy bằng chứng, rồi chờ approval. Giữ terminal chạy; các ID/ref do chính lượt này in ra là đầu vào cho bước duyệt. Không bơm thêm lỗi API trong lượt này.

Theo nhịp mục 2; B7 đã đo latency breach sau PATCH khoảng 2 phút 47 giây và proposal sau khoảng 6 phút 54 giây. Khi `B9_EVIDENCE_PASS`/`B9_WAIT_APPROVAL` xuất hiện:

1. Mở đúng incident mới trên UI, **Tải lại đề xuất**; đối chiếu ID và revision với terminal. Không dùng proposal B7 lịch sử.
2. Xem Payment đứng đầu RCA, không abstain, bằng chứng deployment change, runbook `payment-bad-deploy-rollback`, image hiện tại và image known-good. Điểm RCA B7 khoảng **0,797**, coverage **0,80** là số lịch sử; không đồng nhất điểm RCA với mọi ngưỡng matching/runbook.
3. Kiểm tuổi RCA như dưới; nhập **Lý do quyết định**: “Đã kiểm tra incident/revision, bằng chứng triển khai và hai image; đồng ý rollback về known-good”. Bấm **Duyệt một lần ngay khi kiểm xong**. Nếu nút khóa hoặc guard chặn, đọc lý do; không chỉnh evidence để mở nút.
4. Theo dõi controller/action. Nếu trình duyệt mất phản hồi sau click, kiểm receipt/action trước khi chuyển sang CLI để tránh ghi quyết định lần hai.

Với controller chạy policy mới 900 giây, đọc `as_of` từ **`result_ref`** do runner vừa in (ref thuộc store incident). Chép ref vào biến, không lấy `created_at` của proposal làm thay thế:

```bash
printf 'result_ref RCA cua luot moi: '
read -r RCA_REF
k exec deployment/sentinelops-incident-worker -- python -c '
import sys
from datetime import datetime, timezone, timedelta
from sentinelops.telemetry.store import ArtifactStore
rca = ArtifactStore("/artifacts/incidents").read(sys.argv[1])
as_of = datetime.fromisoformat(rca["as_of"].replace("Z", "+00:00"))
deadline = as_of + timedelta(seconds=900)
print("RCA.as_of:", rca["as_of"])
print("Han freshness UTC:", deadline.isoformat())
print("Con lai (giay):", round((deadline - datetime.now(timezone.utc)).total_seconds(), 1))
' "$RCA_REF"
```

**Không đợi gần hết 900 giây mới bấm**: controller còn cần poll và kiểm lại lúc execute. Nếu đã hết hạn, không duyệt proposal cũ dù còn TTL; kết thúc lượt, khôi phục rồi tạo incident/evidence/proposal mới bằng lượt mới. Tải lại UI không tự làm RCA mới hơn.

Nếu sau khoảng 8 phút chưa có incident/proposal, giữ nguyên hiện trạng để đọc log thay vì bơm thêm lỗi:

```bash
k logs deployment/sentinelops-health-worker --since=10m --tail=80
k logs deployment/sentinelops-incident-worker --since=10m --tail=80
k logs deployment/remediation-controller -c remediation-controller --since=10m --tail=80
```

Nếu RCA abstain/engineer hoặc source thiếu, trình bày đúng lý do và theo dõi runner kết thúc/xác minh recovery. Không cam kết mỗi lượt đều có proposal. B9 hết chờ sẽ dừng Job và kiểm image/Ready; **không tự PATCH khôi phục kể cả chưa có approval**. Chưa xác minh hồi phục thì báo URGENT và exit khác 0. Đã có approval thì theo dõi controller, không tự tranh PATCH. Nếu action bị blocked/escalated hoặc chưa hồi phục, chuyển xử lý vận hành theo mục 7, không gọi đó là demo tự sửa thành công.

## 4. Jaeger: tìm và giải thích cùng một trace

Mở `http://127.0.0.1:16686`, Search service **`checkout-service`**, Operation **`checkout-service POST`**, Last 15 minutes, Find Traces; chọn trace chậm sau T0. Trong waterfall, mở span Checkout và span **`payment-service POST`** cùng trace ID, so thời gian và quan hệ gọi xuống Payment. Nếu giao diện tìm kiếm gọi API v1 rồi lỗi, dùng API v3 dưới đây và mở `http://127.0.0.1:16686/trace/<trace-id>`.

Số đo operator bàn giao: trong **cùng một trace**, `payment-service POST` **966 ms**, `checkout-service POST` **989 ms**; bình thường Payment **3,3–10,8 ms**, Checkout khoảng **30 ms**. Đây là thời gian **span đơn lẻ**, khác p95 của histogram 5 phút. Lời dẫn: “Checkout chờ lời gọi Payment chậm; cùng trace cho thấy đường lan truyền, còn Grafana cho thấy ảnh hưởng trên nhiều request.” Không cộng các span lồng nhau hoặc gán trace ID chưa được bàn giao cho cặp số này.

**Dùng `/api/v3`; API v1 (`/api/traces`, `/api/services`) đã trả 404 ở triển khai demo.** Bộ query của triển khai Jaeger 2.21 dùng tên `query.service_name`, `query.operation_name`, `query.start_time_min`, `query.start_time_max`, `query.num_traces`. Hai mốc phải là **RFC3339 UTC**, ví dụ `2026-11-19T02:04:55Z`, không phải epoch microsecond của API v1. Định dạng thời gian được mô tả trong [hợp đồng Jaeger API v3](https://github.com/jaegertracing/jaeger-idl/blob/main/proto/api_v3/query_service.proto).

Lấy cửa sổ 15 phút hiện tại, tìm trace Checkout để có cả Payment trong cùng đường gọi:

```bash
TRACE_START=$(python3 -c 'from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)-timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ"))')
TRACE_END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
JAEGER_RESULT=$(curl -fsS --get http://127.0.0.1:16686/api/v3/traces \
  --data-urlencode 'query.service_name=checkout-service' \
  --data-urlencode 'query.operation_name=checkout-service POST' \
  --data-urlencode "query.start_time_min=$TRACE_START" \
  --data-urlencode "query.start_time_max=$TRACE_END" \
  --data-urlencode 'query.num_traces=20')

printf '%s\n' "$JAEGER_RESULT" | python3 -c '
import json, sys
rows = []
for line in sys.stdin:
    if not line.strip(): continue
    frame = json.loads(line)
    if "error" in frame: raise SystemExit(frame["error"])
    for resource in frame.get("result", {}).get("resourceSpans", []):
        for scope in resource.get("scopeSpans", []):
            for span in scope.get("spans", []):
                if span.get("name") in ("payment-service POST", "checkout-service POST"):
                    ms = (int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"])) / 1_000_000
                    rows.append((span["traceId"], span["name"], ms))
for trace_id, name, ms in sorted(rows):
    print(trace_id, name, f"{ms:.3f} ms")
'
```

Chọn hai dòng có **cùng trace ID** sau T0, thời gian gần 1 giây; nếu 20 trace chưa chứa lượt chậm, thu hẹp `TRACE_START` về T0 rồi tìm lại. Lấy toàn bộ một trace bằng ID vừa nhận:

```bash
printf 'Trace ID vua tim: '
read -r TRACE_ID
curl -fsS "http://127.0.0.1:16686/api/v3/traces/$TRACE_ID" | jq .
```

Response v3 có thể là NDJSON/OTLP (`result.resourceSpans`), không phải cấu trúc `data[].spans` của v1. Một phản hồi v3 JSON **`No traces found`** có thể cũng mang HTTP 404: kiểm cửa sổ/service, không kết luận v3 không tồn tại. Trace/lịch sử cũ có thể đã mất; nếu không tìm lại được cặp 966/989 ms, đọc là số đo lịch sử và trình chiếu trace mới thực nhận.

## 5. Dự phòng: không đăng nhập được thì on-call duyệt bằng CLI

Chọn CLI **trước T0** nếu login/MFA không hoạt động, để không mất cửa sổ freshness 900 giây của policy mới. CLI cần quyền `kubectl exec` của operator, ghi receipt vào **đúng store controller**; không cần lấy bearer token Cognito. Người duyệt vẫn phải đọc proposal/bằng chứng và nhận trách nhiệm quyết định.

Chuẩn bị danh tính trước lượt:

```bash
printf 'Username on-call dang truc tiep duyet (khong dung email): '
read -r ONCALL
test -n "$ONCALL"
```

Khi runner in proposal mới, nhập ID, đọc proposal và hạn TTL; dùng `result_ref`/lệnh mục 3 để kiểm **RCA.as_of + 900 giây** với policy mới:

```bash
printf 'Proposal ID cua luot moi: '
read -r PROPOSAL_ID
k exec deployment/remediation-controller -c remediation-controller -- python -c '
import json, sys
from sentinelops.remediation.approvals import find_proposal
from sentinelops.telemetry.store import ArtifactStore
print(json.dumps(find_proposal(ArtifactStore("/artifacts/remediation"), sys.argv[1]), indent=2))
' "$PROPOSAL_ID"
```

Sau khi kiểm đúng incident/revision, target, hai image và freshness, người on-call chạy **một lần**:

```bash
k exec deployment/remediation-controller -c remediation-controller -- \
  python scripts/approve_runbook.py --store /artifacts/remediation \
  --proposal-id "$PROPOSAL_ID" --approved-by "$ONCALL" --decision approve \
  --reason 'Đã kiểm tra incident revision, bằng chứng triển khai và image known-good' \
  --confirm-oncall-approval
```

Kết quả `runbook_approval`/`approval_ref` chứng minh đã ghi quyết định, **chưa chứng minh rollback**. CLI không bỏ qua policy, H4, target/revision hay freshness. Nếu RCA/health/Deployment observation quá 900 giây với policy mới, có thể vẫn ghi receipt trong TTL 15 phút nhưng execute bị chặn. Không chạy lệnh lần hai khi mất reply; kiểm các slot đã ghi:

```bash
k exec deployment/remediation-controller -c remediation-controller -- python -c '
import json, sys
from sentinelops.remediation.approvals import read_slot
from sentinelops.telemetry.store import ArtifactStore
store = ArtifactStore("/artifacts/remediation")
for kind in ("approval", "used", "outcome"):
    print(kind, json.dumps(read_slot(store, kind, sys.argv[1])))
' "$PROPOSAL_ID"
```

## 6. Những gì sẽ thấy — và những gì sẽ không thấy

| Trên màn hình | Điều phối nói gì / xử lý gì |
| --- | --- |
| `sentinelops-slo`: p95 tăng, success giảm khi có 503, budget có thể về 0% | Số live thuộc cửa sổ đang chọn. Budget 6h hồi phục chậm hơn lỗi hiện tại; p95 963–968 ms vẫn có thể màu vàng do gauge SLO ngưỡng 1000 ms, dù health breach đã vượt 750 ms. |
| `sentinelops-workers` trống | **Hạn chế đã biết: worker chưa export metric.** Không dùng dashboard trống để kết luận worker chết/khỏe. Kiểm log worker, incident và readiness trực tiếp. Stat incident/RCA dùng cùng nguồn cũng có thể trống. |
| Hai chấm **Incident store / Model readiness = `Unknown`** trên UI public | **Hạn chế đã biết: edge CloudFront chặn `/ready` và `/ready/incidents`**, trả 403; không phải bằng chứng hai dependency đang hỏng. Trình chiếu JSON `/ready` qua port-forward để đối chiếu. Không mất thời gian sửa edge trong demo. |
| Incident đến sau Grafana vài phút | Metric cửa sổ trượt lên trước; worker cần bucket/coverage/evidence rồi mới publish incident. Không refresh liên tục và nói hệ thống “không phát hiện”. |
| Lượt fault API không có proposal; lượt C có thể bị engineer/abstain | Đó là điều kiện bằng chứng/authority, không mặc định lỗi UI. Proposal chỉ xuất hiện khi đủ gate; không tự động rollback ngay khi có proposal. |
| Duyệt thành công nhưng action bị chặn | Đọc reason, nhất là `approval-after-evidence-freshness`; TTL 15 phút không gia hạn RCA. Không công bố đã rollback chỉ vì có receipt. |

## 7. Dọn sau demo và xác nhận hồi phục

1. **Nếu phiên được phép đã chạy fault A/B**, gỡ kể cả demo bị ngắt: `fault_payment healthy 0`. Nếu chỉ xem baseline/console chỉ đọc, không gửi POST gỡ lỗi. Nếu lỗi xảy ra ở terminal trước khi khai báo hàm, chạy lại phần khai báo ở mục 3 rồi gỡ. Với lượt C, theo dõi runner/controller và recovery; fault `healthy` không gỡ latency được đóng trong image regression.
2. **Nếu đã chạy B9:** kết thúc bình thường/Ctrl-C chỉ dừng Job và xác minh image gốc/UID, rollout/Ready; không bảo đảm khôi phục, kể cả chưa có approval. B9-fix không fallback PATCH; nếu chưa hồi phục sẽ báo URGENT và exit khác 0. Kiểm proposal/approval/action/outcome/journal theo [Controlled remediation](CONTROLLED_REMEDIATION.md). Owner phải xác nhận Job/pod đã dừng, không còn action đang thực thi, giành quyền rollback độc quyền rồi mới khôi phục thủ công bằng snapshot/CAS đã review và xác minh image gốc Ready cùng telemetry; ghi đúng là khôi phục thủ công. Đã có approval thì theo dõi controller, không chạy `set image`/`rollout undo` song song. Không bỏ buổi khi Payment vẫn lỗi hoặc quyền điều khiển chưa rõ; SIGKILL/mất node không được trap bảo đảm.
3. Kiểm image, rollout và số liệu; giữ **traffic 2 replica** đến khi có đủ bằng chứng hồi phục:

```bash
k rollout status deployment/payment-service --timeout=180s
k get deployment payment-service -o json | jq '{
  ready: .status.readyReplicas, generation: .metadata.generation,
  image: [.spec.template.spec.containers[] | select(.name == "payment") | .image]}'
printf 'Image baseline: %s\n' "$DEMO_PAYMENT_IMAGE"
prom 'sentinelops:http_latency_p95_ms'
prom 'sentinelops:http_error_ratio:ratio5m'
prom 'sentinelops:slo_latency_breach'
prom 'sentinelops:slo_error_breach'
k logs deployment/sentinelops-health-worker --since=10m --tail=50
k logs deployment/remediation-controller -c remediation-controller --since=10m --tail=80
k get deployments
```

4. Chờ **3 bucket khỏe/eligible** được chấp nhận sau action; verifier chỉ dùng telemetry trong **600 giây từ `effective_at`**, còn executor có **1200 giây từ dispatch** để hoàn tất source read và ghi outcome. Kiểm incident hồi phục/đóng, action `recovered` và `telemetry_verified` nếu đã thực thi. Phân biệt image Ready, hết 5xx, SLO theo cửa sổ và kết quả verifier; budget 6h không bắt buộc trở lại 100% ngay.
5. Ghi lại giờ T0/gỡ lỗi, incident ID/revision, proposal ID, `RCA.as_of`, approval/outcome/action, image cuối và ảnh Grafana/trace của lượt vừa chạy. Giữ artifact, log, PVC và lịch sử; không xóa receipt/checkpoint để làm sạch demo.
6. Khi đã xác nhận hồi phục, Ctrl-C **đúng các terminal port-forward** đã mở, logout UI/Grafana trên máy trình chiếu. Giữ traffic ở baseline **2 replica**, không scale về 0 theo thói quen; không tắt policy hay teardown cluster như một bước dọn màn hình.

## Nguồn và phạm vi bằng chứng

- [DEMO_SCENARIOS_2026-11-19](../delivery/DEMO_SCENARIOS_2026-11-19.md): timeline KB1/KB2/KB3, p95, error ratio, B7 lặp lại 2/2, các proposal chưa duyệt và giới hạn rollback.
- [HANDOVER_2026-11-19](../delivery/HANDOVER_2026-11-19.md): truy cập, dashboard, worker metric trống, `/ready` bị edge chặn, known-good và các snapshot hạ tầng. Ghi chú policy tắt ở snapshot sớm đã được mốc B7 cập nhật; luôn kiểm live trước buổi.
- [EPOCH_MIGRATION_2026-11-19](../delivery/EPOCH_MIGRATION_2026-11-19.md): stream/model/receipt B7, yêu cầu recorder khi đổi image.
- [AWS_DEMO_ACCESS_AND_FLOWS](../delivery/AWS_DEMO_ACCESS_AND_FLOWS.md): CLI duyệt, receipt, TTL/freshness và verification. Đối chiếu mã: [approve_runbook.py](../../scripts/approve_runbook.py), [Policy cố định freshness](../../src/sentinelops/remediation/domain.py), [kiểm RCA.as_of lúc execute](../../src/sentinelops/remediation/authority.py), [controller chuyển reason khi evidence cũ](../../src/sentinelops/remediation/controller.py).
- [FAULT_CONSOLE_DEPLOY_2026-11-19](../delivery/FAULT_CONSOLE_DEPLOY_2026-11-19.md): receipt rollout chỉ đọc, digest, GET thật và đối chiếu 12 workload cũ; xem thêm [Console README](../../scripts/demo_console/README.md) và [package deployment](../../scripts/demo_console/deployment/README.md).
- Cặp span **966/989 ms**, baseline span **3,3–10,8/30 ms**, budget **0%** và yêu cầu kiểm **12 Deployment cũ/traffic 2 replica** là số liệu/điều kiện operator cung cấp trực tiếp cho runbook này. Console tùy chọn làm tổng tăng lên 13, không đổi baseline cũ. Không có trace ID hoặc timestamp riêng cho các số span/budget trong tài liệu delivery đã đọc, nên không tự bổ sung. Bộ B9 là hướng dẫn dùng công cụ chuẩn bị cục bộ, tách khỏi số đo B7 đã chạy thật.
