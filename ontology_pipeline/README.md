# Bộ pipeline độc lập

Chỉ cần giữ hai mục cạnh nhau:

```text
ontology_pipeline.ipynb
ontology_pipeline/
  settings.json
  secrets.example.json
  requirements.txt
  01_schema/
    input/
    output/
  02_base_ontology/
    input/
    output/
  03_reference_context/
    input/
    output/
  04_relations/
    input/
    output/
  05_validation/
    input/
    output/
```

Không cần `src/`, `scripts/`, `configs/`, `data/`, `artifacts/` hoặc notebook cũ của repo. Toàn bộ logic chạy nằm trong các hàm của notebook; không có class tự viết. Đầu mỗi bước, notebook copy các output cần thiết của bước trước vào input bước hiện tại. Hàm xử lý của bước chỉ đọc các file đã chuẩn bị trong thư mục bước đó và cấu hình chung của bộ.

## Chạy trên máy/server khác

Python >= 3.10, SQLite >= 3.37. Từ thư mục chứa notebook:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r ontology_pipeline/requirements.txt
```

Mở notebook trong VS Code, chọn kernel `.venv/bin/python`, chạy từng cell từ trên xuống. Nếu dùng JupyterLab thì cài thêm `jupyterlab` trong môi trường đó.

Để gọi API khi không có cache, cung cấp key bằng một trong hai cách:

- Đặt biến môi trường `DC5_API_KEY` trước khi khởi động kernel.
- Copy `ontology_pipeline/secrets.example.json` thành `ontology_pipeline/secrets.local.json`, điền key. File local này được `.gitignore` bỏ qua.

Endpoint, model, thinking, timeout và proxy nằm trong `settings.json`. Mặc định kết nối trực tiếp, `enable_thinking=true`. Chỉ điền `proxy_url` nếu máy cần proxy; proxy công ty đã timeout trong lần kiểm chứng local này. Không dùng file `llm_config.yaml` bên ngoài bộ.

## Input database và tính độc lập

`01_schema/input/schema.sql` chứa CREATE TABLE và CREATE INDEX, không có rows. Nếu chưa có SQLite local, notebook dựng schema trong RAM từ file này. Bộ schema được kiểm tra khớp chính xác schema SQLite local đã dùng khi chạy API.

Muốn dùng database thật, đặt file tại `01_schema/input/database.local.sqlite3`. File này không đưa lên Git. SQLite thật được mở read-only và kiểm tra FK; database rỗng dựng từ SQL không thể chứng minh tính toàn vẹn của rows trên một database khác.

Bộ input và các cell minh họa/regression hiện được kiểm chứng cho Omnichannel Retail 20 bảng. Nếu thay database khác, sửa các cell minh họa `products` và các ca kiểm tra riêng `products/categories` cho phù hợp.

`01_schema/input/metadata.json` mặc định `{}`. Thêm mô tả bảng/cột rồi chạy lại từ bước 1. Không dùng dữ liệu trong cột tên `description` làm mô tả schema.

## Đọc code và tự sửa

1. Đọc markdown của từng bước để biết input/output.
2. Đọc các hàm ngay dưới markdown; cell “Chạy bước N” mới gọi xử lý.
3. Sửa prompt hoặc Response Contract trong `input/` của bước 2/4; chạy lại từ bước đó xuống.
4. Cache dựa vào fingerprint của context, prompt, contract và cấu hình sinh. Cùng input thì dùng lại response; đổi prompt/thinking/model thì sinh lại phần liên quan. `FORCE=True` bắt gọi lại.
5. Mở proposal, ontology và trace song song để hiểu phần do LLM đề xuất và phần code giữ cố định.

`02_base_ontology/input/review_feedback.json` lưu hướng dẫn sửa riêng cho các bảng đã được QA đối chiếu schema. Feedback được nối vào prompt của đúng bảng, không thêm tham số chạy. Đây là hướng dẫn review, không phải metadata nghiệp vụ mới.

LLM Response Contract là JSON Schema bắt buộc cho response, không phải schema database và không phải SQL runtime contract. Ngoài kiểm tra JSON Schema, code kiểm tra đủ cột, Method scope, operator, khóa unique, group_by và aggregation. Chỉ có một `try/except` ở biên gọi LLM để yêu cầu sửa JSON một lần; lỗi cuối cùng được báo thẳng.

Client gửi JSON Schema strict cho DC5. Backend xgrammar hiện không hỗ trợ `uniqueItems`, nên bản schema gửi API bỏ riêng keyword này; validator local vẫn dùng contract đầy đủ. Với Relations, schema gửi API có đúng số FK và enum relation IDs của request hiện tại; code vẫn kiểm tra coverage và ID trùng. Không có fallback ngầm sang response giả hoặc model khác.

## Kết quả và giới hạn

Xem `05_validation/output/validation_report.json`, `ontology.json`, `class_ontologies/products.json` và `run_summary.json`. Chi tiết kiểm chứng, vấn đề phát hiện và thay đổi nằm trong `05_validation/QC_REVIEW.md`.

AI review dùng cùng model DC5, vì vậy có thể có lỗi tương quan với bước sinh. PASS cấu trúc hoặc review không có findings không chứng minh tuyệt đối mọi business meaning đúng. Methods mới là kế hoạch đọc một bảng, chưa triển khai/chạy SQL; human review vẫn cần trước production.

Traces lưu request không có Authorization, response content, reasoning_content và usage. Không có API key trong notebook hoặc các output được đóng gói. `secrets.local.json` và database có rows được loại khỏi Git.
