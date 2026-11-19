# Kịch bản demo AWS dev 833925774773

Kịch bản trình chiếu theo `AWS_DEMO_PLAN.md` ngày 5. Thao tác chi tiết và guard nằm ở
[DEMO_WEB_UI.md](DEMO_WEB_UI.md) (mục 2a, 5–9). Các số "kỳ vọng" đo live ngày 19/11/2026;
cột MTTD/thời gian rollback ghi sau 3 lượt tập dượt.

## Chuẩn bị (không trình chiếu, khoảng 10 phút)

| # | Kiểm | Lệnh / nơi xem | Kỳ vọng |
| --- | --- | --- | --- |
| 1 | Stack chạy | `kubectl --context sentinelops-833-view -n sentinelops-b-actions get deploy` | 12 Deployment READY, gồm `remediation-controller` 2/2 (controller + `operator-ui`) |
| 2 | Traffic thật | Grafana `/grafana/` (đăng nhập Cognito) hoặc Prometheus `sentinelops:http_request_rate:rate1m` | checkout ≈ 2.5 rps, payment ≈ 4.9 rps, inventory ≈ 5.1 rps; lỗi 0%; p95 checkout ≈ 48 ms, payment ≈ 9 ms, inventory ≈ 5 ms |
| 3 | Payment ở known-good | `kubectl get deploy payment-service -o jsonpath='{..image}'` | `application@sha256:e520e702…` |
| 4 | Không còn incident mở | UI `/ui/` | Incident cũ ở `recovered` |
| 5 | Model + policy đã nối | args `remediation-controller` có `--policy-enabled --model-store /models` | Thiếu thì chỉ demo được bước 1–6 bên dưới |
| 6 | Runner sẵn sàng | `kubectl get job/demo-runner`; tạo theo đầu file `scripts/aws_demo/demo-runner.yaml` | Pod Running trên node chứa các PVC |
| 7 | Operator đăng nhập | `https://dr76usfhbe2w8.cloudfront.net/ui/`: mật khẩu + mã TOTP | Danh sách incident hiện ra; `/v1/incidents` không token trả 401 |

## Trình chiếu (một lượt)

| # | Bước | Ai làm | Xem ở đâu | Kỳ vọng |
| --- | --- | --- | --- | --- |
| 1 | Hệ thống khỏe, traffic Checkout → Payment → Inventory | — | Grafana dashboard | SLO breach = 0 cả 3 service |
| 2 | Deploy image Payment lỗi, cùng repo, digest `766dd5f5…` | runner (`run_runbook_demo.py`) | UI + `kubectl rollout status` | Deployment đổi digest; driver ghi change evidence |
| 3 | Phát hiện: luật SLO mở incident | incident worker (always-on, RDS) | UI danh sách incident | Latency payment ≈ +0.95 s; onset → xác nhận ≈ 2.5 phút (đo live 19/11: onset 04:49:00Z, xác nhận 04:51:30Z) |
| 4 | Nguyên nhân: RCA xếp Payment đầu, kèm thay đổi image (H4) | incident worker | UI chi tiết incident | `payment-service` hạng 1; evidence trace/log/timeline có source ref |
| 5 | Giải thích dựa trên bằng chứng, không dùng LLM | worker | UI chi tiết | Mỗi câu có evidence ref |
| 6 | Mức độ + runbook đề xuất | controller | UI proposal | Severity, runbook khớp; trạng thái chờ duyệt (mọi severity đều cần duyệt) |
| 7 | Operator bấm Duyệt | operator (UI) | UI proposal | Receipt duyệt ghi lại người duyệt và lý do |
| 8 | Rollback Payment về known-good | controller (`payment-rollback` SA) | UI action + `kubectl` | Digest về `e520e702…` |
| 9 | Xác minh hồi phục bằng telemetry | controller | UI action | `recovered`, `telemetry_verified` |

Các lượt phụ, mỗi lượt chạy riêng sau khi Payment healthy lại:
- **Từ chối:** `--reject-run`. Operator bấm Từ chối; không có PATCH. Khôi phục image bằng quy trình review.
- **Kill switch:** `--kill-switch-run`. Controller không dispatch khi `KILL_SWITCH` tồn tại.
- **Failed-verify:** `--failed-verify-run --fault-token-file /tmp/fault-token`. Dependency hỏng trong lúc verify, kết quả `escalated`. Cần RBAC `demo-fault` (`scripts/aws_demo/demo-fault-rbac.yaml`).

## Nói rõ khi demo (AWS_DEMO_PLAN §6)

- Incident do luật SLO mở; IF và XGBoost cho điểm làm bằng chứng bên trong incident.
- Số chất lượng là kết quả phát triển (`ald`, G6 local), không phải nghiệm thu.
- Số liệu qua Prometheus trong cluster, chưa qua CloudWatch/X-Ray.
- Giải thích không dùng LLM.
- Receipt của driver mang `demo_mode=true`, `q09_eligible=false`.

## Nếu chưa có model/policy

Dừng sau bước 5 và nói rõ: RCA abstain với `actions_allowed=false`
(`insufficient-coverage`, `insufficient-score`), nên controller không đề xuất rollback. Đây
là đúng thiết kế fail-closed. Incident sẽ tự chuyển `recovered` khi lỗi hết.

## Sau mỗi lượt

Lưu `/tmp/runs/<n>.json` của runner (đã lọc bí mật), kiểm Payment về known-good và healthy,
rồi mới chạy lượt kế. Sau buổi demo: `kubectl delete job/demo-runner configmap/demo-runner-script`.
