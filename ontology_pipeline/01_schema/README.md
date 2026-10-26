# Bước 1 — Trích xuất schema

Input nằm trong `input/`:

- `database.local.sqlite3`: SQLite thật, tùy chọn, không commit.
- `schema.sql`: schema-only để tái lập khi không có SQLite thật.
- `metadata.json`: mô tả nguồn, `{}` nếu chưa có.

Output nằm trong `output/`:

- `schema_inventory.json`: DDL, cột, PK/FK, indexes/UNIQUE, nullable, default, generated, metadata.
- `extraction_report.json`: số bảng/cột/FK và fingerprint nguồn.

Code nằm trong phần **Bước 1** của notebook. Không gọi LLM. Đọc SQL của indexes để giữ bằng chứng partial/expression UNIQUE. Không đọc rows đưa vào LLM.
