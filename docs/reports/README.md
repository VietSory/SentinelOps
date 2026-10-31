# Reports và evidence

Đây là kết quả theo ngày, SHA, nguồn và môi trường; chúng giữ scope gốc.
[PROJECT_STATUS](../PROJECT_STATUS.md) là bản tóm tắt hiện hành,
[delivery plan](../delivery/DELIVERY_PLAN.md) là việc cần làm tiếp.

| Cần tra | Report |
| --- | --- |
| Source HTTP v2/admission | [2026-10-31 prospective admission](2026-10-31-prospective-http-admission.md) |
| Data/model blockers | [G2/G3 resolution](G2_G3_BLOCKER_RESOLUTION.md), [model software](PHASE2_DATASET_AI_MODELS.md) |
| Continuous incident local | [2026-10-29 integration](2026-10-29-continuous-incident-local.md), [worker metrics](2026-10-30-incident-worker-metrics.md) |
| Native RCA scope/results | [Phase 3 evaluation](PHASE3_RCA_AI_INTEGRATION.md), [G4 findings](G4_RCA_EVIDENCE_RESOLUTION.md) |
| Các thử nghiệm cũ | File có ngày/chủ đề trong thư mục này; source guides cũ ở [archive](../archive/README.md) |

`evidence/` và các JSON cùng report giữ nguyên bytes/paths. Một số JSON là input
của tests/CLIs/CI, nên không di chuyển/xóa như văn bản thông thường. Dữ liệu raw,
model binary và secrets không thuộc thư mục này.

Hai lane tạo/cập nhật `ai-data-delivery.md` và `platform-delivery.md` khi bắt đầu
implementation. Mỗi report giữ kết quả sử dụng được, version, kiểm tra/giới hạn và
dependency; không sinh một file mới cho từng lần chạy test.
