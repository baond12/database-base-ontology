# Bước 4 — Relations

Input: inventory, `reference_contexts/`, `prompt.txt`, `response_contract.json`.

Output: `relations.json`, `annotations/<bảng>.json`, `traces/<bảng>.json`.

Code xác định endpoints, Properties và cardinality từ FK/nullable/UNIQUE. LLM chỉ đặt tên, mô tả và assumptions cho đúng các relation IDs được giao. Mỗi FK được sinh ở bảng sở hữu một lần. Nullable FK không có nghĩa luôn có parent; tồn tại FK không có nghĩa mỗi parent đều có child.
