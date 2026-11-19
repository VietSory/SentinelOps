# Di trú health stream epoch — 19/11/2026

**Đã tạo và vận hành health stream epoch mới; capture v6, `model_evidence` và vòng tự sửa vẫn CHƯA CHỨNG MINH.** Mốc bằng chứng cuối của tài liệu là báo cáo B4 lúc **08:46:07 UTC ngày 19/11/2026** (15:46:07 giờ Việt Nam). Consumer mới có **0 job, 0 capture** vì checkout, payment và inventory đều khỏe. Phải có sự cố mới và capture tương ứng mới kiểm chứng được đường bằng chứng mới.

Tài liệu tổng hợp các báo cáo nội bộ E1 (phân tích/kế hoạch epoch), B1 (snapshot), B2 (probe recorder), B3 (render), B4 (chuyển stream) và R1 (review). Đây là kết quả được bàn giao, không phải một lượt chạy EKS mới của người soạn. Source được đối chiếu tại branch `feat/grafana-incident-dashboard`, commit `e2f740bb8267497725ed42ede40f034dc642870d` (`e2f740b`), PR **#81**. Môi trường: AWS profile `console`, EKS `sentinelops-dev`, context `sentinelops-demo`, namespace `sentinelops-b-actions`.

## 1. Vì sao phải di trú

**Cả ba kịch bản sự cố đã chạy thật, đều đến `ESCALATED` và không có proposal (đề xuất khắc phục).** Đây là kết quả xử lý sự cố được bàn giao; không có nghĩa ba sự cố vẫn đang hoạt động. Snapshot B1 sau đó ghi nhận 5 incident trong DB, tất cả đã `recovered`/`CLOSED`. Chưa có bằng chứng vòng tự sửa đã thực hiện rollback.

RCA abstain: bằng chứng hiện có chỉ đạt **coverage 0,40**, trong khi policy yêu cầu **coverage ≥ 0,75**, **score ≥ 0,75** và **margin ≥ 0,15**. Số liệu Payment trong KB3 giải thích cụ thể điểm thiếu:

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
- Thêm receipt deployment-change hợp lệ: coverage có thể lên **0,65 + 0,15 = 0,80**. Đây là phép tính khi các bằng chứng đều được chấp nhận, **CHƯA CHỨNG MINH** trên một incident thuộc epoch mới.

Model IF cung cấp giá trị `anomaly_strength`; risk từ XGBoost bổ sung fact, không có trọng số RCA riêng (`src/sentinelops/workers/model_signals.py:248`, `:250`). Model đã nạp và readiness báo `dependencies.model=true` không chứng minh incident-worker đã sinh capture có model hoặc RCA đủ điều kiện. R1 đã chỉ ra tài liệu cũ ghi KB2/KB3 chưa chạy, model chưa ready là sai trạng thái; tài liệu này dùng kết quả đã chạy được bàn giao và giữ riêng giới hạn bằng chứng của epoch mới.

## 2. Vì sao không thể chỉ thêm model vào incident-worker

Consumer đang tồn tại được định danh từ `consumer_version` và `health_stream_id` (`src/sentinelops/workers/incident_journal.py:41`). Identity của consumer còn chứa `evidence_profile` (`:45`); thêm `ModelIncidentEvidence` đưa `model_release_ref` vào profile (`src/sentinelops/workers/model_signals.py:91`). Cùng stream sẽ giữ cùng consumer ID nhưng làm identity khác cấu hình đã được chấp nhận.

`initialize()` giữ bản ghi cũ bằng `ON CONFLICT DO NOTHING` rồi so identity (`src/sentinelops/workers/incident_journal.py:66`, `:70`). Khi chỉ thêm model trên stream cũ, nó sẽ ném lỗi tại **`src/sentinelops/workers/incident_journal.py:71`**:

```text
ValueError: consumer configuration changed; explicit migration is required
```

CLI chỉ hiện `incident worker failed: ValueError` (`scripts/run_incident_worker.py:200`). Probe `--status` có thể báo trước lỗi `consumer checkpoint is missing, rewound or configured differently` (`src/sentinelops/workers/incident_journal.py:84`). Đây là bảo vệ identity/lịch sử, không phải lỗi có thể giải quyết bằng restart.

Phương án là dùng **health artifact root mới**, để HealthWorker tạo stream identity mới, sau đó tạo consumer mới với evidence profile có model. Health stream ID được sinh từ identity, trong đó có root; đổi metrics-instance hoặc restart đơn thuần không tạo epoch. Health CLI không có cờ `--epoch`; helper render chỉ sửa manifest, stream thật được tạo khi health-worker chạy. ID phải lấy từ log `READY` và xác nhận có bucket `COMMITTED`.

Không xóa/copy checkpoint, sửa identity hay làm SQL migration để ép qua gate. Stream mới không kế thừa episode/window/lịch sử của stream cũ; DB, PVC và artifact cũ được giữ lại. Đường capture dự kiến là Kubernetes collector → `DeploymentIncidentEvidence` → `ModelIncidentEvidence` (`scripts/run_incident_worker.py:73`, `:83`, `:86`). Wrapper model có code đổi v5 thành v6 và thêm `model_evidence` (`src/sentinelops/workers/model_signals.py:116`); code này chưa thay thế được bằng chứng capture chạy thật.

## 3. Đã làm đến đâu: B1–B4

### B1 — Snapshot và đường khôi phục đã chuẩn bị

Báo cáo B1 xác nhận đủ **9/9 mục**: desired/live args, env, probes, image digest, Deployment/ConfigMap, stream/consumer, checkpoint/ledger, PVC/PV/VolumeAttachment/node và trạng thái incident. **7/7 PVC Bound**; 6 PVC worker/model cùng node. Đối chiếu cuối **08:27 UTC** xác nhận spec, UID, digest, ConfigMap, PVC và ref khớp; 5 revision incident cùng checkpoint B1 còn nguyên trong khi worker tiếp tục chạy.

Đã viết **`ROLLBACK.md` trong bộ snapshot nội bộ**, có thứ tự dừng/khôi phục/khởi động, payload toàn spec kèm **UID guard**, đối chiếu UID từng tài nguyên trong phạm vi khôi phục, trả lại image/ref cũ và kiểm tiến độ worker. **Chưa thực thi rollback.** Snapshot được chụp nhiều thời điểm khi hệ thống đang chạy, không phải backup vật lý RDS/EBS; khôi phục cấu hình không tua lùi checkpoint hoặc lịch sử đã ghi.

### B2 — Đã tìm được đường recorder hợp lệ sẵn có

E1 ban đầu nêu khoảng trống của review CLI: nhánh `--in-cluster-worker` từ chối `open-change`/`complete-change`, còn nhánh mặc định chỉ cho `local`. B2 xác định đã có đường hợp lệ qua **`Backend.deploy()` trong pod demo-runner**, nên **KHÔNG phải sửa guard nào**.

`Backend.deploy()` gọi recorder trực tiếp: mở baseline → kiểm resourceVersion → dry-run PATCH → PATCH thật → poll complete mỗi 2 giây, timeout 150 giây (`scripts/run_runbook_demo.py:284`). Pod `demo-runner-sh259` đang có mount RW vào PVC `deployment-change-artifacts`; incident-worker đọc cùng PVC qua `/artifacts/deployment-changes` ở chế độ RO. Receipt do tiến trình recorder ghi, Kubernetes không tự tạo receipt này.

Bản script trong ConfigMap mà runner đang dùng đã có kiểm image khác digest/cùng repository; B2 không đề nghị chỉnh guard mới. B2 chỉ kiểm code/cấu hình và GET live, **chưa chạy recorder hoặc PATCH**. Driver đầy đủ còn chờ proposal/approval/rollback, mặc định có nhiều vòng; không có mode CLI receipt-only riêng. Việc tìm thấy đường gọi hợp lệ **không chứng minh receipt đã được sinh và ghép vào incident**.

### B3 — Render và server dry-run PASS 3/3

Lúc **08:31 UTC**, ba manifest Deployment cho health-worker, incident-worker và remediation-controller đã render, **server dry-run PASS 3/3**, stderr rỗng. B3 chưa apply; root mới vẫn chưa tồn tại tại bước kiểm này.

- Health: root mới trong args và probes; giữ profile `prometheus-increase-60s-5s/v2`.
- Incident: source root mới trong args/probes; thêm model release và PVC `model-de38fc6b` mount `/models` RO; giữ incident store `/artifacts/incidents`.
- Controller: source root/stream mới; giữ model release, sidecar operator-ui và cấu hình liên quan.
- Giữ UID, PVC/mount cũ, digest và các cấu hình bảo vệ; bổ sung `PGSSLROOTCERT` cho health/incident. Policy vẫn tắt. Stream còn là placeholder để B4 thay bằng ID từ `READY`.

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

Identity cũ, revision incident và checkpoint B1 còn nguyên; PVC Bound/UID/PV, image ba service, ledger ref/approver được giữ. Policy vẫn **false**; B4 không tiêm lỗi, không đổi image Payment, không sửa ledger và không kích hoạt rollback. **B4 đã chuyển epoch nhưng chưa đạt trọn tiêu chí capture v6/model.**

## 4. Trạng thái hiện tại và giới hạn bằng chứng

Tại mốc B4, DB của consumer mới có **0 job**; quét artifact mới thấy **24 manifest, 0 capture**. Checkout/payment/inventory đều `ELIGIBLE` và `healthy`, `incident_active=false`, `pending_openings=[]`; log có `created_jobs=[]`.

`enrich_one()` chọn incident job chưa có evidence và trả về khi không có job (`src/sentinelops/workers/incident_journal.py:327`, `:331`, `:334`). Vì vậy chưa có capture hiện được giải thích bởi chưa phát sinh sự cố, không phải lỗi identity.

| Tiêu chí | Kết luận tại mốc B4 |
| --- | --- |
| Stream mới có bucket commit, consumer bắt kịp | Đã quan sát |
| Worker khởi động với root/model mới, không lỗi identity | Đã quan sát |
| Capture `continuous-incident-evidence/v6` | **CHƯA CHỨNG MINH** |
| `model_evidence` xuất hiện trong capture mới | **CHƯA CHỨNG MINH** |
| Inference `AVAILABLE` gắn với incident mới | **CHƯA CHỨNG MINH** |
| Receipt hợp lệ được ghép vào RCA của incident mới | **CHƯA CHỨNG MINH** |
| RCA vượt các gate, sinh proposal và hoàn tất vòng tự sửa | **CHƯA CHỨNG MINH** |

Phải chờ có sự cố mới và kiểm capture thực tế mới biết các tiêu chí còn lại. Không suy từ worker Ready, model đã nạp hay website 200 rằng vòng tự sửa đã chạy hoặc sắp chạy được.

## 5. Phần còn lại B5–B8 và rủi ro

Các số bước giữ theo kế hoạch E1. **B4 đã thực hiện phần cấu hình/khởi động worker của B6**; bảng dưới nêu phần còn phải xác minh, không yêu cầu lặp lại rollout đã xong. Báo cáo được cung cấp chưa chứng minh B5–B8 hoàn tất.

| Bước | Công việc và điều kiện cần kiểm | Rủi ro và giới hạn |
| --- | --- | --- |
| **B5 — Baseline khỏe và known-good mới** | Giữ healthy traffic; tích ít nhất 5 phút baseline ngoài cửa sổ model 5 phút, tương đương khoảng 10 phút dữ liệu mới. Thu image review đúng root/stream mới, đủ 3 bucket eligible liên tiếp; operator duyệt ledger mới trước khi mở baseline deployment-change. **Ledger mới: CHƯA CHỨNG MINH.** | **Phê duyệt ghi lịch sử bất biến.** Duyệt sai image, UID, stream, root hoặc approver làm sai căn cứ rollback; quay lại cấu hình cũ không xóa được quyết định đã ghi. Phải kiểm bằng chứng/review cụ thể trước khi duyệt; không sửa lịch sử để làm lại. |
| **B6 — Hoàn thiện consumer/controller** | Root/stream, model mount/release và thứ tự incident trước controller đã triển khai ở B4. Còn kiểm known-good-ledger-ref/approved-by mới từ B5, checkpoint/catch-up tại thời điểm tiếp tục, registry và inference `AVAILABLE` thực tế. Giữ policy tắt. **B6 đầy đủ: CHƯA CHỨNG MINH.** | Lệch source/stream/ledger hoặc khởi động controller trước consumer có thể chặn readiness/authority; rollout có thể gián đoạn xử lý/UI. Registry Ready không chứng minh inference trên cửa sổ mới. B4 vẫn giữ ledger/approver cũ, không được coi đó là review mới. |
| **B7 — Tạo deployment-change và sự cố mới** | Sau phê duyệt kế hoạch cụ thể, dùng recorder mở baseline khi Payment đang ở image khỏe; **deploy image lỗi/regression vào Payment thật**; complete-change trong ≤180 giây, trước onset. Chờ bucket hoàn toàn sau change; kiểm `CHANGE_OBSERVED`, feed và capture. **Receipt và capture của lượt này: CHƯA CHỨNG MINH.** | **Ảnh hưởng Payment và đơn đang xử lý; generation cùng lịch sử đã ghi không đảo ngược.** Sai timing, UID, resourceVersion, generation hoặc image khiến receipt không hợp lệ. Chuẩn bị image khỏe/đường khôi phục trước; nếu bước chạy lỗi thì khôi phục theo phương án đã chuẩn bị, không tạo timestamp/receipt giả hay hồi dựng KB3. |
| **B8 — Kiểm gate rồi mới kiểm chứng tự sửa** | Kiểm incident mới có v6, model `AVAILABLE`, change evidence hợp lệ; RCA score ≥0,75, coverage ≥0,75, margin ≥0,15, không abstain và đạt các gate H4 về evidence/revision/health/catch-up. Chỉ khi mọi gate đạt mới lên lịch bật policy, duyệt proposal mới và kiểm rollback thật cùng health phục hồi. **Toàn vòng: CHƯA CHỨNG MINH.** | Bật policy có thể dẫn tới PATCH Deployment thật và tiêu thụ approval một lần. Nếu thiếu bất kỳ gate nào thì dừng điều tra IF/trace/log/onset hoặc candidate còn thiếu; không hạ ngưỡng hoặc ép authority. Không cam kết có proposal hay rollback thành công. |

Receipt cần cùng stream/policy/root/environment/target và observer boot, baseline settled; image đổi đúng generation `+1`, resourceVersion khác, khoảng quan sát lớn hơn 0 và không quá 180 giây. Khoảng change phải nằm trong 600 giây trước onset, kết thúc trước onset và không muộn hơn `bucket_end − 60 giây`, đồng thời khớp image/generation/UID của capture (`src/sentinelops/telemetry/deployment_changes.py:66`; `src/sentinelops/workers/deployment_evidence.py:34`). Có receipt trên đĩa chưa đủ để consumer chấp nhận nó.

Rollover bảo tồn dữ liệu cũ nhưng tách lịch sử thành hai stream; không đảm bảo bù telemetry đã quá hạn. Xóa PVC/artifact, DELETE/TRUNCATE checkpoint/history hoặc sửa identity để vượt gate không thuộc kế hoạch. Rollback cấu hình không hoàn tác approval, receipt, incident hay generation đã ghi.

## 6. ĐIỀU CHƯA BIẾT

**Ngay cả khi hoàn tất việc nối model, ledger và receipt, score vẫn chưa chắc đạt 0,75.** Với giả định giữ nguyên trace/log của KB3, temporal vẫn thiếu và receipt được chấp nhận với change support bằng 1:

```text
coverage = 0,25 (trace) + 0,15 (log) + 0,25 (model) + 0,15 (change) = 0,80
score    = 0,3990235906 + 0,25 × IF + 0,15
score ≥ 0,75  ⇒  IF ≥ 0,8039056376, xấp xỉ 0,803906
```

Do đó cần **IF ≥ 0,803906** theo phép tính trên; mức IF thực tế trên incident mới **CHƯA CHỨNG MINH**. Đây là điều kiện theo số liệu KB3, không phải dự báo cho sự cố mới: trace/log/change có thể khác và phải tính lại. Coverage 0,80 chỉ cho biết đủ trọng số có bằng chứng, không bảo đảm các giá trị bằng chứng đủ mạnh để đạt score.

Còn phải hết sạch **`unobserved-dependency-candidates`**. KB3 có Checkout `score=null`; chỉ một candidate còn `score=null` cũng khiến toàn RCA abstain (`src/sentinelops/incidents/rca.py:125`). Margin, source availability và các gate H4 vẫn phải đạt; nâng score của riêng Payment không giải quyết được mọi điều kiện này.

**Chưa có bằng chứng rằng epoch mới sẽ hết abstain, sinh proposal hoặc thực hiện tự sửa thành công.** Cần incident mới và bằng chứng thực tế để kết luận; tài liệu không đưa ra cam kết thời điểm đạt các điều kiện đó.
