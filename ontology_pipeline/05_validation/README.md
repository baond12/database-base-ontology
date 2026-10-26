# Bước 5 — Kiểm định và xuất

Input: inventory, bases, proposals, contexts, relations và contracts được copy từ các bước trước; prompt/contract riêng cho AI reviewer.

Traces sinh Base/Relations được copy vào `input/generation_traces/`, nên QC và tóm tắt không cần đọc trace ngoài bước 5. `input/baseline_evidence/` và `input/first_revision_evidence/` giữ bằng chứng lịch sử để so sánh; chúng không được dùng thay input của pipeline hiện tại.

Output: `ontology.json`, `class_ontologies/`, `validation_report.json`, `semantic_reviews/`, `traces/`, `run_summary.json`.

Kiểm tra các artifacts khớp nguồn, đủ bảng/cột/FK, Method scope, operator, khóa unique, phép tổng hợp, context và cardinality. AI reviewer đối chiếu mô tả với schema/kế hoạch; findings cần được phân xử dựa trên bằng chứng. Đọc thêm `QC_REVIEW.md` để biết kết quả thật và giới hạn.
