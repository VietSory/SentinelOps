# Dựng mới SentinelOps AWS dev

Quy trình này dùng Terraform và source trong Git, với DB, PVC và image ECR tạo
mới. Bộ biến triển khai nằm trong file `demo.tfvars.json` cục bộ. File có AWS
account, CIDR quản trị và địa chỉ nhận ngân sách, nên giữ ngoài Git. Terraform
state của cụm cũ phục vụ đối chiếu và teardown. Để tạo cụm mới, bắt đầu với một
thư mục state mới dưới Linux/WSL.

## Hạ tầng AWS

Trong WSL tại thư mục gốc repo, đăng nhập đúng AWS CLI profile rồi chạy:

```bash
python3 scripts/aws_demo/rebuild_infra.py --profile sentinelops \
  --inputs /mnt/d/SentinelOps/.local-ops/aws-rebuild/demo.tfvars.json \
  --work-dir "$HOME/sentinelops-dev-rebuild"
```

Lệnh đọc AWS và tạo Terraform plan trong thư mục riêng. Sau khi teardown hoàn
tất và đã kiểm tra plan, thêm `--apply` để tạo hạ tầng. Lệnh kiểm tra account,
từ chối dùng một cụm đang tồn tại bên ngoài state của lần dựng và dừng nếu plan
có thao tác xóa/thay thế. Khi apply thành công, `outputs.json` chứa các endpoint,
ECR URI và OIDC binding mới. Dùng lại thư mục này để tiếp tục nếu lần apply đầu
dừng giữa chừng. Giữ file state của thư mục này ở nơi có quyền truy cập hạn chế.

VPC origin CloudFront bắt đầu ở chế độ bootstrap với distribution tắt. Bật edge
sau khi NodePort, API/UI, Grafana và OAuth đã qua kiểm tra, theo [EDGE.md](EDGE.md).
`edge-config.json` và `plan-review.json` trong thư mục dựng lại cục bộ ghi nhận
định tuyến live và các mục CloudFront/Cognito/ALB cần đối chiếu trước khi publish.
Khi xóa cụm cũ, xử lý riêng bảo vệ xóa RDS/Cognito và giữ bucket chứa state cho
đến khi các bước teardown cần state đã hoàn thành.

## Từ hạ tầng tới demo chạy được

1. Tạo EKS access entries theo nhóm quyền đã phê duyệt. Profile demo hiện có
   `AmazonEKSViewPolicy` toàn cụm và `AmazonEKSEditPolicy` giới hạn namespace
   `sentinelops-b-actions`. Dùng principal bootstrap đã được cấp quyền tạo
   namespace, StorageClass và RBAC cho bước đầu. Dùng kubeconfig mới.
2. Build từ Git bằng [build_images.sh](build_images.sh) và publish qua
   [push_images_ecr.py](../push_images_ecr.py). Dùng digest mới của application
   làm `BASE` để build image Payment regression. Build console EKS bằng
   [Dockerfile của Fault Console](../demo_console/deployment/Dockerfile). Các
   nguồn image công khai cho Postgres, OTel, Prometheus, Jaeger và Grafana được
   ghi theo digest trong cấu hình đã đối chiếu.
3. Bootstrap RDS trắng và tạo credential mới theo [CONTROL_RUNBOOK.md](CONTROL_RUNBOOK.md).
   Khi cấp login demo, truyền `--expires-at` với mốc UTC đã được owner duyệt.
   Dựng volume trống và mount đúng đường dẫn. Dựng model bằng quy trình
   [train, package, review và cấp release](../../docs/runbooks/MODEL_PIPELINE.md)
   rồi nạp vào PVC theo [MODELWIRE.md](MODELWIRE.md).
4. Render lại với Terraform outputs và digest mới theo [README.render.md](README.render.md).
   Đối chiếu cấu hình tại `.local-ops/aws-rebuild/live-config/` trong checkout
   chính. Lấy namespace/Deployment UID và health stream từ cụm mới. Cấu hình
   health và incident hiện dùng subPath lần lượt trên PVC review và model.
   Tạo known-good mới từ baseline Payment đã kiểm tra, bind tới UID thật, rồi
   cấp lại các runtime Secret, Grafana OAuth, tài khoản Cognito operator và quyền demo.
5. Nghiệm thu 14 Deployment sẵn sàng, PVC `Bound`, Grafana nhận p95 và 5xx mới,
   UI/Grafana đăng nhập qua Cognito, Fault Console local kết nối đúng context.
   Chạy một lượt regression được cấp quyền và xác nhận incident, proposal,
   đúng một lần rollback sau khi duyệt, rồi telemetry xác nhận phục hồi.

Lệnh hiện tự động hóa phần hạ tầng. Các bước runtime trên cần được chạy qua
trên môi trường sạch trước khi coi demo dựng lại đã nghiệm thu.
