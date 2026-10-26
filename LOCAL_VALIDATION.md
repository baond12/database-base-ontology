# Kết quả kiểm chứng local ngày 26/10/2026

## Phạm vi đã chạy

- Database: Omnichannel Retail SQLite v06 đã chốt, mở read-only.
- SHA256: `948e4a1bd0b5bbb4c20e75ccfea6c4e3422c033fa44575ad615d0c3156e36916`.
- LLM thật: Qwen qua API DC5, model lấy từ cấu hình local là
  `qwen/qwen3.6-27b`. Không sử dụng fixture thay cho kết quả API này.
- Chạy đủ Module 1, Module 2, Module 3 với `metadata_path: null`.
- Chạy thêm Module 1 cho bảng `brands` với mô tả bảng/cột được cung cấp trong
  file metadata thử nghiệm. Mô tả nguồn được giữ nguyên trong output.

## Kết quả cuối cùng

| Hạng mục | Kết quả |
|---|---:|
| Bảng nguồn / Base Classes | 20 / 20 |
| Cột nguồn / Properties | 157 / 157 |
| Method đề xuất trong một bảng | 92 |
| FK nguồn / Relations toàn cục | 31 / 31 |
| Context tham chiếu trực tiếp | 20 |
| Ontology hoàn chỉnh từng Class | 20 |
| Findings trong kiểm định cấu trúc | 0 |
| Structural status | PASS |
| Semantic review status | PENDING |
| Runtime readiness | NOT_ASSESSED |

Số Method là kết quả sinh của prompt/model trong lần thử này, không phải số
Method đã được nhóm duyệt hoặc một hằng số bắt buộc cho database khác.

Đã chạy lại toàn pipeline với cache: 20 Base Ontology và 16 nhóm mô tả Relation
được tái sử dụng, không gọi lại API; kiểm định vẫn PASS. SHA256 database sau khi
chạy giữ nguyên. Các kết quả nằm trong thư mục local:
`artifacts/omnichannel_retail/local_dc5_v01/`.

## Điều chỉnh sau khi đọc output thực tế

Lần thử đầu có Method lookup chưa đủ bằng chứng UNIQUE, ví dụ lọc địa chỉ mặc
định theo khách hoặc nhà cung cấp chính theo sản phẩm. Đã bổ sung kiểm tra
chữ ký: lookup trả object phải có tham số bắt buộc bao phủ PK/UNIQUE đầy đủ;
các trường hợp khác dùng list/rows. Prompt được cập nhật và toàn bộ 20 bảng
cùng các Relation phụ thuộc đã được chạy lại.

Đã kiểm tra lại output thực tế: tra cứu snapshot dùng đầy đủ khóa UNIQUE khi
trả object; lọc primary supplier không mặc định mỗi sản phẩm chỉ có một nhà
cung cấp chính. Phần khai báo output của tổng hợp theo kênh có dạng rows và
bao gồm cả cột nhóm. Đây là kiểm tra ví dụ, không phải chứng minh mọi business
meaning do LLM sinh ra đều đúng.

## Kiểm thử code và đóng gói

- `python3 -m unittest discover -s tests -q`: **19/19 PASS**, fixture chỉ dùng
  trong unit tests, không gọi mạng.
- `python3 -m compileall -q src scripts tests`: PASS.
- Build wheel không tải thêm dependency: PASS; wheel có đủ 5 JSON contracts
  và 2 prompts. Dữ liệu, local config và traces không nằm trong wheel.
- Kiểm thử bao gồm metadata thiếu/rỗng/null/có mô tả, metadata sai tên cột,
  cache invalidation, FK ghép, self-FK, nullable, partial UNIQUE, khác biệt chữ
  hoa/thường của FK, scope Method, lookup thiếu UNIQUE, Relation bị sửa sai,
  schema thay đổi, HTTP lỗi, JSON sai, response bị cắt và trace không chứa API key.
- GitHub Actions được cấu hình cho Python 3.10/3.12; chưa chạy trên GitHub.
  Kiểm thử local dùng Python 3.14.4.

## Giới hạn cần giữ khi review

PASS chỉ áp dụng kiểm định cấu trúc và các guard đã triển khai. Mô tả và
assumptions do LLM sinh vẫn cần review; chẳng hạn schema chưa đủ xác nhận đơn
vị tiền tệ hoặc quy tắc nghiệp vụ bên ngoài database. `confidence` do LLM tự
đánh giá, chưa được hiệu chuẩn. Methods chưa được triển khai hoặc chạy SQL.
Chưa có Derived Classes, Intent, Semantic Clustering hay benchmark Text2SQL
trong repo này.

Local configs, metadata thử và artifacts được `.gitignore` bỏ qua. Để chạy lại
trên máy/server khác, copy `configs/pipeline.example.yaml` thành
`configs/pipeline.yaml`, sửa đường dẫn SQLite/DC5 và chạy các bước trong README.
