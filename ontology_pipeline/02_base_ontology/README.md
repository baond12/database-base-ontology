# Bước 2 — Base Ontology

Input: `schema_inventory.json` copy từ bước 1, `prompt.txt`, `response_contract.json`.

`review_feedback.json` chứa hướng dẫn sửa riêng từng bảng đã đối chiếu schema; nội dung được nối vào prompt chỉ cho bảng đó. Thay feedback rồi chạy lại từ bước 2; cache các bảng không bị ảnh hưởng vẫn được giữ.

Output: `proposals/<bảng>.json`, `base_ontologies/<bảng>.json`, `traces/<bảng>.json`.

LLM đặt tên/mô tả và đề xuất Methods. Code tạo IDs, giữ kiểu/nullable/PK/default/metadata, gắn Method owner/scope/status. Method có filter operator, group_by và aggregation cụ thể; chỉ tham chiếu cột trong bảng hiện tại.

`response_contract.json` là JSON Schema cho câu trả lời LLM. Ngoài contract, code kiểm tra lookup equality theo khóa unique đầy đủ, đúng output shape, đúng cột tổng hợp và không trùng kế hoạch. Không sinh SQL thực thi.
