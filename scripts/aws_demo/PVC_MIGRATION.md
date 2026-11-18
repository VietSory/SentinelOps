# Di trú artifact PVC sang gp3 + KMS, giữ nguyên ledger

Đây là hướng dẫn để operator thực hiện sau review; lane `iacfix` chỉ sửa và kiểm
tra offline, chưa di trú PVC trên EKS. Mọi lệnh ghi AWS, kể cả CLI và thay đổi
Kubernetes cấp phát/xóa EBS, phải qua self-review + Codex **xhigh**, hết BLOCK
trước khi chạy. Review phải bao gồm đúng manifest, image digest, context,
namespace, KMS ARN, cặp PVC/PV và kế hoạch khôi phục.

## 1. Chuẩn bị và chốt bản sao nhất quán

- Lập inventory PVC → PV → EBS volume ID, capacity, AZ/node affinity, reclaim
  policy, trạng thái mã hóa/KMS và mọi pod/Job/CronJob đang mount. Không suy ra
  mã hóa từ tên `gp2`: xác nhận `Encrypted` và `KmsKeyId` của volume thực.
- Dừng traffic gây thay đổi deployment; tắt dispatch/kill switch theo runbook.
  Scale health, incident, remediation-controller và mọi writer khác về 0;
  suspend CronJob, dừng Job `review`/`approve`, recorder deployment-change,
  API duyệt incident và các trial/demo writer. Chờ pod kết thúc, không chỉ
  scale Deployment rồi bỏ qua Job. Lưu replica/policy/config cũ để khôi phục.
- Sao lưu control DB nhất quán tại điểm dừng cùng toàn bộ năm artifact store,
  cấu hình stream/consumer/cursor, các artifact reference và approval ledger.
  Không reset DB, không xóa/rebootstrap migration ledger. Snapshot EBS cũ và
  archive dự phòng phải được sao chép/mã hóa bằng KMS; kiểm tra đọc/restore
  được và giữ key. DB giữ các reference tới file nên hai phần phải cùng mốc.
- Sau review, đổi reclaim policy của **PV nguồn** sang `Retain` và ghi lại
  volume ID trước khi đụng tới PVC. Không xóa PVC/PV nguồn để đổi storage class;
  `storageClassName` là immutable. Giữ bản nguồn đến khi operator nghiệm thu.

## 2. Tạo PVC đích riêng

Dùng [StorageClass hiện có](../../infra/kubernetes/sentinelops-gp3-kms.storageclass.yaml).
Thay placeholder `kmsKeyId` bằng ARN key đúng môi trường lấy từ output/state
đã có; kiểm tra EBS CSI Pod Identity và quyền dùng key đã cấp. Không commit
state, credential hay bản render chứa dữ liệu riêng. Class dùng `gp3`,
`encrypted: "true"`, `WaitForFirstConsumer`, reclaim policy `Delete`.

[artifact-pvcs-kms.yaml](artifact-pvcs-kms.yaml) tạo năm PVC cùng namespace
`sentinelops-b-actions` với tên mới để cùng tồn tại với PVC nguồn:

| Nguồn | Đích | Consumer / binding cần đổi |
| --- | --- | --- |
| `health-artifacts` | `health-artifacts-kms` | health, incident, controller, review/approve; `health_store_claim` |
| `incident-artifacts` | `incident-artifacts-kms` | incident, controller/API nếu mount; `incident_store_claim` |
| `remediation-artifacts` | `remediation-artifacts-kms` | controller, trial/API nếu mount; `remediation_store_claim` |
| `review-artifacts` | `review-artifacts-kms` | review/approve, controller; `known_good_store_claim` |
| `deployment-change-artifacts` | `deployment-change-artifacts-kms` | incident reader, deployment-change recorder/demo writer; `deployment_change_store_claim` |

Request `5Gi` chỉ là điểm khởi đầu: tăng từng claim tới ít nhất capacity nguồn
và đủ dữ liệu + headroom **trước khi provision**. Fresh install cũng dùng các
tên `-kms` và binding ở trên; không mount nhầm PVC cũ mặc định của renderer.
PVC Pending khi chưa có consumer là bình thường với `WaitForFirstConsumer`.

## 3. Chép và đối chiếu trước cutover

1. Chọn một AZ/node cho tất cả PVC đích sẽ được các worker cùng mount. EBS RWO
   chỉ attach được một node; health/incident/controller/review phải đặt cùng
   node khi chia sẻ health PVC (mount read-only vẫn chịu giới hạn này).
2. Với từng cặp cùng AZ, tạo pod copy tạm (image có GNU tar/coreutils, pin
   digest đã review), mount nguồn **read-only** `/old`, đích `/new`, filesystem
   root của PVC ở cả hai đường dẫn. Dùng node affinity theo
   `kubernetes.io/hostname`/zone phù hợp PV nguồn; không đặt `spec.nodeName`
   vì sẽ bỏ qua scheduler và chặn binding `WaitForFirstConsumer`. Không mount
   Secret DB, không cấp SA token hay quyền AWS cho pod copy. Chờ volume detach
   khỏi consumer cũ rồi mới attach vào node copy.
3. Nếu nguồn ở nhiều AZ, không thể mount tất cả vào một node hoặc chép trực
   tiếp cặp khác AZ. Export từng nguồn tại AZ của nó thành archive mã hóa +
   checksum, import vào PVC đích ở **AZ chung đã chọn**, rồi đối chiếu inventory
   nguồn. Dùng kênh backup đã review, không tạo bucket/quyền AWS tự phát.
4. Chép **toàn bộ gốc PVC**, giữ relative path, bytes, mode, UID/GID, timestamps,
   hardlink/symlink, thư mục ẩn, journal/cursor/ledger và mọi `manifests`, `raw`,
   `signals`… Không chỉ chép file ledger hoặc thư mục có vẻ đang dùng. Đích
   phải mới, chưa có dữ liệu ứng dụng. Không dùng lệnh sync có `--delete`.

Ví dụ chạy **trong pod copy** đã được review, khi mọi writer đã dừng (không
chạy trên máy operator). Lệnh thất bại ở bất kỳ bước nào thì giữ nguyên nguồn:

```bash
set -euo pipefail
# /tmp là emptyDir riêng; chỉ bỏ lost+found do filesystem tự tạo.
tar --acls --xattrs --numeric-owner --exclude='./lost+found' -C /old -cpf - . \
  | tar --acls --xattrs --numeric-owner -C /new -xpf -
sync
(cd /old && find . -path ./lost+found -prune -o -type f -print0 \
  | LC_ALL=C sort -z | xargs -0 -r sha256sum --zero) > /tmp/source.sha256z
(cd /new && find . -path ./lost+found -prune -o -type f -print0 \
  | LC_ALL=C sort -z | xargs -0 -r sha256sum --zero) > /tmp/target.sha256z
cmp /tmp/source.sha256z /tmp/target.sha256z
# So sánh path/type/mode/owner/symlink, gồm cả thư mục rỗng.
for side in old new; do
  (cd /"$side" && find . -path ./lost+found -prune -o \
    -printf '%P\t%y\t%m\t%U\t%G\t%l\0' | LC_ALL=C sort -z) > /tmp/"$side".metadata
done
cmp /tmp/old.metadata /tmp/new.metadata
```

Lưu hai inventory/checksum và log copy vào kho bằng chứng đã mã hóa trước khi
xóa pod. Mount copy bằng root chỉ để giữ numeric ownership; không `chown -R`
nguồn. Xác nhận UID/GID `10001` của workload đọc/ghi đúng quyền trên đích.
Kiểm tra PV đích → EBS thật có `Encrypted=true`, type `gp3` và **đúng KMS key**;
tên StorageClass một mình chưa chứng minh điều đó.

## 4. Đổi binding, nghiệm thu, khôi phục

- Render manifest mới, giữ nguyên mount path (`/artifacts/health`, incident,
  remediation, review, `/artifacts/deployment-changes`), chỉ đổi `claimName`
  theo bảng. Worker CLI `scripts/render_worker_manifests.py` nhận đủ năm flag
  `--health-store-claim`, `--incident-store-claim`, `--remediation-store-claim`,
  `--known-good-store-claim`, `--deployment-change-store-claim`.
  `render_all.py` hiện **không forward** `deployment_change_store_claim`:
  dùng worker CLI trực tiếp với input thật đã review hoặc sửa binding trong
  bản manifest render riêng rồi review; không chỉ thêm key runtime và cho là đủ.
  `review_job.py` nhận `health_store_claim`/`known_good_store_claim` từ runtime;
  cập nhật cả Job/ConfigMap approve, API duyệt và demo/recorder dùng PVC.
- Trước khi bật writer, mở lại các reference đã lưu bằng
  `sentinelops.telemetry.store.ArtifactStore.read()` trên đích. Kiểm checksum
  từng artifact, known-good ledger và incident approval ledger nếu có;
  đối chiếu DB migration ledger, incident ID/revision, stream, cursor và
  deployment-change reference. Không tạo ledger mới để che file bị thiếu.
  Hết hạn TTL/đổi revision vẫn phải xin duyệt mới theo policy, không tự gia hạn.
- Gỡ pod copy để release attachment; đưa worker lên theo thứ tự dependency,
  health rồi incident rồi review/controller; kiểm một tick ghi bền và đọc lại
  sau restart, không lặp/mất incident. Giữ kill switch/dispatch tắt cho đến khi
  xác minh mọi consumer đã dùng PVC mới và operator nghiệm thu.
- Nếu lỗi **trước khi có ghi mới**, dừng consumer và đổi binding về nguồn.
  Nếu đích đã phát sinh dữ liệu, dừng mọi writer, backup đích và reconcile
  dữ liệu/DB cursor tới cùng mốc trước khi quay lại; không mount nguồn cũ rồi
  bỏ phần ledger mới. Giữ cả hai bản đến khi xác minh không thiếu reference.
- Chỉ sau nghiệm thu và backup/restore đã kiểm tra mới review việc xóa nguồn.
  `Retain` để lại PV/EBS tính phí: xóa riêng sau quyết định retention. PVC mới
  dùng `Delete`: xóa claim có thể xóa volume thật. Ghi inventory cả cũ/mới,
  snapshot/archive và KMS vào checklist teardown của hai tài liệu delivery.
