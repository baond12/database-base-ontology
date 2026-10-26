# Database Base Ontology

Module sinh **Base Object Ontology** từ schema SQLite và Qwen qua API DC5.
Mỗi bảng có một Class, Property ánh xạ từng cột và Method đề xuất trong phạm vi
một bảng. Relations được xây riêng qua PK/FK. Không sinh Derived Classes.

```text
SQLite + metadata mô tả tùy chọn
  -> trích xuất schema
  -> Module 1: từng bảng -> Class + Property + Method
  -> Module 2: ontology hiện tại + ontology tham chiếu PK/FK một bước
  -> Module 3: FK-grounded Relations
  -> kiểm định + ontology hợp nhất + ontology hoàn chỉnh từng Class
```

## 1. Cài đặt

Python 3.10 trở lên, SQLite 3.37 trở lên. Dùng môi trường Python đang gọi được DC5:

```bash
python3 -m pip install -e .
cp configs/pipeline.example.yaml configs/pipeline.yaml
```

Sửa `configs/pipeline.yaml`:

- `sqlite_path`: đường dẫn SQLite v06 hiện có, hoặc copy vào `data/omnichannel_retail.sqlite3`.
- `llm.config_path`: file YAML bí mật hiện có, chứa profile `dc5` với `api_key`,
  `base_url`, `model`. Model lấy từ chính file này, không hardcode tên model.
- `metadata_path: null` khi chưa có mô tả. Khi có, trỏ đến JSON theo mẫu bên dưới.
- `llm.proxy_mode: company` bật proxy công ty cho cả bốn biến http/https proxy,
  đồng thời đặt `no_proxy`/`NO_PROXY` rỗng theo môi trường công ty.
  `environment` giữ môi trường hiện tại; `none` không dùng proxy môi trường.
- `run_id`: giữ nguyên để chạy tiếp; đổi tên để giữ một bộ kết quả khác.

Đường dẫn dữ liệu tương đối tính từ thư mục repo. API key, database, trace và
artifact sinh ra được bỏ qua trong `.gitignore`. Không commit `llm_config.yaml`.
Không đọc row/sample values: schema và metadata mô tả là input cho LLM.

## 2. Metadata có hoặc không có

```json
{
  "tables": {
    "products": {
      "description": "Danh mục sản phẩm đang được quản lý",
      "columns": {
        "brand_id": {"description": "Mã nhãn hàng của sản phẩm"},
        "description": {"description": ""}
      }
    }
  }
}
```

Chỉ khai báo những bảng/cột đã có mô tả. Bảng/cột không khai báo có description
rỗng; description có giá trị `null` cũng được chuẩn hóa thành chuỗi rỗng.
`metadata_path: null` hoặc file `{}` đều chạy được. Tên bảng/cột sai bị
chặn để tránh đưa metadata cũ vào schema mới. Cột dữ liệu tên `description`
không mặc nhiên là mô tả schema. Mô tả nguồn được giữ trong
`metadata_description`, tách với diễn giải `description` do LLM đề xuất.

Khi cập nhật metadata rồi chạy lại, module chỉ tái sinh Base Ontology bị thay đổi
và phần Relations phụ thuộc. File đã sinh có fingerprint khớp sẽ được dùng lại.

## 3. Chạy từng bước

```bash
python3 scripts/extract_schema.py --config configs/pipeline.yaml
python3 scripts/build_base_ontologies.py --config configs/pipeline.yaml
python3 scripts/build_reference_contexts.py --config configs/pipeline.yaml
python3 scripts/build_relations.py --config configs/pipeline.yaml
python3 scripts/validate_ontology.py --config configs/pipeline.yaml
```

Để review prompt với một bảng trước:

```bash
python3 scripts/build_base_ontologies.py --config configs/pipeline.yaml --tables products
```

Sau đó chạy lại không có `--tables` để hoàn thành mọi bảng trước Module 2.
Module 2 lấy cả tham chiếu đến và đi từ Class, giữ self-FK nhưng không đệ quy.
Một FK chỉ có một ID toàn cục; Module 3 mô tả FK ở bảng sở hữu khóa ngoại,
rồi gắn Relation vào cả hai Class. Không chuyển Method sang Class tham chiếu.

Khi từng bước đã ổn, chạy toàn luồng:

```bash
python3 scripts/run_pipeline.py --config configs/pipeline.yaml
```

`--force` trên script gọi LLM bỏ qua cache. HTTP timeout hoặc JSON không hợp lệ
sẽ dừng ở bước lỗi; response/prompt và lỗi được lưu để debug. JSON sai contract
được yêu cầu sửa tối đa hai lần gọi theo cấu hình; không tạo kết quả giả thay LLM.

## 4. Input/output và kiểm định

| Module | Input | Output |
|---|---|---|
| 1 | Schema một bảng, metadata của bảng/cột đó | Class, đủ Property, Methods một bảng |
| 2 | Toàn bộ Base Ontology đã có và PK/FK | Context trực tiếp của từng Class |
| 3 | Context và FK sở hữu bởi bảng hiện tại | Relations có source/target Property, cardinality và mô tả |

Code giữ ID và thông tin vật lý ổn định; LLM không quyết định bảng/cột/FK mới.
ID có dạng `class:orders`, `property:orders.order_id`, `method:orders.countOrders`.
Method ID phụ thuộc tên Method được đề xuất, nên có thể đổi khi sinh lại prompt;
không xem Method này là contract đóng băng. Class/Property ID không phụ thuộc tên
nghiệp vụ do LLM đặt.

Cardinality xét FK ghép, nullable và UNIQUE/PK đầy đủ. Partial/expression UNIQUE
không được coi là ràng buộc unique toàn bảng. Có FK không có nghĩa mọi parent
đều có child. Trong SQLite, TEXT PRIMARY KEY trên bảng rowid có thể nullable;
reader giữ hành vi thực tế thay vì mặc định mọi PK đều NOT NULL.

```text
artifacts/<database_id>/<run_id>/
  schema_inventory.json
  base_ontologies/<table>.json
  reference_contexts/<table>.json
  relation_annotations/<table>.json
  class_ontologies/<table>.json
  relations.json
  ontology.json
  validation_report.json
  run_manifest.json
  run_status.json
  llm_traces/base/<table>.json
  llm_traces/relations/<table>.json
```

`run_status.json` cho biết bước gần nhất đang chạy, hoàn thành hay lỗi. Nếu một
lần chạy lại thất bại, kết quả từ lần trước có thể vẫn còn để phục vụ debug;
không coi manifest/report cũ là kết quả của lần đang lỗi. `last_error.json` lưu
lỗi gần nhất trong lịch sử, còn trạng thái hiện tại nằm ở `run_status.json`.

`validation_report.json` phân biệt:

- `structural_status: PASS`: đủ bảng/cột/FK, tham chiếu và scope đúng.
- `semantic_review_status: PENDING`: mô tả nghiệp vụ cần review.
- `runtime_readiness: NOT_ASSESSED`: chưa tạo/chạy SQL thực thi Methods.

Method luôn là `PROPOSED_READ_ONLY`; confidence là tự đánh giá của LLM,
không phải xác suất được hiệu chuẩn. Validator phát hiện tham chiếu cột ngoài
bảng và schema sai. `lookup` trả một object phải có tham số bắt buộc bao phủ
PK/UNIQUE đầy đủ; nếu không, phải đề xuất dạng list/rows. Quy tắc này kiểm tra
tham chiếu chữ ký, không chứng minh thuật toán Method sẽ lọc đúng khi triển khai.
Validator không chứng minh toàn bộ lời mô tả/ý nghĩa Method là đúng.
Các business rule không có trong schema/metadata phải được review, không suy ra
từ tên FK. Database chưa có FK hợp lệ phải được sửa trước khi chạy.

Với schema rất rộng, một bảng vẫn có thể vượt giới hạn context của API; phiên bản
này chưa chia một bảng thành các chunk hoặc cắt bớt cột. Hãy review output trước
khi mở rộng; module không giới hạn số bảng bằng con số 20.

## 5. Kiểm thử local

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src scripts tests
```

Tests dùng LLM fixture riêng, không gọi mạng, không có fallback fixture trong
pipeline thật. Kiểm tra metadata rỗng/có, cache invalidation, FK ghép/self,
nullable, partial unique, hallucinated columns và Relation bị sửa sai.

## 6. Đưa lên GitHub bằng tay

Tạo repo GitHub rỗng `baond12/database-base-ontology`, rồi trong thư mục này:

```bash
git init -b main
git add .
git status --short
git commit -m "Add modular database base ontology builder"
git remote add origin https://github.com/baond12/database-base-ontology.git
git push -u origin main
```

Nếu repo GitHub đã có commit, clone repo đó trước và copy source vào checkout;
không force-push. Không đưa output local vào báo cáo như kết quả DC5 nếu chưa
thực sự chạy API. Kết quả thử nghiệm thực tế nằm trong `LOCAL_VALIDATION.md`.
