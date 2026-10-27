# Ontology Studio — web chạy local

Landing Page và workspace 5 bước, dùng kết quả thật từ `ontology_pipeline/`.
Frontend HTML/CSS/JavaScript thuần; backend Python dùng HTTP server trong thư viện chuẩn.
Không cần Node, npm hoặc build frontend để chạy app.

## Mở web

Tại thư mục gốc repo:

```bash
.venv/bin/python webapp/server.py --port 8787
```

Mở **http://127.0.0.1:8787**. Server chỉ nghe trên máy local.
Nếu cần tạo môi trường mới:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r ontology_pipeline/requirements.txt
.venv/bin/python webapp/server.py --port 8787
```

- Trang chủ: `http://127.0.0.1:8787/`
- Workspace mẫu: `http://127.0.0.1:8787/#workspace/sample`
- Upload và chạy thật: `http://127.0.0.1:8787/#workspace/new`

Xem kết quả mẫu không gọi API. Các lần chạy mới có URL riêng để mở lại.
API key lấy từ `DC5_API_KEY` hoặc `ontology_pipeline/secrets.local.json` phía server.
Model/endpoint mặc định lấy từ `ontology_pipeline/settings.json`; thinking chọn trước khi upload.

## Chạy thử bằng giao diện

1. Chọn **Tạo Ontology**.
2. Upload `examples/schema.sql`; có thể thêm `examples/metadata.json`.
3. Chọn **Trích xuất schema**, xem preview hai bảng.
4. Chọn **Chạy tạo Ontology** để gọi DC5 thật.
5. Theo dõi từng bước/bảng, xem Class, Properties, Methods, Relations, Context/Prompt/Contract.
6. Xem kiểm định, JSON từng Class hoặc toàn database; tải ZIP hoặc báo cáo.

Hỗ trợ SQLite và SQL DDL theo cú pháp SQLite, tối đa 20 MB / 100 bảng ở bản local.
Tên bảng hỗ trợ chữ, số, gạch dưới; bắt đầu bằng chữ hoặc gạch dưới.
SQL upload chỉ được dựng bảng/index trong RAM; INSERT/ATTACH/DDL ngoài phạm vi bị từ chối.
SQLite upload được mở read-only; kiểm tra FK có thể đọc dữ liệu để kiểm tra toàn vẹn.
Request LLM chỉ mang schema/metadata, không mang dữ liệu từng dòng.

## Đọc và sửa code

| File | Trách nhiệm |
|---|---|
| `static/index.html` | Landing Page và khung workspace |
| `static/style.css` | Màu, typography, desktop/tablet/mobile |
| `static/app.js` | Điều hướng, gọi API, tiến trình, viewer và download |
| `server.py` | HTTP API, upload, tạo worker và đọc artifacts |
| `worker.py` | Chạy tuần tự 5 bước, cập nhật tiến độ thật |
| `pipeline_core.py` | Các hàm từ notebook: Methods, context, Relations, validators, DC5 client |
| `storage.py` | Đường dẫn, file trạng thái, lấy key phía server |
| `verify_local.py` | Kiểm chứng API; tùy chọn chạy DC5 thật |

`pipeline_core.py` là bản các hàm của notebook tại thời điểm tách web; không thực thi notebook lúc có request.
Nếu sửa logic notebook về sau, cần cập nhật phần tương ứng trong module này.
Các cell minh họa/regression riêng Omnichannel Retail không đi vào runtime web.

Input/output của mỗi lần chạy nằm dưới `runtime/runs/<run_id>/`, giữ cấu trúc 5 bước.
Thư mục runtime bị Git bỏ qua. Kết quả mẫu gốc không bị ghi đè; không copy key vào từng run.
Frontend chỉ nhận cấu hình công khai; API không phục vụ file secrets hoặc worker log.
Một pipeline DC5 chạy tại một thời điểm. Khi lỗi, thử lại tận dụng responses đã hoàn tất và còn khớp fingerprint.

## Kiểm chứng

Khi server đang chạy:

```bash
.venv/bin/python webapp/verify_local.py
```

Kiểm thử có gọi DC5 thật, tạo thêm run và tiêu thụ API:

```bash
.venv/bin/python webapp/verify_local.py --live-llm
```

Bài live đã chạy thành công: 2 bảng / 7 cột / 1 FK, DC5 thinking, structural PASS và AI review hoàn tất.
Báo cáo kiểm chứng hiện tại lưu ở `runtime/verification.json`.

Đây là bản local để review và dùng thử. Methods mới là đề xuất chỉ đọc, chưa có SQL runtime.
PASS cấu trúc và AI review không chứng minh toàn bộ ý nghĩa nghiệp vụ đúng; trạng thái review nghiệp vụ vẫn chưa duyệt.
