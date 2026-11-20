# Tổng hợp triển khai AWS — chốt 18/11/2026

> **HAI MỐC BẰNG CHỨNG:** phần đối chiếu offline cũ chốt tại `main`, commit `0afb06d`, ngày 18/11/2026; phần **[LIVE] mới** ghi lại xác minh trực tiếp trên AWS/EKS trong phiên 18/11/2026, với image build từ `main` tại commit `b4b416f`.
> Phiên live dùng AWS CLI profile `console`, account `833925774773`, region `ap-southeast-1`, kube context `sentinelops-demo`; lệnh/endpoint kiểm tra được ghi ở từng mục, gồm `aws eks describe-cluster`, `kubectl get deployment,pods` và các HTTP endpoint qua port-forward.
> Lần cập nhật tài liệu này tổng hợp kết quả phiên live đã xác minh; không coi báo cáo lịch sử hoặc manifest là trạng thái live. Không ghi giá trị bí mật; với Secret chỉ nêu tên tài nguyên và tên key.

## 1. Khung đánh giá

Mốc đối chiếu offline: **main, commit `0afb06d`, ngày 18/11/2026**, region triển khai được báo cáo là `ap-southeast-1` (`docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:3`; commit đối chiếu theo mốc bàn giao cũ). Mốc bổ sung **[LIVE]: phiên 18/11/2026**, kiểm EKS bằng `aws eks describe-cluster --name sentinelops-dev`, workload bằng `kubectl get deployment,pods`, image mới bằng `aws ecr describe-images --repository-name sentinelops/control-plane --image-ids imageTag=git-b4b416f`.

| Mức | Ý nghĩa | Giới hạn |
|---|---|---|
| [A] | Có báo cáo/artifact ghi đã apply hoặc chạy trên AWS | Bằng chứng lịch sử; không phải kiểm tra live hiện tại |
| [B] | Có IaC/manifest nhưng không có bằng chứng apply thành công | Không khẳng định dịch vụ hoạt động |
| [C] | Chỉ mã, tooling, kiểm thử hoặc local | Không phải bằng chứng triển khai AWS |
| [LIVE] | Đã xác minh trực tiếp trên AWS/cluster ngày 18/11/2026 bằng lệnh/endpoint đi kèm | Mạnh hơn [A] vì [A] chỉ là báo cáo lịch sử; chỉ chứng minh đúng phạm vi đã kiểm tại thời điểm đó |

Giữ hệ [A/B/C] cho phần đối chiếu source và báo cáo trong `docs/delivery`; bổ sung [LIVE] tại các kết quả vừa kiểm trực tiếp. Trong bằng chứng live bên dưới, `kubectl` dùng context `sentinelops-demo`, mặc định namespace `sentinelops-b-actions`; AWS CLI dùng profile/region nêu ở đầu tài liệu. Kết quả HTTP là qua port-forward, không tự chứng minh đường public (live 18/11/2026: `GET /ui/`, `GET /grafana/api/health`; `aws cloudfront list-distributions` cho thấy `Enabled=false`).

**Cảnh báo tên:** Terraform mặc định `name_prefix=sentinelops`, `environment=prod`; đợt deploy được báo cáo dùng **dev** (`infra/aws/variables.tf:21`, `infra/aws/variables.tf:32`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:9`).
Vì vậy `sentinelops-prod`, `sentinelops-control-prod`, `alias/sentinelops-prod` là tên suy từ defaults, không phải tên live đã kiểm (`infra/aws/variables.tf:21`, `infra/aws/variables.tf:32`).
Tên được báo cáo là cluster `sentinelops-dev`, RDS `sentinelops-control-dev`, secret `sentinelops/dev/control-runtime` (`infra/aws/variables.tf:21`, `infra/aws/variables.tf:32`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:9`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:12`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:88`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:105`).

## 2. TÓM TẮT NHANH

| Câu hỏi chủ dự án | Trả lời dứt khoát theo bằng chứng | Mức | Bằng chứng path:line hoặc live |
|---|---|---|---|
| UI đã deploy chưa? | **Đã deploy và sidecar chạy thật** trong pod controller **2/2 Running, 0 restart**. Đăng nhập chưa hoàn tất: còn đặt password permanent và enroll MFA; hiện chưa có incident. Policy tắt nên bấm Duyệt chưa chạy rollback; CloudFront tắt nên chưa có URL public dùng được. | [LIVE] | Live 18/11/2026: `kubectl get pods`; `GET /ui/` → 200, `/health` → ok, `/v1/incidents` không token → 401; log controller → `[]`; `kubectl get deploy remediation-controller` kiểm args; `aws cognito-idp admin-get-user`; `aws cloudfront list-distributions` |
| Prometheus đã deploy chưa? | **Đã deploy, đang sẵn sàng và có metric thật.** Target `otel-collector` up; đã thấy 13 tên metric, gồm request/error/latency của SentinelOps. | [LIVE] | Live 18/11/2026: `GET /-/ready` → `Prometheus Server is Ready.`, `GET /api/v1/targets`, `GET /api/v1/label/__name__/values`; scrape `http://otel-collector:8889/metrics` |
| Grafana đã deploy chưa? | **Đã deploy mới trong phiên, READY 1/1**, health DB ok, bản 12.2.0, có datasource/dashboard. Truy cập quản trị qua loopback; OAuth Cognito chưa bật, dữ liệu `emptyDir` mất khi thay Pod. | [LIVE] | Live 18/11/2026: `kubectl rollout status deployment/grafana`; `GET /grafana/api/health`, `/grafana/login` → 200; `kubectl get configmap grafana-provisioning grafana-dashboards`; `kubectl get deploy grafana` kiểm env/volume |

## 3. Hạ tầng AWS có bằng chứng đã apply

Báo cáo lịch sử ghi **77/78 tài nguyên đã lên**; **CloudWatch dashboard là thành phần duy nhất apply lỗi trong đợt 78 tài nguyên** (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:136`).
Điều này không nâng các thành phần opt-in hoặc từng cấu hình riêng lên [A] (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`, `infra/aws/dashboard.tf:5`, `infra/aws/dashboard.tf:72`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:136`).

| Thành phần | Kết quả lịch sử và live | Mức và bằng chứng path:line hoặc live |
|---|---|---|
| EKS | `sentinelops-dev` ACTIVE, Kubernetes `v1.34.12-eks-9adbfea`; 2 node Ready `ip-10-60-28-33`, `ip-10-60-4-57`. Loại máy `m7i-flex.large` là thông tin báo cáo lịch sử | [LIVE] 18/11/2026: `aws eks describe-cluster --name sentinelops-dev`, `kubectl get nodes`; [A] loại máy: `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10` |
| RDS | `sentinelops-control-dev` tồn tại thật. Cấu hình PostgreSQL 16.15, `db.t4g.micro`, private, single-AZ, 20 GiB, backup 1 ngày là báo cáo lịch sử | [LIVE] 18/11/2026: `aws rds describe-db-instances --db-instance-identifier sentinelops-control-dev`; [A] cấu hình: `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:12`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:64` |
| ECR | Hai repo `sentinelops/application`, `sentinelops/control-plane` tồn tại; đã push image control-plane `git-b4b416f`. Báo cáo cũ ghi payment-regression dùng chung repo application | [LIVE] 18/11/2026: `aws ecr describe-repositories`, `aws ecr describe-images --repository-name sentinelops/control-plane --image-ids imageTag=git-b4b416f`; [A] regression: `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:75` |
| S3 | Bucket `sentinelops-artifacts-dev-833925774773` có bản backup `terraform-state/terraform.tfstate`; bản backup đã cũ so với thay đổi live, xem mục 10 | [LIVE] 18/11/2026: `aws s3api head-object --bucket sentinelops-artifacts-dev-833925774773 --key terraform-state/terraform.tfstate`; plan với backup trong mục 10 |
| Secrets Manager | `sentinelops/dev/control-runtime` có AWSCURRENT; bootstrap RDS 001–006, runtime `control_writer`, K8s secret `control-runtime` được báo cáo | [A] `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:88` |
| KMS | Có bằng chứng apply cấp KMS; ARN/key alias dev thực tế: **Không có bằng chứng**; không suy policy hiện tại từ IaC | [A] cấp dịch vụ; `infra/aws/eks.tf:87`, `infra/aws/eks.tf:96`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`; lấy key từ state `aws_kms_key.main` |
| VPC/NAT | VPC và 2 NAT được báo cáo đã apply; IaC có public/private subnet, IGW, routes; không xác nhận riêng từng resource phụ | [A] VPC/NAT; phần phụ [B]; `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:64`, `infra/aws/network.tf:16`, `infra/aws/network.tf:30`, `infra/aws/network.tf:46`, `infra/aws/network.tf:67`, `infra/aws/network.tf:123` |
| IAM | Có bằng chứng cấp dịch vụ; chưa có bằng chứng riêng từng role/policy, Pod Identity, EBS CSI | [A] tổng hợp / [B] chi tiết; `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`, `infra/aws/iam.tf:43`, `infra/aws/eks.tf:79`, `infra/aws/ebs_csi.tf:31` |
| CloudWatch Logs | Đợt apply tổng hợp bao gồm Logs; không có artifact kiểm riêng từng group hoặc xác nhận dữ liệu nhận được | [A] tổng hợp / [B] từng group; `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`, `infra/aws/observability.tf:5`, `infra/aws/observability.tf:13`, `infra/aws/observability.tf:21`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:64` |
| Budgets | Budget IaC được báo cáo; có thêm budget CLI toàn account 80 USD ngoài IaC; default IaC 300 USD, emails `[]` | [A] `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:68`, `infra/aws/variables.tf:266`, `infra/aws/variables.tf:277` |
| CloudWatch dashboard | Lỗi “Should NOT have more than 4 items”; sửa local **chưa apply** | [B] `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:136`, `infra/aws/dashboard.tf:72` |

RDS defaults là 16.10 / `db.t4g.medium` / 50 GiB / Multi-AZ / backup 7 ngày; node mặc định `t3.large`, khác cấu hình đợt dev đã báo cáo (`infra/aws/eks.tf:1`, `infra/aws/eks.tf:48`, `infra/aws/eks.tf:57`, `infra/aws/variables.tf:65`, `infra/aws/variables.tf:76`, `infra/aws/variables.tf:87`, `infra/aws/variables.tf:98`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`, `infra/aws/storage.tf:86`, `infra/aws/storage.tf:105`, `infra/aws/storage.tf:118`, `infra/aws/storage.tf:128`, `infra/aws/storage.tf:130`, `infra/aws/variables.tf:160`, `infra/aws/variables.tf:177`, `infra/aws/variables.tf:183`, `infra/aws/variables.tf:195`, `infra/aws/variables.tf:206`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:59`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:64`).
Endpoint EKS public `/32` + private và access entry cluster-admin CLI được báo cáo đã tạo; default IaC lại `cluster_public_access=false`. (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:60`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:61`, `infra/aws/variables.tf:109`).
Báo cáo cũng ghi đợt apply diễn ra khi reviewer còn BLOCK; đây là giới hạn quản trị của bằng chứng lịch sử (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:60`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:68`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:99`, `infra/aws/variables.tf:109`, `infra/aws/variables.tf:239`, `infra/aws/eks.tf:6`, `infra/aws/iam.tf:136`, `infra/aws/iam.tf:145`, `infra/aws/iam.tf:158`, `infra/aws/outputs.tf:107`).

## 4. Cognito, CloudFront và phần còn thiếu bằng chứng apply

**Sửa kết luận cũ:** Cognito và CloudFront đã được apply từ trước, pool và distribution tồn tại thật. Không dùng default `false` của IaC để kết luận chưa apply. Distribution tồn tại nhưng **`Enabled=false`**, nên chưa có URL ứng dụng public dùng được (live 18/11/2026: `aws cognito-idp describe-user-pool --user-pool-id ap-southeast-1_JS1rLBJg3`, `aws cloudfront list-distributions`; default: `infra/aws/operator_identity.tf:10`, `infra/aws/variables.tf:283`).

| Thành phần | Flag / default hoặc điều kiện | Kết luận và bằng chứng path:line hoặc live |
|---|---|---|
| CloudFront edge / VPC origin | Domain `dr76usfhbe2w8.cloudfront.net`, Status `Deployed`, **Enabled=false**; cấu hình trong state đã kiểm: `edge_enabled=true`, `edge_vpc_origin_bootstrap=true` | [LIVE] 18/11/2026: `aws cloudfront list-distributions`, `aws cloudfront list-vpc-origins`; bootstrap giữ distribution disabled: `infra/aws/edge.tf:242` |
| ALB internal / đường origin | ALB internal và VPC origin đã tạo thật; chưa chứng minh đường CloudFront → UI/Grafana hoạt động vì distribution tắt | [LIVE] 18/11/2026: `aws elbv2 describe-load-balancers`, `aws cloudfront list-vpc-origins`, `aws cloudfront list-distributions`; wiring thiết kế: `infra/aws/edge.tf:20`, `infra/aws/edge.tf:42`, `infra/aws/edge.tf:103` |
| WAF REGIONAL gắn ALB | `edge_waf_enabled=false`; cần cả edge | [B] Chưa có bằng chứng apply; `infra/aws/variables.tf:283`, `infra/aws/variables.tf:295`, `infra/aws/edge.tf:306` |
| Cognito pool/domain/operator client | Pool `ap-southeast-1_JS1rLBJg3`, tên `sentinelops-dev-operators`, MFA **ON**; hosted domain `sentinelops-dev-833925774773-operators`; operator client `216lcjofak9930v4q8ljuu7nff`, Grafana client `48tmlc7u0su8coconaojdjhr74` | [LIVE] 18/11/2026: `aws cognito-idp describe-user-pool`, `aws cognito-idp describe-user-pool-client` trên pool/client nêu trong hàng; đây là ID tài nguyên, không phải client secret |
| X-Ray sampling rule | Không có flag enable trong resource; `xray_fixed_sampling_rate=0.1` mặc định | [B] Có IaC nhưng chưa có bằng chứng apply riêng hoặc trace delivery; `infra/aws/observability.tf:31`, `infra/aws/variables.tf:255`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:17` |
| PVC gp3 + KMS | Không có flag Terraform; manifest StorageClass + 5 PVC áp dụng/di trú riêng; `kmsKeyId` còn placeholder, request mỗi PVC 5 GiB | [B] Chưa di trú cloud; `infra/kubernetes/sentinelops-gp3-kms.storageclass.yaml:13`, `scripts/aws_demo/artifact-pvcs-kms.yaml:16`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:140` |

PVC `health-artifacts`, `incident-artifacts`, `review-artifacts`, `deployment-change-artifacts` đã **Bound**, StorageClass `gp2`; `remediation-artifacts` trước khi có controller còn Pending vì chưa có Pod dùng (live 18/11/2026: `kubectl get pvc`, `kubectl describe pvc remediation-artifacts`). Tên StorageClass không chứng minh EBS encrypted/KMS; di trú KMS vẫn chưa có bằng chứng hoàn tất (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:50`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:140`).

## 5. Workload trên EKS

`b-actions` trong bảng là namespace **`sentinelops-b-actions`**; namespace `sentinelops` là riêng (`scripts/render_worker_manifests.py:31`, `scripts/render_api_manifests.py:18`).
D = Deployment, S = Service. Cổng ghi theo manifest trừ nơi có kiểm HTTP live; S testbed là ClusterIP (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:83`, `scripts/render_worker_manifests.py:31`, `scripts/render_business_manifests.py:21`, `scripts/render_collector_manifests.py:18`, `scripts/kubernetes_testbed.py:88`, `scripts/kubernetes_testbed.py:20`). Các hàng [LIVE] dưới đây là kiểm ngày 18/11/2026 trong namespace b-actions bằng `kubectl get deployment,pods,services` và endpoint đi kèm.

| Thành phần | Namespace | Loại | Cổng manifest | Mức / bằng chứng path:line hoặc live |
|---|---|---|---|---|
| checkout-service | b-actions | D+S | 8000 | [LIVE] 18/11/2026: `kubectl get deploy checkout-service` → READY 1/1; cổng: `scripts/kubernetes_testbed.py:130` |
| payment-service | b-actions | D+S | 8000 | [LIVE] 18/11/2026: `kubectl get deploy payment-service` → READY 1/1; cổng: `scripts/kubernetes_testbed.py:130` |
| inventory-service | b-actions | D+S | 8000 | [LIVE] 18/11/2026: `kubectl get deploy inventory-service` → READY 1/1; cổng: `scripts/kubernetes_testbed.py:130` |
| postgres thương mại | b-actions | D+S | 5432 | [LIVE] 18/11/2026: `kubectl get deploy postgres` → READY 1/1; cổng: `scripts/kubernetes_testbed.py:93` |
| otel-collector nội bộ | b-actions | D+S | 13133, 4317, 4318, 8889 | [LIVE] 18/11/2026: `kubectl get deploy otel-collector` → READY 1/1; Prometheus `GET /api/v1/targets` → up; cổng: `scripts/kubernetes_testbed.py:100` |
| prometheus | b-actions | D+S | 9090 | [LIVE] 18/11/2026: `kubectl get deploy prometheus` → READY 1/1; `GET /-/ready` → ready; cổng: `scripts/kubernetes_testbed.py:104` |
| jaeger | b-actions | D+S | 16686, 4317 | [LIVE] 18/11/2026: `kubectl get deploy jaeger` → READY 1/1; cổng: `scripts/kubernetes_testbed.py:99` |
| sentinelops-traffic | b-actions | D | Không khai báo cổng/S | [LIVE] 18/11/2026: `kubectl get deploy sentinelops-traffic` → READY 1/1; manifest: `scripts/aws_demo/render_all.py:47` |
| sentinelops-health-worker | b-actions | D | Không cổng/S; probe exec | [LIVE] 18/11/2026: `kubectl get deploy sentinelops-health-worker` → READY 1/1; probe: `scripts/render_worker_manifests.py:213` |
| sentinelops-incident-worker | b-actions | D | Không cổng/S; probe exec | [LIVE] 18/11/2026: `kubectl get deploy sentinelops-incident-worker` → READY 1/1; [A] `actions_allowed=false`: `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:90` |
| remediation-controller | b-actions | D; 2 container | Controller không khai báo cổng | [LIVE] 18/11/2026: `kubectl get pods -l app=remediation-controller` → **2/2 Running, 0 restart**; `kubectl logs deployment/remediation-controller -c remediation-controller` → `[]`; policy tắt, xem mục 9 |
| operator-ui API sidecar | b-actions | Container trong D controller; chung PVC `remediation-artifacts` | 8000 | [LIVE] 18/11/2026: `kubectl get deploy remediation-controller`; `GET /ui/` → 200, `/health` → ok, `/v1/incidents` không token → 401; mount: `scripts/render_worker_manifests.py:366` |
| sentinelops-api phục vụ sidecar | b-actions | **S + ConfigMap `sentinelops-api-runtime`**, chọn pod controller; không phải D độc lập | 8000 | [LIVE] 18/11/2026: `kubectl get svc sentinelops-api`, `kubectl get configmap sentinelops-api-runtime`; cơ chế selector/sidecar: `scripts/render_api_manifests.py:81`, `scripts/render_api_manifests.py:85` |
| sentinelops-api độc lập | sentinelops | D+S | 8000, ClusterIP mặc định | [B] `scripts/render_api_manifests.py:18`, `scripts/render_api_manifests.py:44`, `scripts/render_api_manifests.py:81`, `scripts/render_api_manifests.py:109`, `scripts/render_api_manifests.py:123` |
| otel-collector AWS | sentinelops | D+S | Container 4317/4318/13133; S 4317/4318 | [B] `scripts/render_collector_manifests.py:18`, `scripts/render_collector_manifests.py:114`, `scripts/render_collector_manifests.py:136`, `scripts/render_collector_manifests.py:152` |
| grafana | b-actions | D+S ClusterIP; dữ liệu `emptyDir` | 3000 | [LIVE] 18/11/2026: `kubectl rollout status deployment/grafana`, `kubectl get deploy,svc grafana` → READY 1/1; `GET /grafana/api/health` → DB ok, 12.2.0; `kubectl get deploy grafana` kiểm volume |
| Bootstrap 6 migration + runtime | b-actions theo template | Jobs; tên thực không được ghi | Không cổng/S | [A] Lịch sử chạy; `scripts/aws_demo/render_all.py:115`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:88`, `scripts/aws_demo/control-job.yaml:4`, `scripts/aws_demo/control-job.yaml:15`, `scripts/aws_demo/control.py:74`, `scripts/aws_demo/control.py:86`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:164` |
| action-runtime | b-actions theo template | Job qua control tooling | Không cổng/S | [B] Chưa xác minh riêng Job/role; không tiếp tục kết luận runtime chưa có chỉ từ checklist cũ. Secret `remediation-runtime` đã có, controller đã poll được [LIVE] 18/11/2026: `kubectl get secret remediation-runtime`, `kubectl logs deployment/remediation-controller -c remediation-controller`; tooling: `scripts/aws_demo/control.py:86` |
| review / approve | b-actions | Job; template dùng tên review | Không cổng/S | [B] Template: `scripts/aws_demo/review_job.py:54`, `scripts/aws_demo/review_job.py:66`; [LIVE] ledger known-good từ job `approve-known-good` đã có: kiểm artifact store qua `kubectl exec` ngày 18/11/2026, xem mục 9; không còn coi known-good là chưa được tạo |
| modelwire-de38fc6b | b-actions mặc định | Job | Không cổng/S | [B] `scripts/aws_demo/modelwire.py:244`, `scripts/aws_demo/modelwire.py:275`, `scripts/aws_demo/modelwire.py:340`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:162` |
| sentinelops-gp3-kms; 5 PVC *-artifacts-kms | SC cấp cluster; PVC b-actions | StorageClass/PVC; không phải workload | Không áp dụng | [B] `infra/kubernetes/sentinelops-gp3-kms.storageclass.yaml:5`, `scripts/aws_demo/artifact-pvcs-kms.yaml:8`, `scripts/aws_demo/artifact-pvcs-kms.yaml:21`, `scripts/aws_demo/artifact-pvcs-kms.yaml:34`, `scripts/aws_demo/artifact-pvcs-kms.yaml:47`, `scripts/aws_demo/artifact-pvcs-kms.yaml:60`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:140` |
| payment-rollback / payment-quarantine-observer | Xem manifest RBAC | SA/RBAC; không D/S/Job | Không áp dụng | [LIVE] `payment-rollback` ngày 18/11/2026: `kubectl get role payment-rollback` → Deployment `payment-service` chỉ `get`/`patch`; [B] observer riêng: `infra/kubernetes/payment-quarantine-observer-rbac.yaml:9` |
| infra/control, infra/commerce | Không áp dụng | SQL; không phải workload K8s | Không áp dụng | [C] Mã SQL; migration control qua Job [A] ở trên; `infra/control/001-deployment-roles.sql:1`, `infra/commerce/001-deployment.sql:1`, `scripts/kubernetes_testbed.py:54` |
| Renderer, driver, harness | Không áp dụng | Tooling | Không áp dụng | [C] `scripts/aws_demo/README.render.md:77`, `scripts/aws_demo/README.render.md:5` |

API renderer `controller_sidecar` chỉ tạo ConfigMap+S; namespace mặc định của API độc lập vẫn là `sentinelops` (`scripts/render_api_manifests.py:18`, `scripts/render_api_manifests.py:71`, `scripts/render_api_manifests.py:85`). Phiên này ConfigMap/S đã được triển khai cùng controller trong b-actions và sidecar phục vụ HTTP thật; không còn kết luận thiếu wiring namespace cho bản đang chạy (live 18/11/2026: `kubectl get svc sentinelops-api`, `kubectl get configmap sentinelops-api-runtime`, `GET /ui/`, `GET /health`).
Hai collector có pipeline khác nhau; cấu hình testbed chưa gửi vào collector AWS, không dùng collector nội bộ [LIVE] để nâng collector AWS [B] (`scripts/aws_demo/README.render.md:85`; live 18/11/2026: `GET /api/v1/targets` của Prometheus cho target nội bộ up).
Báo cáo lịch sử ghi 7/7 pod nhưng danh sách không khớp tổng đó (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:12`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:87`). Phiên live kiểm thấy 10 Deployment có sẵn READY 1/1 trước khi thêm Grafana/controller; số 2/2 của pod controller là hai container trong cùng Pod (live 18/11/2026: `kubectl get deployment,pods`).

## 6. Observability

| Thành phần | Trạng thái theo repo và live | Bằng chứng path:line hoặc live |
|---|---|---|
| Prometheus | [LIVE] Ready, target Collector up tại `http://otel-collector:8889/metrics`; có 13 tên metric, gồm `sentinelops_http_requests_total`, `sentinelops_http_errors_total`, `sentinelops_http_request_duration_ms_milliseconds_bucket` | Live 18/11/2026: `GET /-/ready`, `/api/v1/targets`, `/api/v1/label/__name__/values`; [A] image v3.5.0 pin digest: `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:79` |
| Jaeger | [LIVE] Deployment READY 1/1; [A] image 2.21.0 pin digest; cấu hình Collector nội bộ xuất trace sang Jaeger | Live 18/11/2026: `kubectl get deploy jaeger`; image: `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:79`; pipeline: `observability/otel-collector-config.yaml:23`, `observability/otel-collector-config.yaml:34` |
| Grafana EKS | [LIVE] Đã deploy, READY 1/1; datasource mặc định `SentinelOps-Prometheus` trỏ `http://prometheus.sentinelops-b-actions.svc.cluster.local:9090`; dashboard `SentinelOps — worker telemetry`, uid `sentinelops-workers`; OAuth tắt, dữ liệu `emptyDir` | Live 18/11/2026: `kubectl get deploy grafana`, `kubectl get configmap grafana-provisioning grafana-dashboards`; `GET /grafana/api/health` → DB ok, 12.2.0; `GET /grafana/login` → 200; cấu hình/giới hạn ở mục 9 |
| Grafana local | [C] Compose root và lane B; dashboard/checker không chứng minh deploy AWS | `docker-compose.yml:42`, `docker-compose.yml:60`, `docker-compose.b.yml:6`, `docs/runbooks/OBSERVABILITY.md:237`, `docs/runbooks/OBSERVABILITY.md:253`, `docker-compose.yml:65`, `scripts/check_grafana_dashboards.py:1`, `scripts/check_grafana_dashboards.py:6` |
| CloudWatch Logs | [A] Bằng chứng apply tổng hợp; [B] chưa kiểm riêng group/config | `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`, `infra/aws/observability.tf:5`, `infra/aws/observability.tf:13`, `infra/aws/observability.tf:21`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:64` |
| CloudWatch dashboard | [B] Apply lỗi, sửa local chưa apply | `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:136`, `infra/aws/dashboard.tf:72` |
| CloudWatch/X-Ray telemetry delivery | [B] Có cấu hình/quyền; **Không có bằng chứng** delivery/parity; demo dùng Prometheus/Jaeger | `infra/aws/telemetry_exporter.tf:3`, `infra/aws/telemetry_exporter.tf:31`, `infra/aws/telemetry_exporter.tf:46`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:17` |

Prometheus và Grafana đã được truy cập qua port-forward trong phiên live 18/11/2026: `GET http://127.0.0.1:9090/-/ready`, `GET http://127.0.0.1:13000/grafana/api/health`. Các lệnh dưới đây là đường loopback tương ứng; chưa có bằng chứng URL public hoạt động vì CloudFront `Enabled=false` (live 18/11/2026: `aws cloudfront list-distributions`).

```sh
kubectl --context sentinelops-demo -n sentinelops-b-actions port-forward --address 127.0.0.1 svc/prometheus 9090:9090
kubectl --context sentinelops-demo -n sentinelops-b-actions port-forward --address 127.0.0.1 svc/grafana 13000:3000
# Chạy mỗi lệnh trong một terminal; mở http://127.0.0.1:9090 và http://127.0.0.1:13000/grafana/
```

## 7. UI operator

**[LIVE] UI/API đã deploy và sidecar chạy thật; đăng nhập UI chưa hoàn tất.** Pod `remediation-controller` có container controller + `operator-ui`, **2/2 Running, 0 restart**; dùng chung PVC `remediation-artifacts` tại `/artifacts/remediation` (live 18/11/2026: `kubectl get pods -l app=remediation-controller`, `kubectl get deploy remediation-controller`; mount: `scripts/render_worker_manifests.py:318`, `scripts/render_worker_manifests.py:366`). UI/API cùng FastAPI, cổng 8000; sidecar được render bằng `--demo-ui`, trong khi API độc lập vẫn là phương án khác (`scripts/render_worker_manifests.py:333`, `scripts/render_worker_manifests.py:345`, `scripts/render_api_manifests.py:85`).

Live 18/11/2026 qua port-forward: `GET /ui/` → **HTTP 200**; `GET /health` → `status=ok`, `service=sentinelops-api`, `version=0.2.1`; `GET /v1/incidents` không token → **HTTP 401**; `GET /ui/auth-config` → `mode=oidc`, issuer pool `ap-southeast-1_JS1rLBJg3`. Những kiểm tra này chứng minh HTTP và chặn truy cập ẩn danh, chưa chứng minh đăng nhập hoặc duyệt thành công. Hiện chưa có incident để hiển thị; log controller poll trả `[]` (live 18/11/2026: `kubectl logs deployment/remediation-controller -c remediation-controller`).

Đường mạng thiết kế: CloudFront → VPC origin → ALB internal → NodePort 30080 → sidecar:8000 (`scripts/aws_demo/README.ui.md:5`, `scripts/aws_demo/README.ui.md:18`, `scripts/aws_demo/README.ui.md:50`, `scripts/aws_demo/README.ui.md:59`). CloudFront/ALB/VPC origin đã có nhưng **CloudFront chưa phục vụ URL public vì `Enabled=false`** (live 18/11/2026: `aws cloudfront list-distributions`, `aws cloudfront list-vpc-origins`, `aws elbv2 describe-load-balancers`).

```sh
# Đường loopback tới sidecar đã chạy trong phiên live 18/11/2026.
kubectl --context sentinelops-demo -n sentinelops-b-actions port-forward deployment/remediation-controller --address 127.0.0.1 8000:8000
# Mở bằng hostname localhost để callback Cognito khớp: http://localhost:8000/ui/
```

**Bẫy Cognito đã xác minh live 18/11/2026:** callback HTTP chỉ được chấp nhận với hostname **`localhost`**, không phải `127.0.0.1`. Ban đầu pool có 0 user, client chỉ có callback `https://dr76usfhbe2w8.cloudfront.net/ui/callback`, nên đăng nhập loopback lỗi `redirect_mismatch`. Owner đã thêm `http://localhost:8000/ui/callback`, giữ callback CloudFront và các field khác, rồi tạo user `nvtank` (lệnh/endpoint trong phiên: `aws cognito-idp list-users`, `aws cognito-idp describe-user-pool-client`, `aws cognito-idp update-user-pool-client`, `aws cognito-idp admin-create-user`, hosted-login `/oauth2/authorize`). Vì vậy phải mở **`http://localhost:8000/ui/`**; chưa hoàn tất đăng nhập vì còn đặt password permanent và enroll MFA TOTP (live 18/11/2026: `aws cognito-idp admin-get-user`, `aws cognito-idp describe-user-pool`; MFA bắt buộc: `infra/aws/operator_identity.tf:29`, `infra/aws/operator_identity.tf:34`).

Client operator dùng flow `code`, scopes `openid`, `sentinelops/incidents.read`, `sentinelops/remediation.approve`; password policy tối thiểu 16 ký tự, có hoa/thường/số/ký hiệu (live 18/11/2026: `aws cognito-idp describe-user-pool-client`, `aws cognito-idp describe-user-pool`; đối chiếu `infra/aws/operator_identity.tf:37`, `infra/aws/operator_identity.tf:82`). `PreventUserExistenceErrors=ENABLED` khiến thông báo lỗi đăng nhập không chỉ ra đúng nguyên nhân; phiên này phải kiểm trạng thái thật bằng `aws cognito-idp admin-get-user` (live 18/11/2026; cấu hình: `infra/aws/operator_identity.tf:79`). Enroll TOTP cần người dùng quét QR và nhập mã từ authenticator; tạo user bằng IAM admin chưa hoàn tất bước đó (live 18/11/2026: hosted-login yêu cầu MFA).

Callback localhost thêm bằng CLI tạo **drift** với Terraform: validation hiện chỉ chấp nhận HTTPS; khi edge bật, callback IaC chỉ là CloudFront, nên apply cấu hình cũ sẽ hoàn nguyên callback loopback (`infra/aws/operator_identity.tf:19`, `infra/aws/operator_identity.tf:86`; live 18/11/2026: `aws cognito-idp describe-user-pool-client`).
Mã UI [C] dùng Authorization Code + PKCE; token trong RAM; quyết định proposal cần cả `incidents.read` và `remediation.approve` (`scripts/aws_demo/README.ui.md:71`, `scripts/aws_demo/README.ui.md:79`, `scripts/aws_demo/README.ui.md:91`, `scripts/aws_demo/README.ui.md:94`).
POST decision dùng Bearer token và `If-Match` hash, chỉ ghi receipt; **duyệt không chứng minh rollback/recovered** (`scripts/aws_demo/README.ui.md:119`, `scripts/aws_demo/README.ui.md:128`, `src/sentinelops/ui/assets/client.js:192`, `src/sentinelops/api.py:169`, `src/sentinelops/ui/assets/client.js:75`, `scripts/aws_demo/README.ui.md:71`, `scripts/aws_demo/README.ui.md:77`, `scripts/aws_demo/README.ui.md:151`, `src/sentinelops/ui/assets/client.js:164`, `scripts/aws_demo/README.ui.md:91`, `scripts/aws_demo/README.ui.md:94`, `src/sentinelops/api_settings.py:23`, `src/sentinelops/remediation/proposal_api.py:131`).

## 8. Trạng thái luồng tự sửa

```text
[A] Payment +950 ms → OPEN → RCA_READY → ESCALATED → đã gỡ fault
[LIVE 18/11/2026] UI/sidecar + controller chạy; chưa có incident, policy tắt
[B/C] UI duyệt → rollback → recovered: CHƯA CÓ BẰNG CHỨNG AWS
```

Hàng [A] là sự kiện lịch sử (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:91`); hàng [LIVE] kiểm bằng `kubectl get pods`, `kubectl logs deployment/remediation-controller -c remediation-controller` → `[]`, `kubectl get deploy remediation-controller` → không có `--policy-enabled`, cùng `GET /ui/`/`GET /health` ngày 18/11/2026. **Bấm Duyệt hiện chưa chạy rollback vì policy tắt**; kill switch được cấu hình tại `/artifacts/remediation/KILL_SWITCH`, không khẳng định file đang tồn tại (live 18/11/2026: kiểm args Deployment; ý nghĩa: `scripts/run_remediation_controller.py:50`, `scripts/run_remediation_controller.py:51`, `scripts/render_worker_manifests.py:293`).

Incident worker được báo cáo `actions_allowed=false`; fault gỡ lúc 06:43:15Z. Chưa có bằng chứng model được nạp hoặc remediation chạy trọn vòng EKS (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:90`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:91`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:162`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:148`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:18`).
Kết quả recovered/telemetry_verified trong checkpoint là sandbox kind local [C], `G6_accepted=false`, không nâng thành AWS (`docs/delivery/LANE_B_CHECKPOINT.md:662`, `docs/delivery/LANE_B_CHECKPOINT.md:676`, `docs/delivery/LANE_B_CHECKPOINT.md:547`, `docs/delivery/AWS_DEMO_PLAN.md:47`).
Hợp đồng hiện hành yêu cầu duyệt ở mọi severity, không có auto; mô tả LOW tự chạy/v2 trong báo cáo AWS đã cũ (`docs/design/AUTO_RUNBOOK_INTERFACE.md:9`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:23`).
Ngưỡng oncall: score ≥ max(0.80, catalog), margin ≥ max(0.15, catalog), executable và đủ evidence (`docs/design/AUTO_RUNBOOK_INTERFACE.md:9`, `configs/runbook_catalog.json:3`, `configs/severity_policy.json:2`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:103`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:23`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:14`).
Fault RAM qua API không tạo deployment-change và không đủ điều kiện rollback bad-deploy; demo rollback cần image lỗi và known-good đã duyệt trước onset (`scripts/run_runbook_demo.py:99`, `services/common/faults.py:31`, `services/payment/app.py:97`, `configs/runbook_catalog.json:11`, `docs/runbooks/CONTROLLED_REMEDIATION.md:25`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:17`).

## 9. Phiên triển khai 18/11/2026

Các kết quả **[LIVE]** dưới đây được ghi nhận trong phiên 18/11/2026 bằng AWS CLI, `kubectl` và HTTP qua port-forward; lệnh/endpoint đi kèm từng bước.

### 9.1. Trước khi triển khai thêm

Cluster `sentinelops-dev` ACTIVE, namespace `sentinelops-b-actions` tồn tại, 10 Deployment cũ READY 1/1; RDS và hai repo ECR đã có. Các Secret có sẵn gồm `control-runtime`, `remediation-runtime`, `commerce-credentials`, `incident-observer-credentials`; không sao chép giá trị từ các Secret vào tài liệu (live 18/11/2026: `aws eks describe-cluster`, `aws rds describe-db-instances`, `aws ecr describe-repositories`, `kubectl get namespace sentinelops-b-actions`, `kubectl get deployment`, `kubectl get secret` chỉ xem tên/trạng thái). Grafana và controller chưa tồn tại ở đầu phiên; được tạo mới trong các bước dưới (live 18/11/2026: đối chiếu `kubectl get deployment` trước/sau apply).

### 9.2. Build và push image mới

Đã build control-plane từ `main` tại commit **`b4b416f`**, push ECR tag **`git-b4b416f`**, digest **`sha256:bb40c73ad3c17e585e36f6e49d99f491371e971b3f730ee9d0d341de949a4859`**; giữ nguyên tag cũ `git-d1eee79` (live 18/11/2026: Docker build/push và `aws ecr describe-images --repository-name sentinelops/control-plane --image-ids imageTag=git-b4b416f`; cách gắn revision/tag: `scripts/aws_demo/build_images.sh:37`, `scripts/aws_demo/build_images.sh:74`, `docker/Dockerfile.control-plane:22`).

Lý do phải build: image cũ duy nhất trong ECR là `git-d1eee79`, chưa có `src/sentinelops/remediation/proposal_api.py`, chưa có flag `--demo-ui`, và còn bảng quyết định `score >= 0.80 → auto`, cho LOW tự chạy không cần duyệt (live 18/11/2026: `aws ecr describe-images --repository-name sentinelops/control-plane` và kiểm nội dung image bằng Docker; mô tả auto cũ: `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:23`). Hợp đồng mới yêu cầu duyệt ở mọi severity; module/flag hiện có trong `src/sentinelops/remediation/proposal_api.py:1`, `scripts/render_worker_manifests.py:437`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:9`.

Đã kiểm trong image mới: `sentinelops/ui/` có `index.html`, `app.js`, `client.js`, `workspace.js`, `styles.css`, `routes.py`; `sentinelops.remediation.proposal_api` import được (live 18/11/2026: `docker run` kiểm file và Python import trong image `git-b4b416f`; phản hồi runtime `GET /ui/` → 200). Việc push image này không tự chứng minh hai worker cũ đã được nâng image; chúng vẫn cần đối chiếu digest riêng (`scripts/render_worker_manifests.py:161`, `scripts/render_worker_manifests.py:391`).

### 9.3. Deploy Grafana

Đã apply mới 3 ConfigMap `grafana-config`, `grafana-provisioning`, `grafana-dashboards`; 2 Secret `grafana-admin`, `grafana-oauth`; Service `grafana` ClusterIP cổng 3000 và Deployment `grafana`. Rollout thành công, READY 1/1; `GET /grafana/api/health` trả DB `ok`, version `12.2.0`; `GET /grafana/login` trả 200 (live 18/11/2026: `kubectl get configmap`, `kubectl get secret` chỉ xem metadata, `kubectl get svc grafana`, `kubectl rollout status deployment/grafana`, các endpoint trên qua port-forward).

Datasource mặc định `SentinelOps-Prometheus` trỏ `http://prometheus.sentinelops-b-actions.svc.cluster.local:9090`; dashboard provisioned `SentinelOps — worker telemetry`, uid `sentinelops-workers` (live 18/11/2026: `kubectl get configmap grafana-provisioning grafana-dashboards`). Đường quản trị loopback dùng `--emergency-admin-login`, `--storage empty-dir`, override `GF_SERVER_ROOT_URL`, `GF_SECURITY_COOKIE_SECURE=false`, `GF_AUTH_GENERIC_OAUTH_ENABLED=false` (live 18/11/2026: kiểm cấu hình/env/volume bằng `kubectl get deploy grafana`; ý nghĩa flag: `scripts/aws_demo/render_grafana.py:270`, `scripts/aws_demo/render_grafana.py:271`).

Chọn `emptyDir` để tránh phụ thuộc StorageClass `sentinelops-gp3-kms` còn placeholder ARN. **OAuth Cognito của Grafana chưa bật; dữ liệu Grafana mất khi Pod bị thay.** Truy cập đã kiểm: `http://127.0.0.1:13000/grafana/`; chỉ ghi tên Secret `grafana-admin` với key `admin-password`, không ghi giá trị (live 18/11/2026: `kubectl get deploy grafana`, `GET /grafana/login`; placeholder: `infra/kubernetes/sentinelops-gp3-kms.storageclass.yaml:13`; lưu trữ: `scripts/aws_demo/render_grafana.py:248`; key: `scripts/aws_demo/render_grafana.py:265`).

### 9.4. Deploy controller và sidecar UI

Đã tạo Deployment `remediation-controller` gồm controller và sidecar `operator-ui`, dùng image mới, chung PVC `remediation-artifacts`; kèm ConfigMap `sentinelops-api-runtime` và Service `sentinelops-api` trong b-actions. Kết quả cuối **2/2 Running, 0 restart**; log controller poll ra `[]`, hiện chưa có incident chờ. UI trả 200, health ok, API incidents không token trả 401, auth-config dùng OIDC pool `ap-southeast-1_JS1rLBJg3` (live 18/11/2026: `kubectl get deploy remediation-controller`, `kubectl get pods`, `kubectl get svc sentinelops-api`, `kubectl get configmap sentinelops-api-runtime`, `kubectl logs deployment/remediation-controller -c remediation-controller`; `GET /ui/`, `/health`, `/v1/incidents`, `/ui/auth-config`).

**Policy vẫn tắt:** args không có `--policy-enabled`, controller chưa dispatch; bấm Duyệt chưa chạy rollback. Kill switch được cấu hình tại `/artifacts/remediation/KILL_SWITCH`. Role `payment-rollback` chỉ cho `get`/`patch` đối với đúng Deployment `payment-service`; còn quyền đọc ReplicaSet/Pod/HPA, không phải quyền patch mọi Deployment (live 18/11/2026: `kubectl get deploy remediation-controller`, `kubectl get role payment-rollback`; đối chiếu `scripts/render_worker_manifests.py:293`, `scripts/render_worker_manifests.py:397`).

### 9.5. Hai lỗi thật đã gặp và cách sửa

| Lỗi | Nguyên nhân và cách sửa trong phiên | Bằng chứng |
|---|---|---|
| `ValueError: explicit known-good ledger reference and approver are required` | Controller cần tham chiếu ledger và người duyệt rõ ràng. Đã dùng ledger known-good thật có sẵn do job `approve-known-good` tạo; Secret `remediation-runtime` đã có đủ key `action-database-url`, `known-good-ledger-ref`, `approved-by`. Cấp đúng các tham chiếu này cho controller; chỉ ghi tên key, không ghi giá trị. Ledger vẫn có `action_dispatch_authorized=false`. | Live 18/11/2026: log container controller (`kubectl logs`), kiểm ledger trong artifact store qua `kubectl exec` và tên key Secret; validator: `scripts/run_remediation_controller.py:43`, `scripts/run_remediation_controller.py:68`; wiring key: `scripts/render_worker_manifests.py:326` |
| `asyncpg.exceptions.ClientConfigurationError` | Kết nối yêu cầu `sslmode=verify-full` nhưng thiếu `sslrootcert`; asyncpg fallback sang `~/.postgresql/root.crt`, không có trong container uid 10001 với `readOnlyRootFilesystem`. `SSL_CERT_FILE` không có tác dụng ở nhánh asyncpg tự cấu hình này. Đã thêm **`PGSSLROOTCERT=/etc/sentinelops/tls/ca-bundle.crt`** cho controller; không thêm `sslrootcert` vào DSN vì validator bắt query đúng `sslmode=verify-full`. | Live 18/11/2026: log controller trước sửa, kiểm env bằng `kubectl get deploy remediation-controller`, sau sửa pod 2/2 Running và poll `[]`; query validator: `scripts/run_remediation_controller.py:62`; CA mount/env cũ: `scripts/render_worker_manifests.py:315`, `scripts/render_worker_manifests.py:325`; uid: `scripts/render_worker_manifests.py:381` |

## 10. Cảnh báo Terraform state đã cũ

**Không apply Terraform từ bản backup này trước khi đồng bộ lại state, biến cấu hình và drift live.** Repo cố ý không có backend block (`infra/aws/versions.tf:7`); trong phiên 18/11/2026 không có state local tại `infra/aws`, nên đã đối chiếu backup S3 bằng AWS CLI và Terraform CLI (`aws s3api head-object`, `terraform -chdir=infra/aws state list`, `terraform -chdir=infra/aws plan`).

Backup ở `s3://sentinelops-artifacts-dev-833925774773/terraform-state/terraform.tfstate`, serial **125**, **322926 byte**, ghi ngày **18/11/2026 lúc 18:14**. Đây là tên/vị trí và metadata của tài nguyên; không trích nội dung state vì có thể chứa dữ liệu nhạy cảm (live 18/11/2026: `aws s3api head-object --bucket sentinelops-artifacts-dev-833925774773 --key terraform-state/terraform.tfstate`; serial đọc khi kiểm backup bằng Terraform CLI).

Plan đã thử với backup, `operator_identity_enabled=true` và `edge_enabled=false` cho kết quả **0 thêm / 8 sửa / 19–20 xóa**. Tập xóa gồm CloudFront, VPC origin, ALB, listener, target group, SG edge và client Cognito Grafana; plan còn hoàn nguyên `aws_eks_cluster.main` vì backup thiếu CIDR mới thêm (live 18/11/2026: `terraform -chdir=infra/aws plan` với các biến trên, đối chiếu `aws eks describe-cluster --name sentinelops-dev`; điều kiện resource edge/Grafana: `infra/aws/edge.tf:241`, `infra/aws/operator_identity.tf:101`).

State đã kiểm phản ánh `edge_enabled=true`, `operator_identity_enabled=true`, `edge_vpc_origin_bootstrap=true`, `edge_waf_enabled=false`; dùng bộ biến `edge_enabled=false` là lệch tài nguyên đang có. Cần đồng bộ cả CIDR EKS và callback Cognito localhost trước lần plan tiếp theo; kiểm kỹ mọi delete/replace và phần hoàn nguyên drift (live 18/11/2026: kiểm backup bằng Terraform CLI, `aws eks describe-cluster`, `aws cognito-idp describe-user-pool-client`; callback IaC: `infra/aws/operator_identity.tf:19`, `infra/aws/operator_identity.tf:86`).

## 11. Năm thay đổi hạ tầng đã thực hiện trong phiên

| STT | Thay đổi đã thực hiện | Bằng chứng live ngày 18/11/2026 |
|---|---|---|
| 1 | Thêm `14.191.113.71/32` vào EKS `publicAccessCidrs`, giữ `14.224.239.90/32`, `42.114.156.253/32`: tổng 3 CIDR. IP operator ngoài allowlist trước đó khiến `kubectl` timeout. | `aws eks update-cluster-config`, `aws eks describe-cluster --name sentinelops-dev`; `kubectl get nodes` sau khi cập nhật |
| 2 | Push tag ECR mới `git-b4b416f` cho control-plane, không ghi đè `git-d1eee79`. | Docker push; `aws ecr describe-images --repository-name sentinelops/control-plane` |
| 3 | Apply mới Grafana: 3 ConfigMap, 2 Secret, 1 Service, 1 Deployment. | `kubectl get configmap`, `kubectl get secret` chỉ xem metadata, `kubectl get svc grafana`, `kubectl rollout status deployment/grafana`; `GET /grafana/api/health` |
| 4 | Apply mới `remediation-controller` + sidecar `operator-ui`, kèm ConfigMap `sentinelops-api-runtime`, Service `sentinelops-api`; sửa wiring known-good/TLS để pod chạy ổn định. | `kubectl get deploy remediation-controller`, `kubectl get pods` → 2/2 Running, 0 restart; `GET /ui/`, `/health`; log controller sau sửa |
| 5 | **Owner thực hiện:** thêm callback `http://localhost:8000/ui/callback` vào Cognito, giữ callback CloudFront/các field khác; tạo user `nvtank`. | `aws cognito-idp update-user-pool-client`, `aws cognito-idp admin-create-user`; kiểm bằng `describe-user-pool-client`, `admin-get-user` |

Phiên này **không chạy `terraform apply`, không bật CloudFront**; plan chỉ dùng để phát hiện rủi ro ở mục 10, distribution cuối phiên vẫn `Enabled=false` (ghi nhận thao tác phiên 18/11/2026; kiểm cuối bằng `aws cloudfront list-distributions`).

## 12. Bảng LINK

**Đã có domain CloudFront thật nhưng chưa có URL ứng dụng public dùng được vì `Enabled=false`.** UI/Grafana hiện truy cập qua port-forward; đăng nhập UI chưa hoàn tất (live 18/11/2026: `aws cloudfront list-distributions`, `GET /ui/`, `GET /grafana/login`, `aws cognito-idp admin-get-user`). AWS Console cần identity operator; các link Console không phải URL ứng dụng (`docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:28`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:38`).

| Loại | Link / output cần lấy | Bằng chứng path:line hoặc live |
|---|---|---|
| EKS Console | [sentinelops-dev](https://ap-southeast-1.console.aws.amazon.com/eks/home?region=ap-southeast-1#/clusters/sentinelops-dev) | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:32` |
| RDS Console | [Databases](https://ap-southeast-1.console.aws.amazon.com/rds/home?region=ap-southeast-1#databases:) | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:33` |
| ECR Console | [Repositories](https://ap-southeast-1.console.aws.amazon.com/ecr/repositories?region=ap-southeast-1) | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:34` |
| S3 Console | [Buckets](https://ap-southeast-1.console.aws.amazon.com/s3/buckets?region=ap-southeast-1); bucket `sentinelops-artifacts-dev-833925774773` | Live 18/11/2026: `aws s3api head-object` trên backup state ở mục 10; Console: `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:35` |
| Secrets Manager | [Secrets](https://ap-southeast-1.console.aws.amazon.com/secretsmanager/listsecrets?region=ap-southeast-1) | `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:88` |
| Budgets | [Budgets Console](https://ap-southeast-1.console.aws.amazon.com/billing/home?region=ap-southeast-1#/budgets) | `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:68`, `infra/aws/variables.tf:266`, `infra/aws/variables.tf:277` |
| CloudWatch | [Console](https://ap-southeast-1.console.aws.amazon.com/cloudwatch/home?region=ap-southeast-1); lấy `log_group_names`, không suy dashboard tồn tại | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:38`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:28`, `infra/aws/outputs.tf:73` |
| VPC | [Console](https://ap-southeast-1.console.aws.amazon.com/vpcconsole/home?region=ap-southeast-1) | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:39` |
| KMS | [Keys](https://ap-southeast-1.console.aws.amazon.com/kms/home?region=ap-southeast-1#/kms/keys); ARN thật lấy state `aws_kms_key.main` | `infra/aws/eks.tf:87`, `infra/aws/eks.tf:96`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10` |
| PR được kế hoạch nhắc | [#68](https://github.com/VietSory/SentinelOps/pull/68) | `docs/delivery/AWS_DEMO_PLAN.md:96` |
| Báo cáo triển khai | [AWS_DEPLOY_REPORT_2026-11-18.md](AWS_DEPLOY_REPORT_2026-11-18.md) | `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:1`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:172`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:183` |
| Truy cập và luồng | [AWS_DEMO_ACCESS_AND_FLOWS.md](AWS_DEMO_ACCESS_AND_FLOWS.md) | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:1`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:22` |
| Kế hoạch / checkpoint | [AWS_DEMO_PLAN.md](AWS_DEMO_PLAN.md), [LANE_B_CHECKPOINT.md](LANE_B_CHECKPOINT.md) | `docs/delivery/AWS_DEMO_PLAN.md:1`, `docs/delivery/LANE_B_CHECKPOINT.md:1` |
| Hướng dẫn UI / edge / PVC | [README.ui.md](../../scripts/aws_demo/README.ui.md), [EDGE.md](../../scripts/aws_demo/EDGE.md), [PVC_MIGRATION.md](../../scripts/aws_demo/PVC_MIGRATION.md) | `scripts/aws_demo/README.ui.md:156`, `scripts/aws_demo/EDGE.md:5`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:1`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:172`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:183` |
| UI loopback | [http://localhost:8000/ui/](http://localhost:8000/ui/) sau port-forward controller; phải dùng `localhost`, login còn chờ password permanent/MFA | Live 18/11/2026: `GET /ui/`, `/ui/auth-config`; Cognito `/oauth2/authorize`, `aws cognito-idp admin-get-user` |
| Grafana loopback | [http://127.0.0.1:13000/grafana/](http://127.0.0.1:13000/grafana/) sau port-forward Service; OAuth chưa bật, dữ liệu `emptyDir` | Live 18/11/2026: `GET /grafana/login`, `/grafana/api/health`; `kubectl get deploy grafana` |
| Cognito hosted-login | Hosted domain `sentinelops-dev-833925774773-operators`, pool `ap-southeast-1_JS1rLBJg3`; đi qua nút đăng nhập UI để có code/PKCE | Live 18/11/2026: `aws cognito-idp describe-user-pool`, `/ui/auth-config`; PKCE: `src/sentinelops/ui/assets/client.js:83` |
| CloudFront domain đã tạo | `dr76usfhbe2w8.cloudfront.net` — **Deployed nhưng Enabled=false, chưa dùng được làm URL public** | Live 18/11/2026: `aws cloudfront list-distributions` |

Các lệnh output/state dưới đây giữ làm tham khảo theo IaC, **không phải bằng chứng endpoint sống**; chỉ dùng sau khi xử lý state cũ ở mục 10. Kết quả live hiện có lấy từ AWS/kubectl/HTTP ở từng hàng (`infra/aws/outputs.tf:15`, `infra/aws/outputs.tf:73`, `infra/aws/outputs.tf:107`, `infra/aws/operator_identity.tf:130`; live 18/11/2026: `aws cloudfront list-distributions`, `GET /ui/`, `/grafana/api/health`).

```sh
terraform -chdir=infra/aws output artifact_bucket
terraform -chdir=infra/aws output log_group_names
terraform -chdir=infra/aws output edge_domain
terraform -chdir=infra/aws output operator_login_domain
terraform -chdir=infra/aws output -json operator_oidc_binding
terraform -chdir=infra/aws state show aws_kms_key.main
```

## 13. Việc còn lại trước demo trọn vòng

| Việc cần hoàn tất | Tiêu chí / giới hạn bằng chứng hiện có |
|---|---|
| Đặt password permanent và enroll MFA TOTP cho user Cognito | User `nvtank` đã tạo, login chưa hoàn tất; người dùng cần quét QR và nhập mã authenticator. Không đưa giá trị password/MFA vào tài liệu. Live 18/11/2026: `aws cognito-idp admin-get-user`, `describe-user-pool`, hosted-login yêu cầu MFA |
| Sinh incident thật để UI có dữ liệu | Hiện chưa có incident; cần tiêm lỗi phù hợp, kiểm health → incident → proposal sau đăng nhập. Live 18/11/2026: log controller `[]`, `GET /ui/`; phân biệt fault RAM/bad-deploy: `scripts/run_runbook_demo.py:99`, `docs/runbooks/CONTROLLED_REMEDIATION.md:25` |
| Nạp model/H4, đối chiếu image worker đã pin | Chưa có bằng chứng mới nạp model; image `git-b4b416f` mới xác nhận cho controller/UI, không suy worker cũ đã nâng. Lịch sử timeout: `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:162`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:163`; live 18/11/2026: `kubectl get deploy remediation-controller` |
| Kiểm điều kiện trước khi cho phép dispatch | Known-good đã có; còn kiểm khớp target/image/thời điểm trước onset, action writer và các guard trước khi bật policy. **Policy hiện tắt, bấm Duyệt chưa chạy rollback**. Live 18/11/2026: `kubectl get deploy remediation-controller`, log controller; guard: `docs/runbooks/CONTROLLED_REMEDIATION.md:76`, `scripts/run_remediation_controller.py:51` |
| Đồng bộ state/biến Terraform và drift CLI | Xử lý backup thiếu CIDR mới và callback localhost; plan hiện có **19–20 delete**, không apply bản backup. Live 18/11/2026: `terraform -chdir=infra/aws plan` với `operator_identity_enabled=true`, `edge_enabled=false`; `aws eks describe-cluster`, `aws cognito-idp describe-user-pool-client` |
| Hoàn tất đường web public và Grafana | CloudFront đã có nhưng `Enabled=false`; còn kiểm origin/wiring trước khi bật. Grafana OAuth chưa bật, cần lưu trữ bền thay `emptyDir` nếu giữ dữ liệu. Live 18/11/2026: `aws cloudfront list-distributions`, `kubectl get deploy grafana`; cấu hình: `infra/aws/edge.tf:242`, `scripts/aws_demo/render_grafana.py:248` |
| Di trú PVC KMS; xử lý dashboard CloudWatch | Kiểm EBS CSI/KMS thực, giữ ledger; dashboard sửa local chưa apply; `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:58`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:136`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:50`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:140` |
| Digest regression cho CLI | Driver nhận digest khác known-good **trong cùng repo** known-good (yêu cầu của rollback); dùng `833925774773.dkr.ecr.ap-southeast-1.amazonaws.com/sentinelops/application@sha256:766dd5f5a32e…` (`git-d200fc0-payment-regression`); không tạo repo `payment-regression` riêng vì rollback sẽ bị chặn `known-good-image-is-not-a-distinct-same-repository-digest`. Child amd64 `sha256:c6db35a8…` scan BASIC COMPLETE: CRITICAL 0, HIGH 3, MEDIUM 12, LOW 3 (live 19/11/2026: `aws ecr describe-image-scan-findings`); attestation manifest không quét được (FAILED) là bình thường | `scripts/run_runbook_demo.py`, `src/sentinelops/remediation/domain.py`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:75` |
| Thu bằng chứng rollback và các nhánh âm | Recovered + telemetry_verified, đúng 1 dispatch, 3 bucket clear; kiểm reject/verify lỗi/kill switch; `scripts/run_runbook_demo.py:236`, `docs/runbooks/CONTROLLED_REMEDIATION.md:215`, `scripts/run_runbook_demo.py:89`, `scripts/run_runbook_demo.py:221`, `scripts/run_runbook_demo.py:6` |
| Lưu receipt và dọn sau demo | Receipt demo không phải acceptance; teardown chưa có bằng chứng hoàn tất; `scripts/run_runbook_demo.py:479`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:16`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:59`, `docs/delivery/AWS_DEMO_PLAN.md:67`, `docs/delivery/AWS_DEMO_PLAN.md:114` |

## 14. Runbook demo web

Đường dẫn bàn giao theo yêu cầu: [docs/runbooks/DEMO_WEB_UI.md](../runbooks/DEMO_WEB_UI.md).
Runbook và [scripts/aws_demo/README.ui.md](../../scripts/aws_demo/README.ui.md) là hướng dẫn thao tác [B/C], không tự chứng minh luồng remediation chạy trọn vòng (`scripts/aws_demo/README.ui.md:3`, `scripts/aws_demo/README.ui.md:146`). Bằng chứng UI đã deploy là kết quả [LIVE] 18/11/2026 ở mục 7/9: pod 2/2, `GET /ui/` → 200, `/health` → ok; giới hạn hiện tại vẫn là đăng nhập chưa hoàn tất, chưa có incident, policy tắt và CloudFront disabled (`aws cognito-idp admin-get-user`, log/args controller, `aws cloudfront list-distributions`). Khi làm theo hướng dẫn cũ, dùng callback **localhost** đã xác minh trong mục 7.
