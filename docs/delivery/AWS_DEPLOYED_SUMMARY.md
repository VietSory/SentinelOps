# Tổng hợp triển khai AWS — chốt 18/11/2026

> **KHÔNG CÓ XÁC MINH LIVE:** máy này không có AWS credential hợp lệ và không có kube context `sentinelops-demo`.
> Tài liệu chỉ tổng hợp bằng chứng trong repo; không xác nhận tài nguyên còn tồn tại hoặc đang chạy.
> Không chạy `aws`, `kubectl`, `terraform` trong phiên này.

## 1. Khung đánh giá

Mốc đối chiếu: **main, commit `0afb06d`, ngày 18/11/2026**, region triển khai được báo cáo là `ap-southeast-1` (`docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:3`; commit đối chiếu theo mốc đầu bài).

| Mức | Ý nghĩa | Giới hạn |
|---|---|---|
| [A] | Có báo cáo/artifact ghi đã apply hoặc chạy trên AWS | Bằng chứng lịch sử; không phải kiểm tra live hiện tại |
| [B] | Có IaC/manifest nhưng không có bằng chứng apply thành công | Không khẳng định dịch vụ hoạt động |
| [C] | Chỉ mã, tooling, kiểm thử hoặc local | Không phải bằng chứng triển khai AWS |

Tài liệu được tổng hợp từ đối chiếu source và các báo cáo trong `docs/delivery`; mức [A/B/C] được giữ theo giới hạn bằng chứng nêu trên.

**Cảnh báo tên:** Terraform mặc định `name_prefix=sentinelops`, `environment=prod`; đợt deploy được báo cáo dùng **dev**.
Vì vậy `sentinelops-prod`, `sentinelops-control-prod`, `alias/sentinelops-prod` là tên suy từ defaults, không phải tên live đã kiểm.
Tên được báo cáo là cluster `sentinelops-dev`, RDS `sentinelops-control-dev`, secret `sentinelops/dev/control-runtime` (`infra/aws/variables.tf:21`, `infra/aws/variables.tf:32`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:9`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:12`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:88`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:105`).

## 2. TÓM TẮT NHANH

| Câu hỏi chủ dự án | Trả lời dứt khoát theo bằng chứng | Mức | Bằng chứng path:line |
|---|---|---|---|
| UI đã deploy chưa? | **Chưa có bằng chứng deploy UI lên AWS; chưa có URL public bàn giao.** Có manifest sidecar và mã UI. | [B]; mã/local [C] | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:15`, `scripts/aws_demo/README.ui.md:10`, `scripts/aws_demo/README.ui.md:156` |
| Prometheus đã deploy chưa? | **Có, theo báo cáo đã triển khai trên EKS.** Không xác minh đang chạy hôm nay. | [A] | `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:12`, `scripts/kubernetes_testbed.py:104` |
| Grafana đã deploy chưa? | **Chưa deploy EKS theo trạng thái báo cáo.** Có renderer; Grafana local không tính là AWS. | [B]; Compose local [C] | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:14`, `scripts/aws_demo/README.grafana.md:3`, `docker-compose.yml:42`, `docker-compose.b.yml:6` |

## 3. Hạ tầng AWS có bằng chứng đã apply

Báo cáo ghi **77/78 tài nguyên đã lên**; **CloudWatch dashboard là thành phần duy nhất apply lỗi trong đợt 78 tài nguyên**.
Điều này không nâng các thành phần opt-in hoặc từng cấu hình riêng lên [A] (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`, `infra/aws/dashboard.tf:5`, `infra/aws/dashboard.tf:72`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:136`).

| Thành phần | Kết quả được báo cáo | Mức và bằng chứng path:line |
|---|---|---|
| EKS | `sentinelops-dev`, Kubernetes 1.34, 2 node `m7i-flex.large`; context được tài liệu gọi `sentinelops-demo` | [A] `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:9` |
| RDS | `sentinelops-control-dev`, PostgreSQL 16.15, `db.t4g.micro`, private, single-AZ, 20 GiB, backup 1 ngày | [A] `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:12`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:64` |
| ECR | `sentinelops/application`, `sentinelops/control-plane`; image đã push/pin digest; payment-regression dùng chung repo application | [A] `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:34`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:74`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:75`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:76`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:77` |
| S3 | Artifacts và backup prefix `terraform-state/` được báo cáo có; tên bucket: **Không có bằng chứng** trong commit | [A] cấp dịch vụ; `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:62`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:35`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:567`; lấy tên từ output `artifact_bucket` |
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

## 4. Có IaC/manifest nhưng chưa bật hoặc chưa có bằng chứng apply

| Thành phần | Flag / default hoặc điều kiện | Kết luận và bằng chứng path:line |
|---|---|---|
| CloudFront edge / VPC origin | `edge_enabled=false`; bật edge cần identity; `edge_vpc_origin_bootstrap=false`, nếu true distribution bị disabled | [B] Không có domain triển khai; `infra/aws/variables.tf:283`, `infra/aws/variables.tf:289`, `infra/aws/edge.tf:95`, `infra/aws/edge.tf:240`, `infra/aws/edge.tf:242` |
| ALB internal, target groups, listener, NodePort wiring | Cùng `edge_enabled=false`; ALB thiết kế internal | [B] Chưa có bằng chứng apply; `infra/aws/variables.tf:283`, `infra/aws/edge.tf:20`, `infra/aws/edge.tf:42`, `infra/aws/edge.tf:103` |
| WAF REGIONAL gắn ALB | `edge_waf_enabled=false`; cần cả edge | [B] Chưa có bằng chứng apply; `infra/aws/variables.tf:283`, `infra/aws/variables.tf:295`, `infra/aws/edge.tf:306` |
| Cognito pool/domain/operator client | `operator_identity_enabled=false`; Grafana client cần identity và edge cùng true | [B] Pool ID, client ID, login domain: **Không có bằng chứng**; `infra/aws/operator_identity.tf:7`, `infra/aws/operator_identity.tf:25`, `infra/aws/operator_identity.tf:101`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:44` |
| X-Ray sampling rule | Không có flag enable trong resource; `xray_fixed_sampling_rate=0.1` mặc định | [B] Có IaC nhưng chưa có bằng chứng apply riêng hoặc trace delivery; `infra/aws/observability.tf:31`, `infra/aws/variables.tf:255`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:17` |
| PVC gp3 + KMS | Không có flag Terraform; manifest StorageClass + 5 PVC áp dụng/di trú riêng; `kmsKeyId` còn placeholder, request mỗi PVC 5 GiB | [B] Chưa di trú cloud; `infra/kubernetes/sentinelops-gp3-kms.storageclass.yaml:13`, `scripts/aws_demo/artifact-pvcs-kms.yaml:16`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:140` |

PVC gp2 đã được tạo theo báo cáo nhưng mã hóa thực tế chưa xác minh; tên StorageClass không chứng minh EBS encrypted/KMS (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:50`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:140`).

## 5. Workload trên EKS

`b-actions` trong bảng là namespace **`sentinelops-b-actions`**; namespace `sentinelops` là riêng.
D = Deployment, S = Service. Cổng lấy từ manifest, không phải kiểm tra live; S testbed là ClusterIP (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:83`, `scripts/render_worker_manifests.py:31`, `scripts/render_business_manifests.py:21`, `scripts/render_collector_manifests.py:18`, `scripts/kubernetes_testbed.py:88`, `scripts/kubernetes_testbed.py:20`).

| Thành phần | Namespace | Loại | Cổng manifest | Mức / bằng chứng path:line |
|---|---|---|---|---|
| checkout-service | b-actions | D+S | 8000 | [A] `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:12`, `scripts/kubernetes_testbed.py:22`, `scripts/kubernetes_testbed.py:130` |
| payment-service | b-actions | D+S | 8000 | [A] `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:12`, `scripts/kubernetes_testbed.py:22`, `scripts/kubernetes_testbed.py:130` |
| inventory-service | b-actions | D+S | 8000 | [A] `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:12`, `scripts/kubernetes_testbed.py:22`, `scripts/kubernetes_testbed.py:130` |
| postgres thương mại | b-actions | D+S | 5432 | [A] `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:12`, `scripts/kubernetes_testbed.py:93` |
| otel-collector nội bộ | b-actions | D+S | 13133, 4317, 4318, 8889 | [A] `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:12`, `scripts/kubernetes_testbed.py:100` |
| prometheus | b-actions | D+S | 9090 | [A] `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:12`, `scripts/kubernetes_testbed.py:104` |
| jaeger | b-actions | D+S | 16686, 4317 | [A] `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:12`, `scripts/kubernetes_testbed.py:99` |
| sentinelops-traffic | b-actions | D | Không khai báo cổng/S | [A] `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:12`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:87`, `scripts/aws_demo/render_all.py:47`, `scripts/aws_demo/render_all.py:53` |
| sentinelops-health-worker | b-actions | D | Không cổng/S; probe exec | [A] READY theo báo cáo; `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:89`, `scripts/render_worker_manifests.py:161`, `scripts/render_worker_manifests.py:213`, `scripts/render_worker_manifests.py:226` |
| sentinelops-incident-worker | b-actions | D | Không cổng/S; probe exec | [A] READY, `actions_allowed=false`; `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:90`, `scripts/render_worker_manifests.py:161`, `scripts/render_worker_manifests.py:213`, `scripts/render_worker_manifests.py:226` |
| remediation-controller | b-actions | D | Không khai báo cổng/S | [B] `scripts/render_worker_manifests.py:283`, `scripts/render_worker_manifests.py:375`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:166` |
| operator-ui API sidecar | b-actions | Container trong D controller | 8000 | [B] Chỉ render khi bật demo UI; `scripts/render_worker_manifests.py:337`, `scripts/render_worker_manifests.py:359`, `scripts/render_worker_manifests.py:362`, `scripts/render_worker_manifests.py:395` |
| sentinelops-api độc lập | sentinelops | D+S | 8000, ClusterIP mặc định | [B] `scripts/render_api_manifests.py:18`, `scripts/render_api_manifests.py:44`, `scripts/render_api_manifests.py:81`, `scripts/render_api_manifests.py:109`, `scripts/render_api_manifests.py:123` |
| otel-collector AWS | sentinelops | D+S | Container 4317/4318/13133; S 4317/4318 | [B] `scripts/render_collector_manifests.py:18`, `scripts/render_collector_manifests.py:114`, `scripts/render_collector_manifests.py:136`, `scripts/render_collector_manifests.py:152` |
| grafana | b-actions | D+S; PVC tùy chọn | 3000; NodePort tùy chọn | [B] `scripts/aws_demo/render_grafana.py:17`, `scripts/aws_demo/render_grafana.py:206`, `scripts/aws_demo/render_grafana.py:227`, `scripts/aws_demo/render_grafana.py:234`, `scripts/aws_demo/README.grafana.md:3`, `scripts/aws_demo/render_grafana.py:188`, `scripts/aws_demo/render_grafana.py:254` |
| Bootstrap 6 migration + runtime | b-actions theo template | Jobs; tên thực không được ghi | Không cổng/S | [A] Lịch sử chạy; `scripts/aws_demo/render_all.py:115`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:88`, `scripts/aws_demo/control-job.yaml:4`, `scripts/aws_demo/control-job.yaml:15`, `scripts/aws_demo/control.py:74`, `scripts/aws_demo/control.py:86`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:164` |
| action-runtime | b-actions theo template | Job qua control tooling | Không cổng/S | [B] Còn phải chạy; `scripts/aws_demo/control-job.yaml:4`, `scripts/aws_demo/control-job.yaml:15`, `scripts/aws_demo/control.py:74`, `scripts/aws_demo/control.py:86`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:164` |
| review / approve | b-actions | Job, approve cũng dùng tên review | Không cổng/S | [B] `scripts/aws_demo/review_job.py:54`, `scripts/aws_demo/review_job.py:59`, `scripts/aws_demo/review_job.py:66`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:165` |
| modelwire-de38fc6b | b-actions mặc định | Job | Không cổng/S | [B] `scripts/aws_demo/modelwire.py:244`, `scripts/aws_demo/modelwire.py:275`, `scripts/aws_demo/modelwire.py:340`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:162` |
| sentinelops-gp3-kms; 5 PVC *-artifacts-kms | SC cấp cluster; PVC b-actions | StorageClass/PVC; không phải workload | Không áp dụng | [B] `infra/kubernetes/sentinelops-gp3-kms.storageclass.yaml:5`, `scripts/aws_demo/artifact-pvcs-kms.yaml:8`, `scripts/aws_demo/artifact-pvcs-kms.yaml:21`, `scripts/aws_demo/artifact-pvcs-kms.yaml:34`, `scripts/aws_demo/artifact-pvcs-kms.yaml:47`, `scripts/aws_demo/artifact-pvcs-kms.yaml:60`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:140` |
| payment-rollback / payment-quarantine-observer | Xem manifest RBAC | SA/RBAC; không D/S/Job | Không áp dụng | [B] `infra/kubernetes/payment-rollback-rbac.yaml:11`, `infra/kubernetes/payment-rollback-rbac.yaml:17`, `infra/kubernetes/payment-quarantine-observer-rbac.yaml:3`, `infra/kubernetes/payment-quarantine-observer-rbac.yaml:9` |
| infra/control, infra/commerce | Không áp dụng | SQL; không phải workload K8s | Không áp dụng | [C] Mã SQL; migration control qua Job [A] ở trên; `infra/control/001-deployment-roles.sql:1`, `infra/commerce/001-deployment.sql:1`, `scripts/kubernetes_testbed.py:54` |
| Renderer, driver, harness | Không áp dụng | Tooling | Không áp dụng | [C] `scripts/aws_demo/README.render.md:77`, `scripts/aws_demo/README.render.md:5` |

API renderer `controller_sidecar` chỉ tạo ConfigMap+S, selector controller nhưng namespace mặc định `sentinelops` khác controller ở b-actions; chưa thấy wiring đồng namespace, chưa có bằng chứng end-to-end (`scripts/render_api_manifests.py:71`, `scripts/render_api_manifests.py:81`, `scripts/render_api_manifests.py:85`, `scripts/render_worker_manifests.py:31`).
Hai collector có pipeline khác nhau; testbed chưa gửi vào collector AWS, không dùng collector nội bộ [A] để nâng collector AWS [B] (`scripts/aws_demo/README.render.md:85`).
Báo cáo ghi 7/7 pod nhưng danh sách thành phần không khớp tổng đó; không suy diễn số pod hiện tại (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:12`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:87`).

## 6. Observability

| Thành phần | Trạng thái theo repo | Bằng chứng path:line |
|---|---|---|
| Prometheus | [A] Image v3.5.0 pin digest; scrape Collector; UI chỉ hướng dẫn truy cập nội bộ | `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:12`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:79`, `scripts/kubernetes_testbed.py:80`, `scripts/kubernetes_testbed.py:88`, `scripts/kubernetes_testbed.py:104`, `observability/prometheus.yml:12` |
| Jaeger | [A] Image 2.21.0 pin digest; Collector nội bộ xuất trace sang Jaeger | `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:12`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:79`, `scripts/kubernetes_testbed.py:80`, `scripts/kubernetes_testbed.py:88`, `scripts/kubernetes_testbed.py:99`, `observability/otel-collector-config.yaml:23`, `observability/otel-collector-config.yaml:34` |
| Grafana EKS | [B] Renderer sinh ConfigMap/S/D/PVC tùy chọn; chưa có URL AWS | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:14`, `scripts/aws_demo/README.grafana.md:3`, `scripts/aws_demo/README.grafana.md:96`, `scripts/aws_demo/render_grafana.py:188`, `scripts/aws_demo/render_grafana.py:227`, `scripts/aws_demo/render_grafana.py:234`, `scripts/aws_demo/render_grafana.py:254` |
| Grafana local | [C] Compose root và lane B; dashboard/checker không chứng minh deploy AWS | `docker-compose.yml:42`, `docker-compose.yml:60`, `docker-compose.b.yml:6`, `docs/runbooks/OBSERVABILITY.md:237`, `docs/runbooks/OBSERVABILITY.md:253`, `docker-compose.yml:65`, `scripts/check_grafana_dashboards.py:1`, `scripts/check_grafana_dashboards.py:6` |
| CloudWatch Logs | [A] Bằng chứng apply tổng hợp; [B] chưa kiểm riêng group/config | `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`, `infra/aws/observability.tf:5`, `infra/aws/observability.tf:13`, `infra/aws/observability.tf:21`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:64` |
| CloudWatch dashboard | [B] Apply lỗi, sửa local chưa apply | `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:136`, `infra/aws/dashboard.tf:72` |
| CloudWatch/X-Ray telemetry delivery | [B] Có cấu hình/quyền; **Không có bằng chứng** delivery/parity; demo dùng Prometheus/Jaeger | `infra/aws/telemetry_exporter.tf:3`, `infra/aws/telemetry_exporter.tf:31`, `infra/aws/telemetry_exporter.tf:46`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:17` |

Các lệnh dưới đây **chỉ để operator tham khảo sau khi có context hợp lệ; chưa chạy**; localhost không phải URL public (`docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:59`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:60`).

```sh
kubectl --context sentinelops-demo -n sentinelops-b-actions port-forward --address localhost svc/prometheus 9090:9090
kubectl --context sentinelops-demo -n sentinelops-b-actions port-forward --address localhost svc/jaeger 16686:16686
# Sau port-forward tương ứng: http://localhost:9090 và http://localhost:16686
```

## 7. UI operator

**Không có URL public UI được chứng minh.** UI/API, CloudFront, Cognito, sidecar và NodePort đều chưa có bằng chứng apply (`scripts/aws_demo/README.ui.md:156`, `scripts/aws_demo/EDGE.md:5`, `infra/aws/operator_identity.tf:131`).
Phương án ghi approval [B] là sidecar `operator-ui` trong pod `remediation-controller`, dùng chung PVC `/artifacts/remediation`; UI/API cùng FastAPI, cổng 8000.
Sidecar **chỉ render khi bật `--demo-ui`, mặc định tắt**; API riêng không mount approval PVC và chỉ là phương án đọc (`scripts/aws_demo/README.ui.md:10`, `scripts/aws_demo/README.ui.md:146`, `scripts/render_worker_manifests.py:333`, `scripts/render_worker_manifests.py:345`, `scripts/render_worker_manifests.py:359`, `src/sentinelops/api.py:150`, `scripts/aws_demo/README.ui.md:40`, `scripts/aws_demo/README.ui.md:44`, `scripts/aws_demo/README.ui.md:50`, `scripts/render_api_manifests.py:81`, `scripts/aws_demo/README.ui.md:62`).

Đường mạng dự kiến [B]: CloudFront → VPC origin → ALB internal → NodePort 30080 → sidecar:8000; đây chưa phải đường truy cập đang hoạt động (`scripts/aws_demo/README.ui.md:5`, `scripts/aws_demo/README.ui.md:18`, `scripts/aws_demo/README.ui.md:50`, `scripts/aws_demo/README.ui.md:59`).
Pool ID, client ID, hosted-login domain và edge domain thực: **Không có bằng chứng**; lấy từ `operator_oidc_binding`, `operator_login_domain`, `edge_domain`, vốn null khi chưa bật tương ứng (`infra/aws/outputs.tf:107`, `infra/aws/operator_identity.tf:130`, `infra/aws/operator_identity.tf:142`, `infra/aws/operator_identity.tf:7`, `infra/aws/operator_identity.tf:25`, `infra/aws/operator_identity.tf:74`, `infra/aws/operator_identity.tf:78`, `infra/aws/operator_identity.tf:82`, `scripts/aws_demo/EDGE.md:93`).

```sh
# Chỉ tham khảo nếu controller/sidecar đã triển khai và context đã được xác minh.
kubectl --context sentinelops-demo -n sentinelops-b-actions port-forward deployment/remediation-controller --address 127.0.0.1 8000:8000
# Khi đó mới thử http://127.0.0.1:8000/ui/
```

**Bẫy Cognito:** phải đăng ký chính xác callback `http://127.0.0.1:8000/ui/callback`; client edge mặc định chỉ đăng ký CloudFront.
Port-forward một mình không làm login hoạt động (`scripts/aws_demo/README.ui.md:143`, `src/sentinelops/ui/routes.py:19`, `src/sentinelops/ui/assets/client.js:80`, `scripts/aws_demo/README.ui.md:149`).
Mã UI [C] dùng Authorization Code + PKCE; token trong RAM; quyết định proposal cần cả `incidents.read` và `remediation.approve`.
POST decision dùng Bearer token và `If-Match` hash, chỉ ghi receipt; **duyệt không chứng minh rollback/recovered** (`scripts/aws_demo/README.ui.md:119`, `scripts/aws_demo/README.ui.md:128`, `src/sentinelops/ui/assets/client.js:192`, `src/sentinelops/api.py:169`, `src/sentinelops/ui/assets/client.js:75`, `scripts/aws_demo/README.ui.md:71`, `scripts/aws_demo/README.ui.md:77`, `scripts/aws_demo/README.ui.md:151`, `src/sentinelops/ui/assets/client.js:164`, `scripts/aws_demo/README.ui.md:91`, `scripts/aws_demo/README.ui.md:94`, `src/sentinelops/api_settings.py:23`, `src/sentinelops/remediation/proposal_api.py:131`).

## 8. Trạng thái luồng tự sửa

```text
[A] Payment +950 ms → OPEN → RCA_READY → ESCALATED → đã gỡ fault
[B/C] UI duyệt → controller rollback → recovered: CHƯA CÓ BẰNG CHỨNG AWS
```

Incident worker được báo cáo `actions_allowed=false`; fault gỡ lúc 06:43:15Z. Chưa có bằng chứng model được nạp hoặc remediation chạy trọn vòng EKS (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:90`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:91`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:162`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:148`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:18`).
Kết quả recovered/telemetry_verified trong checkpoint là sandbox kind local [C], `G6_accepted=false`, không nâng thành AWS (`docs/delivery/LANE_B_CHECKPOINT.md:662`, `docs/delivery/LANE_B_CHECKPOINT.md:676`, `docs/delivery/LANE_B_CHECKPOINT.md:547`, `docs/delivery/AWS_DEMO_PLAN.md:47`).
Hợp đồng hiện hành yêu cầu duyệt ở mọi severity, không có auto; mô tả LOW tự chạy/v2 trong báo cáo AWS đã cũ.
Ngưỡng oncall: score ≥ max(0.80, catalog), margin ≥ max(0.15, catalog), executable và đủ evidence (`docs/design/AUTO_RUNBOOK_INTERFACE.md:9`, `configs/runbook_catalog.json:3`, `configs/severity_policy.json:2`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:103`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:23`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:14`).
Fault RAM qua API không tạo deployment-change và không đủ điều kiện rollback bad-deploy; demo rollback cần image lỗi và known-good đã duyệt trước onset (`scripts/run_runbook_demo.py:99`, `services/common/faults.py:31`, `services/payment/app.py:97`, `configs/runbook_catalog.json:11`, `docs/runbooks/CONTROLLED_REMEDIATION.md:25`, `docs/design/AUTO_RUNBOOK_INTERFACE.md:17`).

## 9. Bảng LINK

**Không tồn tại URL ứng dụng public được bàn giao trong bằng chứng của mốc này.** AWS Console cần identity operator; các link này không phải URL ứng dụng (`docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:16`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:59`, `docs/delivery/AWS_DEMO_PLAN.md:67`, `docs/delivery/AWS_DEMO_PLAN.md:114`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:28`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:38`, `infra/aws/outputs.tf:73`).

| Loại | Link / output cần lấy | Bằng chứng path:line |
|---|---|---|
| EKS Console | [sentinelops-dev](https://ap-southeast-1.console.aws.amazon.com/eks/home?region=ap-southeast-1#/clusters/sentinelops-dev) | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:32` |
| RDS Console | [Databases](https://ap-southeast-1.console.aws.amazon.com/rds/home?region=ap-southeast-1#databases:) | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:33` |
| ECR Console | [Repositories](https://ap-southeast-1.console.aws.amazon.com/ecr/repositories?region=ap-southeast-1) | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:34` |
| S3 Console | [Buckets](https://ap-southeast-1.console.aws.amazon.com/s3/buckets?region=ap-southeast-1); tên thật: output `artifact_bucket` | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:35`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:62`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:560`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:567` |
| Secrets Manager | [Secrets](https://ap-southeast-1.console.aws.amazon.com/secretsmanager/listsecrets?region=ap-southeast-1) | `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:88` |
| Budgets | [Budgets Console](https://ap-southeast-1.console.aws.amazon.com/billing/home?region=ap-southeast-1#/budgets) | `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:68`, `infra/aws/variables.tf:266`, `infra/aws/variables.tf:277` |
| CloudWatch | [Console](https://ap-southeast-1.console.aws.amazon.com/cloudwatch/home?region=ap-southeast-1); lấy `log_group_names`, không suy dashboard tồn tại | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:38`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:28`, `infra/aws/outputs.tf:73` |
| VPC | [Console](https://ap-southeast-1.console.aws.amazon.com/vpcconsole/home?region=ap-southeast-1) | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:39` |
| KMS | [Keys](https://ap-southeast-1.console.aws.amazon.com/kms/home?region=ap-southeast-1#/kms/keys); ARN thật lấy state `aws_kms_key.main` | `infra/aws/eks.tf:87`, `infra/aws/eks.tf:96`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:10` |
| PR GitHub tại mốc đối chiếu | [#77](https://github.com/VietSory/SentinelOps/pull/77), [#76](https://github.com/VietSory/SentinelOps/pull/76), [#75](https://github.com/VietSory/SentinelOps/pull/75), [#74](https://github.com/VietSory/SentinelOps/pull/74), [#71](https://github.com/VietSory/SentinelOps/pull/71), [#70](https://github.com/VietSory/SentinelOps/pull/70) | Không có bằng chứng `path:line` trong file repo cho toàn bộ danh sách PR; các số PR được đối chiếu bằng lịch sử Git, không phải bằng chứng AWS; PR/code không chứng minh apply |
| PR được kế hoạch nhắc | [#68](https://github.com/VietSory/SentinelOps/pull/68) | `docs/delivery/AWS_DEMO_PLAN.md:96` |
| Báo cáo triển khai | [AWS_DEPLOY_REPORT_2026-11-18.md](AWS_DEPLOY_REPORT_2026-11-18.md) | `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:1`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:172`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:183` |
| Truy cập và luồng | [AWS_DEMO_ACCESS_AND_FLOWS.md](AWS_DEMO_ACCESS_AND_FLOWS.md) | `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:1`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:22` |
| Kế hoạch / checkpoint | [AWS_DEMO_PLAN.md](AWS_DEMO_PLAN.md), [LANE_B_CHECKPOINT.md](LANE_B_CHECKPOINT.md) | `docs/delivery/AWS_DEMO_PLAN.md:1`, `docs/delivery/LANE_B_CHECKPOINT.md:1` |
| Hướng dẫn UI / edge / PVC | [README.ui.md](../../scripts/aws_demo/README.ui.md), [EDGE.md](../../scripts/aws_demo/EDGE.md), [PVC_MIGRATION.md](../../scripts/aws_demo/PVC_MIGRATION.md) | `scripts/aws_demo/README.ui.md:156`, `scripts/aws_demo/EDGE.md:5`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:1`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:172`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:183` |
| URL UI/Grafana/Cognito thật | **Không có bằng chứng**; lấy `edge_domain`, `operator_login_domain`, `operator_oidc_binding`, `edge_grafana_client_id` sau triển khai | `infra/aws/outputs.tf:107`, `infra/aws/outputs.tf:112`, `infra/aws/operator_identity.tf:130`, `infra/aws/operator_identity.tf:142` |

Lệnh tra output/state chỉ dành cho operator có môi trường hợp lệ; **chưa chạy**, output không tự chứng minh endpoint sống (`infra/aws/outputs.tf:15`, `infra/aws/outputs.tf:53`, `infra/aws/outputs.tf:58`, `infra/aws/outputs.tf:102`, `infra/aws/outputs.tf:1`, `infra/aws/outputs.tf:63`, `infra/aws/outputs.tf:68`, `infra/aws/outputs.tf:73`, `infra/aws/outputs.tf:82`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:136`).

```sh
terraform -chdir=infra/aws output artifact_bucket
terraform -chdir=infra/aws output log_group_names
terraform -chdir=infra/aws output edge_domain
terraform -chdir=infra/aws output operator_login_domain
terraform -chdir=infra/aws output -json operator_oidc_binding
terraform -chdir=infra/aws state show aws_kms_key.main
```

## 10. Việc còn lại trước demo trọn vòng

| Việc cần hoàn tất | Tiêu chí / giới hạn bằng chứng hiện có |
|---|---|
| Xác minh lại môi trường dev và trạng thái thực | Có credential/context hợp lệ; hiện chỉ có bằng chứng lịch sử, không biết tài nguyên còn tồn tại; `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:3` |
| Nạp model/H4, nâng image worker đã pin | Model chưa có bằng chứng nạp, nâng image từng timeout; `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:13`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:163`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:162`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:18` |
| Provision action-runtime/control_action_writer, known-good | Còn phải chạy action-runtime, thu và duyệt known-good trước lỗi; `scripts/aws_demo/control-job.yaml:4`, `scripts/aws_demo/control-job.yaml:15`, `scripts/aws_demo/control.py:74`, `scripts/aws_demo/control.py:86`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:164`, `scripts/aws_demo/review_job.py:54`, `scripts/aws_demo/review_job.py:59`, `scripts/aws_demo/review_job.py:66`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:165`, `docs/runbooks/CONTROLLED_REMEDIATION.md:76` |
| Deploy controller, UI sidecar và sửa wiring namespace | Chưa deploy controller; cùng PVC approval và đúng namespace; `scripts/render_worker_manifests.py:283`, `scripts/render_worker_manifests.py:375`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:166`, `scripts/render_api_manifests.py:71`, `scripts/render_api_manifests.py:81`, `scripts/render_api_manifests.py:85`, `scripts/render_worker_manifests.py:31`, `scripts/aws_demo/README.ui.md:10`, `scripts/aws_demo/README.ui.md:146` |
| Hoàn tất Cognito/edge/Grafana nếu demo web public | Bật đúng flag, callback, quyền, xác nhận URL thực; chưa có bằng chứng publish/apply; `docs/delivery/AWS_DEMO_PLAN.md:67`, `src/sentinelops/ui/routes.py:19`, `src/sentinelops/ui/assets/client.js:80`, `scripts/aws_demo/README.ui.md:149` |
| Di trú PVC KMS; xử lý dashboard CloudWatch | Kiểm EBS CSI/KMS thực, giữ ledger; dashboard sửa local chưa apply; `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:58`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:136`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:50`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:140` |
| Chốt digest regression tương thích CLI | CLI đòi `/payment-regression@sha256:<64hex>`, báo cáo lại ghi chung repo application; chưa chứng minh dùng thẳng được; `scripts/run_runbook_demo.py:3`, `scripts/run_runbook_demo.py:417`, `docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:75` |
| Thu bằng chứng rollback và các nhánh âm | Recovered + telemetry_verified, đúng 1 dispatch, 3 bucket clear; kiểm reject/verify lỗi/kill switch; `scripts/run_runbook_demo.py:236`, `docs/runbooks/CONTROLLED_REMEDIATION.md:215`, `scripts/run_runbook_demo.py:89`, `scripts/run_runbook_demo.py:221`, `scripts/run_runbook_demo.py:6` |
| Lưu receipt và dọn sau demo | Receipt demo không phải acceptance; teardown chưa có bằng chứng hoàn tất; `scripts/run_runbook_demo.py:479`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:16`, `docs/delivery/AWS_DEMO_ACCESS_AND_FLOWS.md:59`, `docs/delivery/AWS_DEMO_PLAN.md:67`, `docs/delivery/AWS_DEMO_PLAN.md:114` |

## 11. Runbook demo web

Đường dẫn bàn giao theo yêu cầu: [docs/runbooks/DEMO_WEB_UI.md](../runbooks/DEMO_WEB_UI.md).
Runbook là tài liệu hướng dẫn thao tác; liên kết này không chứng minh UI hoặc luồng remediation đã triển khai AWS (`docs/delivery/AWS_DEPLOY_REPORT_2026-11-18.md:153`, `scripts/aws_demo/README.ui.md:3`).
Hướng dẫn UI nằm tại [scripts/aws_demo/README.ui.md](../../scripts/aws_demo/README.ui.md); đây là hướng dẫn [B/C], không phải bằng chứng UI đã triển khai (`scripts/aws_demo/README.ui.md:156`, `scripts/aws_demo/EDGE.md:5`, `scripts/aws_demo/README.ui.md:10`, `scripts/aws_demo/README.ui.md:146`).
