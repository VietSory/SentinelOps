# Dựng mới SentinelOps AWS dev

Quy trình này bắt đầu từ repo vừa clone và tạo DB, PVC, image ECR cùng Terraform
state mới cho tài khoản của người triển khai. Mẫu cấu hình và các bước runtime
đều có trong repo. Với môi trường đang hoạt động, lưu state của môi trường đó
trong backend riêng có quyền truy cập hạn chế. Terraform state và plan có thể
chứa credential do provider tạo.

## Hạ tầng AWS

Trong Linux/WSL, cài Python 3, AWS CLI và Terraform theo
[`versions.tf`](../../infra/aws/versions.tf). Từ thư mục gốc repo, tạo file
riêng từ [mẫu AWS dev](demo.tfvars.example.json):

```bash
test -e "$HOME/sentinelops-demo.tfvars.json" ||
  install -m 600 scripts/aws_demo/demo.tfvars.example.json "$HOME/sentinelops-demo.tfvars.json"
```

Điền AWS account ID đã được cấp quyền, email nhận cảnh báo ngân sách, và
`name_prefix`/VPC CIDR phù hợp tài khoản. Xác minh quota, loại instance và phiên
bản EKS, RDS cùng addon trong region. Mẫu bật endpoint riêng. Để dùng kubectl
từ WSL bên ngoài VPC, dùng đường kết nối riêng đã cấp hoặc bật
`cluster_public_access` với CIDR quản trị giới hạn trong
`cluster_public_access_cidrs`. Chọn đúng AWS CLI profile đã đăng nhập rồi lập plan:

```bash
python3 scripts/aws_demo/rebuild_infra.py --profile your-aws-profile \
  --inputs "$HOME/sentinelops-demo.tfvars.json" \
  --work-dir "$HOME/sentinelops-dev-rebuild"
```

Lệnh đọc AWS và tạo Terraform plan trong thư mục riêng. Khi đã kiểm tra plan
và được phép tạo tài nguyên trong tài khoản đó, thêm `--apply`. Lệnh kiểm tra account,
từ chối dùng một cụm đang tồn tại bên ngoài state của lần dựng và dừng nếu plan
có thao tác xóa/thay thế. Khi apply thành công, `outputs.json` chứa các endpoint,
ECR URI và OIDC binding mới. Dùng lại thư mục này để tiếp tục nếu lần apply đầu
dừng giữa chừng. Giữ file state của thư mục này ở nơi có quyền truy cập hạn chế.

VPC origin CloudFront bắt đầu ở chế độ bootstrap với distribution tắt. Bật edge
sau khi NodePort, API/UI, Grafana và OAuth đã qua kiểm tra, theo [EDGE.md](EDGE.md).
Tham chiếu [bản kiểm kê trước teardown](../../docs/delivery/AWS_TERRAFORM_STATE_REDACTED.md)
khi cần đối chiếu lịch sử triển khai.
Khi xóa cụm cũ, xử lý riêng bảo vệ xóa RDS/Cognito và giữ bucket chứa state cho
đến khi các bước teardown cần state đã hoàn thành.

Khi chạy thử song song trong cùng tài khoản, dùng `name_prefix` và VPC CIDR riêng.
Chỉ bật `database_skip_final_snapshot` và tắt `database_deletion_protection` cùng
`operator_deletion_protection` cho môi trường bỏ đi. Khi bootstrap DB thử, truyền
`--runtime-secret-name` khớp tên trong Terraform. Kiểm tra image bằng cùng UID
10001 mà EKS dùng trước khi rollout.

## Từ hạ tầng tới demo chạy được

1. Tạo EKS access entries cho principal của môi trường mới. Quyền demo tham
   chiếu là `AmazonEKSViewPolicy` toàn cụm và `AmazonEKSEditPolicy` giới hạn
   namespace `sentinelops-b-actions`. Dùng principal bootstrap đã được cấp quyền tạo
   namespace, StorageClass và RBAC cho bước đầu. Dùng kubeconfig mới.
2. Build từ Git bằng [build_images.sh](build_images.sh) và publish qua
   [push_images_ecr.py](../push_images_ecr.py). Dùng digest mới của application
   làm `BASE` để build image Payment regression. Build console EKS bằng
   [Dockerfile của Fault Console](../demo_console/deployment/Dockerfile).
   [Mẫu cấu trúc image](k8s-images.example.json) minh họa các key của renderer
   và có digest giả để test offline. Với bản dựng thật, lấy digest ECR từ
   lượt push mới. Dùng `docker buildx imagetools inspect <image:tag>` để tra
   digest các image công khai theo tag đã duyệt, rồi điền các trường còn lại.
   [Runbook Grafana](README.grafana.md) có pin riêng cho Grafana.
3. Bootstrap RDS trắng và tạo credential mới theo [CONTROL_RUNBOOK.md](CONTROL_RUNBOOK.md).
   Khi cấp login demo, truyền `--expires-at` với mốc UTC đã được owner duyệt.
   Dựng volume trống và mount đúng đường dẫn. Dựng model bằng quy trình
   [train, package, review và cấp release](../../docs/runbooks/MODEL_PIPELINE.md)
   rồi nạp vào PVC theo [MODELWIRE.md](MODELWIRE.md).
4. Render lại với Terraform outputs, digest mới và
   [mẫu runtime](k8s-runtime.example.json) theo [README.render.md](README.render.md).
   Lấy namespace/Deployment UID và health stream từ cụm mới. Với cấu hình
   health và incident dùng subPath trên PVC review và model, tạo thư mục mới
   và xác minh quyền ghi của worker trước khi rollout.
   Tạo known-good mới từ baseline Payment đã kiểm tra, bind tới UID thật, rồi
   cấp lại các runtime Secret, Grafana OAuth, tài khoản Cognito operator và quyền demo.
5. Nghiệm thu 14 Deployment sẵn sàng, PVC `Bound`, Grafana nhận p95 và 5xx mới,
   UI/Grafana đăng nhập qua Cognito, Fault Console local kết nối đúng context.
   Chạy một lượt regression được cấp quyền và xác nhận incident, proposal,
   đúng một lần rollback sau khi duyệt, rồi telemetry xác nhận phục hồi.

### Cảnh báo sớm, email và Payment v2 (nâng cấp demo 11/2026)

Thêm vào các bước trên, theo [DEMO_UPGRADE_PLAN](../../docs/delivery/DEMO_UPGRADE_PLAN.md):

1. **Email nhận cảnh báo** đặt ngoài Git, ví dụ file bị ignore
   `infra/aws/demo-alerts.auto.tfvars` với `demo_alert_emails = ["<địa chỉ>"]`,
   hoặc biến môi trường `TF_VAR_demo_alert_emails`. Plan sẽ có thêm topic SNS,
   subscription, role `alerts_publisher` và Pod Identity association. Sau apply,
   mở hộp thư và bấm **Confirm subscription**; chưa xác nhận thì không có email.
2. **Image:** control-plane build từ commit mới đã chứa `run_risk_worker.py` và
   `run_notifier.py`. Build thêm Payment v2 chậm dần bằng
   `docker/Dockerfile.payment-gradual-regression` với `--build-arg BASE=<digest application>`,
   push vào cùng repo `sentinelops/application`; trước khi build chạy
   `python -m scripts.check_payment_regression_image --base <digest>` cho bản regression cũ.
3. **Render worker** thêm `--early-warning --ui-url https://<edge_domain>/ui/
   --alerts-otlp-endpoint http://otel-collector.sentinelops-b-actions.svc.cluster.local:4317
   --alerts-topic-arn $(terraform -chdir=infra/aws output -raw demo_alerts_topic_arn)`
   (cần `--model-claim` và `--model-release-ref`). Pod incident-worker có thêm
   container `risk-worker`; pod remediation-controller có thêm `notifier`.
   Log `notifier identity arn:aws:sts::…:assumed-role/<prefix>-alerts-publisher/…`
   xác nhận Pod Identity hoạt động.
4. **Fault Console:** đặt `DEMO_CONSOLE_GRADUAL_SCRIPT` (script deploy Payment v2 bằng
   runner với `--scenario gradual-regression --prometheus <url> --image-params '<json>'`)
   và `DEMO_CONSOLE_RESET_SCRIPT` (đưa Payment về known-good, tắt fault, chờ khỏe).
5. **Nghiệm thu L2/L3** theo [DEMO_UPGRADE_SPEC](../../docs/delivery/DEMO_UPGRADE_SPEC.md) §1.5:
   một giờ khỏe rồi `python -m scripts.check_risk_quiet --prometheus <url> --minutes 60`;
   sau đó chạy runner và `python -m scripts.check_demo_round <out>/round-*.json --require 3`.
   Panel "SentinelOps — Cảnh báo sớm" trên Grafana phải có số liệu, UI hiện
   "Chờ duyệt từ …" thay cho đồng hồ.

Lệnh hiện tự động hóa phần hạ tầng. Các bước runtime trên cần được chạy qua
trên môi trường sạch trước khi coi demo dựng lại đã nghiệm thu.

## Tái tạo dữ liệu và model từ nguồn

- **Model AWS/dev:** dùng bảng MSRTMCR của
  [Alibaba Cluster Trace 2022](https://github.com/alibaba/clusterdata/tree/master/cluster-trace-microservices-v2022).
  [Model pipeline](../../docs/runbooks/MODEL_PIPELINE.md) có lệnh aggregate,
  fit, package và các cổng duyệt. [Báo cáo G3 của `de38fc6b`](../../docs/reports/G3_CANDIDATE_ALIBABA_DE38FC6B.md)
  ghi manifest và phạm vi 240 archive của lượt đã đo. Nếu cần lặp đúng lượt đó,
  phải có danh sách archive và checksum của lượt gốc. Dùng release đã duyệt và
  checksum xác minh khi cần chạy đúng bundle cũ.
- **RCAEval:** dùng revision đã pin trong [catalog nguồn public](../../configs/public_datasets.json).
  Từ repo đã cài dependency, chạy `python -m scripts.download_public_data --dataset rcaeval`
  cho các ca mặc định, rồi xem [runbook đánh giá RCA](../../docs/runbooks/RCAEVAL_NATIVE.md)
  và [partition đã khóa](../../docs/runbooks/RCAEVAL_PARTITIONS.md).
- **Dữ liệu lịch sử:** GAMMA và SMD phục vụ đối chiếu lịch sử. Nguồn, vai trò
  và giới hạn sử dụng được ghi trong [hướng dẫn dữ liệu](../../docs/guide/02_AI_TRAINING_DATA.md).
  SMD có lệnh `python -m scripts.download_public_data --dataset smd --historical-reproduction`.
  [Báo cáo và receipt](../../docs/reports/README.md) ghi các kết quả cần trích dẫn.
