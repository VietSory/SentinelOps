# RCA development baseline

Giữ kết quả này để so sánh thay đổi thuật toán/evidence trên cùng nguồn và mẫu số,
không dùng nó để xác nhận service hiện tại đang chạy.

## Nguồn và phạm vi

- Reference implementation: `b27a8c260f1408962dcdba8770df59da0eb8b5a7`.
- RCAEval revision: `afeacb11bcc94dadfd1c8f483ee4377b2b8b614e`, ba development cases
  đã được phép; final holdout chưa mở, không dùng cho ML fitting.
- [Kết quả máy gốc](evidence/rca-development.json), mục `canonical_ADR013_verification`,
  pin source manifest, profile/policy hashes, cửa sổ và kết quả từng case. Các trường
  legacy trong cùng JSON giữ nguyên provenance, không trộn với canonical result.

## Kết quả canonical đã đo

| Evidence | Evaluated | Descriptive top-1 | Descriptive top-3 | MRR | Abstained / qualified | Coverage theo case |
| --- | --- | ---: | ---: | ---: | --- | --- |
| Native traces + logs | 3 | 0 | 1/3 | 1/6 | 3 / 0 | 0 / 0.40 / 0.40 |
| Bỏ log support | 3 | 0 | 1/3 | 1/6 | 3 / 0 | 0 / 0.25 / 0.25 |
| Bỏ trace support | 3 | 0 | 0 | 0 | 3 / 0 | 0 / 0.15 / 0.15 |

Ranking bao gồm hypothesis bị abstain, **không phải qualified RCA accuracy**.
Coverage tối đa 0.40 chưa đạt floor 0.75; thiếu source receipt/SLO/model/deployment
support. Không suy ra causal/online/MTTD hoặc generalization từ ba case này.
Chưa có independent-session confidence interval và chưa accepted full G4.

## Dùng lại

Theo [native runbook](../runbooks/RCAEVAL_NATIVE.md#reproduce-and-recover) và
[development protocol](../runbooks/RCAEVAL_NATIVE_DEVELOPMENT.md), giữ source/profile/
policy và báo cùng mẫu số. Chỉ cập nhật baseline khi có evaluation mới đáng so sánh;
không nối nhật ký test, số lần PASS hoặc lần sửa nhỏ vào đây.
