# Di trú health stream epoch — 19/11/2026

**Di trú epoch ĐÃ THÀNH CÔNG: lượt B7 chạy thật 09:47–09:56 UTC ngày 19/11/2026 đã chứng minh capture v6, `model_evidence`, receipt hợp lệ và tạo đề xuất runbook đầu tiên.** RCA không abstain; Payment đứng đầu với coverage **0,80**, score **0,7974433166482036**, margin **0,39658798795773287**. Mốc cập nhật cuối là **09:58:36 UTC**, khi controller rollout xong sau khi owner bật `--policy-enabled` lúc **09:58:23 UTC**; **policy đang BẬT**.

**Đề xuất CHƯA ĐƯỢC DUYỆT; rollback tự động CHƯA CHẠY.** Owner chưa có mã MFA nên chưa bấm **Duyệt** trên website. Đề xuất có TTL **15 phút**, hết hạn khoảng **2026-11-19T10:10:52Z**; phải chạy lại B7 để sinh đề xuất mới cho lượt kiểm chứng phê duyệt/rollback. Khôi phục image gốc lúc 09:55:59.088 là thao tác kết thúc lượt B7, không phải executor rollback theo đề xuất.

Tài liệu tổng hợp các báo cáo nội bộ E1 (phân tích/kế hoạch epoch), B1 (snapshot), B2 (probe recorder), B3 (render), B4 (chuyển stream), R1 (review) và số đo B7 do operator bàn giao trong yêu cầu cập nhật (**LIVE-19-B7**). Đây là kết quả được bàn giao, không phải một lượt chạy EKS mới của người soạn. Bản cập nhật được soạn tại branch `feat/grafana-incident-dashboard`, commit nền `a16eeb9`; các tham chiếu source của báo cáo trước được đối chiếu tại `e2f740bb8267497725ed42ede40f034dc642870d` (`e2f740b`), PR **#81**. Mọi mốc giờ dưới đây dùng **UTC ngày 19/11/2026**. Môi trường: AWS profile `console`, EKS `sentinelops-dev`, context `sentinelops-demo`, namespace `sentinelops-b-actions`.

## 1. Vì sao phải di trú

**Trước di trú, cả ba kịch bản KB1–KB3 đã chạy thật, đều đến `ESCALATED` và không có proposal (đề xuất khắc phục).** Đây là kết quả lịch sử; không có nghĩa ba sự cố vẫn đang hoạt động. Snapshot B1 sau đó ghi nhận 5 incident trong DB, tất cả đã `recovered`/`CLOSED`. B7 là kịch bản thứ tư, lần đầu sinh proposal; vòng tự sửa vẫn chưa thực hiện rollback.

Ở KB3, RCA abstain: bằng chứng chỉ đạt **coverage 0,40**, trong khi policy yêu cầu **coverage ≥ 0,75**, **score ≥ 0,75** và **margin ≥ 0,15**. Số liệu Payment trong KB3 giải thích cụ thể điểm thiếu:

| Thành phần RCA | Trọng số | Giá trị trong bằng chứng KB3 | Đóng góp coverage |
| --- | --- | --- | --- |
| Anomaly strength / Isolation Forest (IF) | 0,25 | `null`: chưa có IF trong capture | 0 |
| Temporal precedence | 0,20 | `null`: onset chồng lấn, chưa xác định thứ tự | 0 |
| Dependency trace relevance | 0,25 | 0,9960943624 | 0,25 |
| Log support | 0,15 | 1 | 0,15 |
| Recent deployment change | 0,15 | `null`: thiếu receipt deployment-change | 0 |

Coverage là tổng trọng số các thành phần có giá trị khác `null`, kể cả giá trị bằng 0; score là tổng `weight × value`. Không chia lại trọng số khi thiếu bằng chứng (`src/sentinelops/incidents/rca.py:48`, `:84`, `:85`; `configs/rca_policy.json:3`). Vì vậy:

- Trace + log: **0,25 + 0,15 = 0,40** coverage; score = `0,25 × 0,9960943624 + 0,15 × 1` ≈ **0,3990235906**.
- Thêm model có inference `AVAILABLE` hợp lệ: coverage có thể lên **0,40 + 0,25 = 0,65**, vẫn dưới 0,75. **Model một mình KHÔNG đủ.**
- Thêm receipt deployment-change hợp lệ: dự đoán **0,40 + 0,25 + 0,15 = 0,80** coverage. **B7 đã đo đúng 0,80** trên incident thuộc epoch mới, xác nhận phép tính trước đó.

Model IF cung cấp giá trị `anomaly_strength`; risk từ XGBoost bổ sung fact, không có trọng số RCA riêng (`src/sentinelops/workers/model_signals.py:248`, `:250`). Model đã nạp và readiness báo `dependencies.model=true` không chứng minh incident-worker đã sinh capture có model hoặc RCA đủ điều kiện. R1 đã chỉ ra tài liệu cũ ghi KB2/KB3 chưa chạy, model chưa ready là sai trạng thái; tài liệu này dùng kết quả đã chạy được bàn giao và giữ riêng giới hạn bằng chứng của epoch mới.

## 2. Vì sao không thể chỉ thêm model vào incident-worker

Consumer đang tồn tại được định danh từ `consumer_version` và `health_stream_id` (`src/sentinelops/workers/incident_journal.py:41`). Identity của consumer còn chứa `evidence_profile` (`:45`); thêm `ModelIncidentEvidence` đưa `model_release_ref` vào profile (`src/sentinelops/workers/model_signals.py:91`). Cùng stream sẽ giữ cùng consumer ID nhưng làm identity khác cấu hình đã được chấp nhận.

`initialize()` giữ bản ghi cũ bằng `ON CONFLICT DO NOTHING` rồi so identity (`src/sentinelops/workers/incident_journal.py:66`, `:70`). Khi chỉ thêm model trên stream cũ, nó sẽ ném lỗi tại **`src/sentinelops/workers/incident_journal.py:71`**:

```text
ValueError: consumer configuration changed; explicit migration is required
```

CLI chỉ hiện `incident worker failed: ValueError` (`scripts/run_incident_worker.py:200`). Probe `--status` có thể báo trước lỗi `consumer checkpoint is missing, rewound or configured differently` (`src/sentinelops/workers/incident_journal.py:84`). Đây là bảo vệ identity/lịch sử, không phải lỗi có thể giải quyết bằng restart.

Phương án là dùng **health artifact root mới**, để HealthWorker tạo stream identity mới, sau đó tạo consumer mới với evidence profile có model. Health stream ID được sinh từ identity, trong đó có root; đổi metrics-instance hoặc restart đơn thuần không tạo epoch. Health CLI không có cờ `--epoch`; helper render chỉ sửa manifest, stream thật được tạo khi health-worker chạy. ID phải lấy từ log `READY` và xác nhận có bucket `COMMITTED`.

Không xóa/copy checkpoint, sửa identity hay làm SQL migration để ép qua gate. Stream mới không kế thừa episode/window/lịch sử của stream cũ; DB, PVC và artifact cũ được giữ lại. Đường capture là Kubernetes collector → `DeploymentIncidentEvidence` → `ModelIncidentEvidence` (`scripts/run_incident_worker.py:73`, `:83`, `:86`). Wrapper model đổi v5 thành v6 và thêm `model_evidence` (`src/sentinelops/workers/model_signals.py:116`); capture B7 tại mục 4 đã xác nhận kết quả chạy thật có cả model và receipt.

## 3. Lịch sử triển khai: B1–B4

### B1 — Snapshot và đường khôi phục đã chuẩn bị

Báo cáo B1 xác nhận đủ **9/9 mục**: desired/live args, env, probes, image digest, Deployment/ConfigMap, stream/consumer, checkpoint/ledger, PVC/PV/VolumeAttachment/node và trạng thái incident. **7/7 PVC Bound**; 6 PVC worker/model cùng node. Đối chiếu cuối **08:27 UTC** xác nhận spec, UID, digest, ConfigMap, PVC và ref khớp; 5 revision incident cùng checkpoint B1 còn nguyên trong khi worker tiếp tục chạy.

Đã viết **`ROLLBACK.md` trong bộ snapshot nội bộ**, có thứ tự dừng/khôi phục/khởi động, payload toàn spec kèm **UID guard**, đối chiếu UID từng tài nguyên trong phạm vi khôi phục, trả lại image/ref cũ và kiểm tiến độ worker. **Chưa thực thi rollback.** Snapshot được chụp nhiều thời điểm khi hệ thống đang chạy, không phải backup vật lý RDS/EBS; khôi phục cấu hình không tua lùi checkpoint hoặc lịch sử đã ghi.

### B2 — Đã tìm được đường recorder hợp lệ sẵn có

E1 ban đầu nêu khoảng trống của review CLI: nhánh `--in-cluster-worker` từ chối `open-change`/`complete-change`, còn nhánh mặc định chỉ cho `local`. B2 xác định đã có đường hợp lệ qua **`Backend.deploy()` trong pod demo-runner**, nên **KHÔNG phải sửa guard nào**.

`Backend.deploy()` gọi recorder trực tiếp: mở baseline → kiểm resourceVersion → dry-run PATCH → PATCH thật → poll complete mỗi 2 giây, timeout 150 giây (`scripts/run_runbook_demo.py:284`). Pod `demo-runner-sh259` đang có mount RW vào PVC `deployment-change-artifacts`; incident-worker đọc cùng PVC qua `/artifacts/deployment-changes` ở chế độ RO. Receipt do tiến trình recorder ghi, Kubernetes không tự tạo receipt này.

Bản script trong ConfigMap mà runner dùng ở B2 đã có kiểm image khác digest/cùng repository; B2 không đề nghị chỉnh guard mới. B2 chỉ kiểm code/cấu hình và GET live, **chưa chạy recorder hoặc PATCH tại bước này**. Driver đầy đủ có các bước chờ proposal/approval/rollback, mặc định có nhiều vòng; không có mode CLI receipt-only riêng. B2 mới xác định đường gọi hợp lệ; **B7 sau đó đã chứng minh receipt được sinh và ghép vào incident**, xem mục 4.

### B3 — Render và server dry-run PASS 3/3

Lúc **08:31 UTC**, ba manifest Deployment cho health-worker, incident-worker và remediation-controller đã render, **server dry-run PASS 3/3**, stderr rỗng. B3 chưa apply; root mới vẫn chưa tồn tại tại bước kiểm này.

- Health: root mới trong args và probes; giữ profile `prometheus-increase-60s-5s/v2`.
- Incident: source root mới trong args/probes; thêm model release và PVC `model-de38fc6b` mount `/models` RO; giữ incident store `/artifacts/incidents`.
- Controller: source root/stream mới; giữ model release, sidecar operator-ui và cấu hình liên quan.
- Giữ UID, PVC/mount cũ, digest và các cấu hình bảo vệ; bổ sung `PGSSLROOTCERT` cho health/incident. Policy tắt tại bước B3. Stream còn là placeholder để B4 thay bằng ID từ `READY`.

Dry-run chỉ chứng minh API/admission chấp nhận manifest, **không chứng minh inference hoặc readiness của epoch mới**.

### B4 — Đã tạo stream mới và chuyển worker

| Thuộc tính | Giá trị đã ghi nhận |
| --- | --- |
| Health root cũ | `/artifacts/health` |
| Stream cũ | `6028fde726c16b94bd0d5df07e4ae5cc2baa69cc5aec763a08359750f01aecaf` |
| Consumer cũ | `37c81847ab7eff6eedc7e33c5b8c6334d7bc150c846ba6fc46dfb86f7c525d4e` |
| Health root mới | `/artifacts/health/e1-model-de38fc6b-20261119` |
| Stream mới | `806d288ae147ab38977cbbac07d8cd0140a1e0347a32d043a89486637bc31e4d` |
| Consumer mới | `95b77490ed80b5682eaf3ec06340b76205d3df897c7c11bf4f4969fee6df4c50` |
| Model release | `artifact://manifests/092290cedcea07578f664aa670eabf236f66ce7db27ebf159c2c386c3ee5216b.json` |
| Profile | `prometheus-increase-60s-5s/v2` |

Stream mới được lấy từ log **`READY` lúc 08:40:41 UTC**, bucket `COMMITTED` đầu có mốc **08:40:00 UTC**. Hậu kiểm ghi nhận **5 bucket/15 signal**, đến **08:44:00 UTC**; checkpoint consumer cùng mốc với health. ConfigMap `sentinelops-workers-runtime.SENTINELOPS_HEALTH_STREAM` và explicit env của incident-worker đã được cập nhật.

Thứ tự apply thực tế là **health → incident → controller**, đợi incident `READY`/checkpoint trước khi apply controller vì controller chỉ đọc consumer đã tồn tại (`scripts/run_remediation_controller.py:106`). B4 không scale controller về 0 như trình tự dự kiến ban đầu; giữ pod cũ khi bootstrap rồi rollout controller một lần.

Hậu kiểm: health **1/1**, incident **1/1**, controller/UI **2/2 Ready**; 4 container có `restartCount=0`. Incident có log `READY`/`PROGRESS`/`INGESTED`, không thấy `ValueError`, lỗi consumer identity, `Traceback` hay `worker failed`. Website trả **HTTP 200 trước, sau rollout và cuối hậu kiểm**. Đây là các lần kiểm riêng; strategy `Recreate` có thể gây gián đoạn UI ngắn và báo cáo không đo liên tục.

Identity cũ, revision incident và checkpoint B1 còn nguyên; PVC Bound/UID/PV, image ba service, ledger ref/approver được giữ. **Tại mốc B4**, policy vẫn **false**; B4 không tiêm lỗi, không đổi image Payment, không sửa ledger và không kích hoạt rollback. B4 mới chứng minh việc chuyển epoch; **B7 đã bổ sung bằng chứng capture v6/model**, và owner bật policy sau đó lúc 09:58:23.

## 4. B7 — ĐÃ CHỨNG MINH đường bằng chứng và đề xuất đầu tiên

Snapshot B4 lúc 08:46:07 có **0 job, 0 capture** vì các service khỏe; đó là trạng thái lịch sử. B7 chạy thật trong khoảng **09:47–09:56**, tạo incident mới trên epoch đã di trú. Toàn bộ số đo và artifact trong mục này lấy từ **LIVE-19-B7**.

### Timeline và số đo B7

| Thời điểm UTC ngày 19/11/2026 | Thao tác / quan sát | Kết quả đo |
| --- | --- | --- |
| **09:48:50.005** | Mở baseline deployment-change | Snapshot ghi `generation=4`, image gốc `sha256:e520e70255f4763de7c7ad4bb9053452e5b93ec0716fc5a5e1ea7a24caabb28c` |
| **09:48:57.029** | PATCH image regression | `sha256:766dd5f5a32eba9950c4854451f6f6891483d82df735ac93c7046597af7584e0`; generation **4 → 5** |
| **09:48:57.468** | Receipt `recorded_at` | Receipt đã được ghi; ref đầy đủ ở bảng artifact bên dưới |
| **09:48:57.478** | `CHANGE_OBSERVED` | Khoảng cách từ PATCH theo số đo được bàn giao **0,438842 giây**, trong cửa sổ cho phép **2,75 giây**; baseline → after **7,462369 giây** |
| **Sau khi ghi receipt; không có mốc kiểm feed riêng** | Kiểm `/artifacts/deployment-changes` | **4 file**: **2 snapshot + 1 baseline + 1 receipt** |
| **09:51:00** | Onset theo bucket health | Tách biệt với thời điểm mẫu Prometheus đầu tiên bên dưới |
| **09:51:43.606** | Mẫu `slo_latency_breach=1` đầu tiên | Cả **Payment và Checkout**; **không có error breach** |
| **09:55:30.908** | Tạo incident trong DB | Severity **`high`**; ID đầy đủ ở bảng artifact bên dưới |
| **09:55:30.913** | Lifecycle `OPEN` | Revision **1** |
| **09:55:32.987** | Capture `analyzed_at` | **`continuous-incident-evidence/v6`**, có **`model_evidence` với 3 signal `AVAILABLE`**; `deployment_changes` chứa **đúng 1 receipt** |
| **09:55:37.573** | Lifecycle `RCA_READY` | Revision **2**; `abstained=false`, `abstention_reasons=[]`; **Payment đứng đầu**, score **0,7974433166482036**, coverage **0,8**, margin **0,39658798795773287** |
| **09:55:51.010** | Tạo đề xuất runbook đầu tiên | `decision=oncall`, `reason=oncall-approval-required`; ID/ref đầy đủ bên dưới |
| **09:55:52.928** | `B7_EVIDENCE_PASS` | Đã chứng minh v6, model, receipt, RCA không abstain và đề xuất |
| **09:55:59.088** | Khôi phục image gốc thành công | Generation **5 → 6**, **Ready 1/1**; đây là thao tác khôi phục của lượt chạy, chưa phải rollback tự động |
| **09:58:23** | Owner bật controller `--policy-enabled` | **Policy BẬT** |
| **09:58:36** | Rollout controller hoàn tất | Bật policy không đồng nghĩa đề xuất đã được duyệt hoặc executor đã chạy |

### Định danh và artifact của lượt B7

| Đối tượng | ID / ref đầy đủ |
| --- | --- |
| Incident | `INC-2bf8f369a77128a520a1e98848c9b0c7f631bae2a3033a2205337696b8b44aab` |
| Deployment-change receipt | `artifact://manifests/d9c8836592de0dce8a2d896dc41cefbf8636028e4282cbddbb613b0dccac84fa.json` |
| Capture v6 | `artifact://manifests/a597a3455c3185960210fb408acf88ccb70706217c04c06624d174292c5aa2d6.json` |
| Model release | `092290cedcea07578f664aa670eabf236f66ce7db27ebf159c2c386c3ee5216b` |
| RCA | `artifact://manifests/67ab23b2f27a92e27ac34821bc74ceef685565e443055dd9a0758555e049b1f4.json` |
| Proposal ID | `5b21bc47e39d4a94af9b965b4c3103cfc9e033a220edce2162995764f2b12931` |
| Proposal artifact | `artifact://manifests/cf56840e8ab07932b99ccaa14085855048e4de1c6ca29b8d92c11bc6c6d9ec3b.json` |

### Trạng thái bằng chứng sau B7

| Tiêu chí | Kết luận cập nhật |
| --- | --- |
| Stream mới có bucket commit, consumer bắt kịp; worker không lỗi identity | **ĐÃ CHỨNG MINH tại B4** |
| Capture `continuous-incident-evidence/v6` | **ĐÃ CHỨNG MINH tại B7** |
| `model_evidence` và inference gắn với incident mới | **ĐÃ CHỨNG MINH: 3 signal `AVAILABLE`** |
| Receipt hợp lệ được ghép vào capture/RCA của incident mới | **ĐÃ CHỨNG MINH: `deployment_changes` có đúng 1 receipt** |
| RCA vượt ngưỡng score/coverage/margin, không abstain | **ĐÃ CHỨNG MINH**, `abstention_reasons=[]` |
| Sinh đề xuất runbook | **ĐÃ CHỨNG MINH**, `oncall` / `oncall-approval-required` |
| Policy controller | **ĐANG BẬT**, rollout xong 09:58:36 |
| Owner duyệt đề xuất | **CHƯA DUYỆT**: chưa có mã MFA, chưa bấm Duyệt trên website |
| Executor rollback tự động và xác minh hồi phục | **CHƯA CHẠY / CHƯA CHỨNG MINH** |

Proposal có **TTL 15 phút**, hết hạn khoảng **10:10:52Z ngày 19/11/2026**. Phải **chạy lại B7 để sinh proposal mới** trước lượt duyệt/rollback tiếp theo. Không dùng `B7_EVIDENCE_PASS`, policy đang bật hay việc Payment đã Ready 1/1 để kết luận toàn vòng tự sửa hoàn tất.

## 5. Trạng thái B5–B8 và phần còn lại

Các số bước giữ theo kế hoạch E1. **Di trú và đường bằng chứng B7 đã thành công**; phần chưa hoàn tất là phê duyệt và thực thi/xác minh rollback. Không yêu cầu làm lại di trú epoch.

| Bước | Kết quả / việc còn lại | Giới hạn bằng chứng |
| --- | --- | --- |
| **B5 — Baseline và known-good** | B7 đã mở baseline với image gốc ở generation 4. Trước lượt chạy tiếp theo, đối chiếu known-good-ledger-ref/approved-by, image, UID, root/stream và baseline khỏe hiện tại. | Số đo bàn giao chưa nêu ref hoặc timestamp phê duyệt ledger mới; baseline deployment-change không thay thế approval known-good. Không suy thêm lịch sử phê duyệt từ sự tồn tại proposal. |
| **B6 — Consumer/controller** | Cấu hình epoch đã chạy ở B4; B7 xác nhận capture v6, model release và 3 signal `AVAILABLE`. Owner đã bật policy, rollout hoàn tất 09:58:36. | Khi tiếp tục vẫn phải kiểm checkpoint/catch-up và health hiện tại; không coi snapshot B4 là trạng thái hiện tại. |
| **B7 — Deployment-change và incident mới** | **ĐÃ ĐẠT**: receipt thật, feed 4 file, capture chứa 1 receipt, RCA không abstain, proposal đầu tiên và `B7_EVIDENCE_PASS`. Image gốc đã khôi phục, generation 6, Ready 1/1. | Chạy lại B7 để có proposal mới vì TTL 15 phút. Receipt/timestamp phải thuộc lượt mới, không tái dùng artifact cũ làm bằng chứng cho thay đổi mới. |
| **B8 — Phê duyệt và kiểm chứng tự sửa** | Gate số đo RCA đã đạt, proposal đã tạo, policy đã bật. Còn owner có MFA, duyệt proposal mới còn hạn trên website; sau đó ghi approval receipt, action ID, executor rollback và health hồi phục nếu thực thi thành công. | **Đề xuất chưa duyệt, rollback tự động chưa chạy.** Chưa có bằng chứng executor đi qua đủ gate H4 tại thời điểm dispatch; proposal không tự chứng minh `dispatch_authority` hoặc rollback thành công. |

Receipt cần cùng stream/policy/root/environment/target và observer boot, baseline settled; image đổi đúng generation `+1`, resourceVersion khác, khoảng quan sát lớn hơn 0 và không quá 180 giây. Khoảng change phải nằm trong 600 giây trước onset, kết thúc trước onset và không muộn hơn `bucket_end − 60 giây`, đồng thời khớp image/generation/UID của capture (`src/sentinelops/telemetry/deployment_changes.py:66`; `src/sentinelops/workers/deployment_evidence.py:34`). B7 đã có receipt được nhận vào capture; lượt sau vẫn phải thỏa các điều kiện này.

Rollover bảo tồn dữ liệu cũ nhưng tách lịch sử thành hai stream; không đảm bảo bù telemetry đã quá hạn. Rollback cấu hình không hoàn tác approval, receipt, incident hay generation đã ghi. Không sửa lịch sử hoặc hạ ngưỡng để ép lượt kiểm chứng tiếp theo đạt.

## 6. Coverage đã đúng dự đoán; biên score còn rất mỏng

**Dự đoán coverage 0,40 + model 0,25 + receipt 0,15 = 0,80 đã được B7 xác nhận đúng 0,80.** Model một mình chỉ đưa coverage lên 0,65; receipt hợp lệ là phần bổ sung cần thiết trong cấu hình bằng chứng này. Coverage đủ không tự bảo đảm score đủ.

| Gate RCA | Ngưỡng | B7 đo thực tế | Khoảng vượt ngưỡng |
| --- | ---: | ---: | ---: |
| Coverage | 0,75 | 0,80 | 0,05 |
| Score Payment | 0,75 | 0,7974433166482036 | **0,0474433166482036 ≈ 0,047** |
| Margin giữa các candidate | 0,15 | 0,39658798795773287 | 0,24658798795773287 |

**Biên score RẤT MỎNG:** score khoảng **0,7974** chỉ hơn ngưỡng 0,75 khoảng **0,047**, phụ thuộc điểm **Isolation Forest (IF)**. Nếu IF thấp hơn ở lượt sau và kéo score xuống hơn phần dư này thì RCA sẽ trượt gate dù coverage vẫn 0,80. Margin giữa các candidate khác với khoảng score vượt ngưỡng; margin tốt không loại bỏ rủi ro score sát ngưỡng.

Phép tính trước B7 vẫn hữu ích như một giả định tham chiếu: nếu giữ trace/log của KB3, temporal còn thiếu và change support bằng 1 thì:

```text
coverage = 0,25 (trace) + 0,15 (log) + 0,25 (model) + 0,15 (change) = 0,80
score    = 0,3990235906 + 0,25 × IF + 0,15
score ≥ 0,75  ⇒  IF ≥ 0,8039056376, xấp xỉ 0,803906
```

Đây là điều kiện tính theo **KB3**, không phải giá trị IF đo trong B7. Bộ số đo B7 bàn giao tổng score/coverage/margin, chưa nêu riêng giá trị IF; không suy ngược IF khi chưa có các thành phần score của B7. Với lượt mới phải kiểm lại trace/log/change, IF, source availability và mọi candidate. B7 đã có `abstention_reasons=[]`, nên `unobserved-dependency-candidates` không còn chặn lượt này; điều đó không bảo đảm lượt sau sẽ giống hệt (`src/sentinelops/incidents/rca.py:125`).

**B7 đã chứng minh epoch mới hết abstain và sinh proposal. Phê duyệt, executor rollback và hồi phục sau action vẫn chưa được chứng minh.** Xem thêm bảng đối chiếu bốn kịch bản trong [kết quả demo](DEMO_SCENARIOS_2026-11-19.md).
