# Kiểm chứng local và tối ưu pipeline

Đã chạy API thật DC5 với model **qwen/qwen3.6-27b**, `enable_thinking=true`, kết nối trực tiếp.
Bộ cuối hoàn tất lúc **15:13:25 26/10/2026 (Asia/Ho_Chi_Minh)**.
Không dùng fixture để thay kết quả API. Fixture SQL chỉ dùng trong regression checks.

## Kết quả bộ cuối

| Kiểm tra | Kết quả |
|---|---:|
| Bảng / Classes | 20 / 20 |
| Cột / Properties | 157 / 157 |
| Methods đề xuất một bảng | 82 |
| FK / Relations toàn cục | 31 / 31 |
| Contexts một bước | 20 |
| Ontology từng Class | 20 |
| Structural findings | 0 |
| Structural status | PASS |
| Regression checks | 38/38 PASS |
| Review ngữ nghĩa từng bảng | 20/20 hoàn tất |
| AI findings chưa xử lý ở input cuối | 0 |
| Copy độc lập chỉ notebook + folder | PASS |

Nguồn SQLite local có SHA256 `948e4a1bd0b5bbb4c20e75ccfea6c4e3422c033fa44575ad615d0c3156e36916`.
Database mở read-only; hash giữ nguyên. Schema-only SQL đóng gói tái tạo đúng toàn bộ schema đã đọc từ database thật.
Metadata nguồn hiện là `{}`; các diễn giải nghiệp vụ vẫn mang trạng thái PROPOSED.

Các traces đang được giữ trong bộ có **58 HTTP attempts** và
**58 attempts có reasoning_content hoặc reasoning_tokens**.
Tổng usage được giữ là 352,803 tokens. Đây là số của các attempts trong traces hiện tại,
bao gồm lần sửa JSON và các response được cache; không phải số request mới khi chạy lại từ cache,
cũng không phải tổng billing của mọi lần thử lịch sử. Xem `run_summary.json` để đối chiếu từng bảng.

## Những lỗi thực tế đã phát hiện

1. **Proxy công ty timeout.** Kết nối trực tiếp tới `/models` và API hoạt động. Bộ dùng `proxy_url` rỗng;
   người chạy trên mạng khác có thể sửa cấu hình này.
2. **Pipeline gốc dừng vì JSON sai.** Baseline từ commit `c9fb019` đã sinh 20 Classes, 157 Properties,
   102 Methods và 5 nhóm Relations, rồi dừng ở `inventory_snapshots`: cả hai attempts sai dấu đóng JSON,
   dù finish_reason=stop. Không ghi baseline này là full-pipeline PASS.
3. **Method chưa diễn tả đầy đủ logic.** Ví dụ `listProductsInStock` nhận boolean bất kỳ nhưng description
   luôn nói sản phẩm đang có hàng. Các aggregate cũ chỉ có danh sách cột, chưa khai báo function/group_by rõ.
4. **Suy diễn quá schema.** Có mô tả ID tự tăng khi không AUTOINCREMENT; NULL thành root/chưa giao;
   JSONB thành danh sách JSON hợp lệ; agent_id thành người xử lý đơn; store/team nullable bị nói như luôn có.
5. **Mâu thuẫn giữa Class/Property/Relation.** Property của addresses khẳng định khách hàng sở hữu địa chỉ
   trong khi Relation ghi nhận vai trò sở hữu chưa được xác nhận. Self-FK của categories từng dùng từ “khác”,
   dễ hiểu là không được tham chiếu chính bản ghi, trong khi schema không chặn trường hợp này.
6. **Reviewer cũng có thể báo sai.** Review đầu từng phản đối region_code xác định duy nhất bản ghi chỉ vì
   nó không phải PK. Finding này bị bác: `region_code TEXT NOT NULL UNIQUE` vẫn là khóa định danh duy nhất hợp lệ.
   Prompt reviewer đã được bổ sung để không nhầm UNIQUE với PK.

## Prompt và contract được tối ưu thế nào

- Methods có `operator`, `group_by`, `output.aggregations` với function/cột cụ thể.
  `source_columns` phải đúng bằng hợp các cột được dùng, không chỉ là tập bao phủ.
- `lookup` chỉ trả object khi input required + equality bao phủ một PK/UNIQUE toàn bảng.
  Partial/expression UNIQUE không được dùng để suy ra unique toàn bảng.
- `list`, `count`, aggregate có/không group đều có output shape được kiểm tra.
  SUM/AVG chỉ trên cột khai báo kiểu số; không filter trực tiếp JSON/BLOB.
- Prompt phân biệt DEFAULT với CHECK; nullable với nguyên nhân thiếu dữ liệu; JSONB/BLOB với cấu trúc/nội dung đã xác nhận.
- Mô tả FK trung lập, đúng chiều, dùng tên cột vật lý nhất quán. Không tự gán creator/owner/attribution.
- Feedback riêng cho addresses/categories nằm ở `02_base_ontology/input/review_feedback.json`.
  Feedback đã đối chiếu DDL, không bổ sung business facts ngoài nguồn. Chỉ bảng bị ảnh hưởng và phần phụ thuộc được sinh lại.
- Guard local chặn câu tự tăng thiếu AUTOINCREMENT và cách diễn đạt self-FK loại bỏ tự tham chiếu.

## Vì sao có JSON Schema strict mà vẫn cần validator?

Thử `json_object` cho thấy JSON có thể hợp cú pháp nhưng thêm field ngoài contract.
Thử strict với contract Relations tổng quát cho thấy JSON có thể đúng contract nhưng chỉ trả 1/2 FK.
Vì vậy bộ cuối chuyên biệt contract Relations theo đúng số FK và enum IDs, rồi vẫn kiểm tra coverage/IDs bằng code.

Backend DC5/xgrammar trả HTTP 400 với keyword `uniqueItems`. Hàm `generation_contract()` chỉ bỏ keyword này
khỏi bản schema gửi API; contract local vẫn giữ đầy đủ và phát hiện trùng lặp. Không bỏ kiểm tra để làm PASS.

## Thay đổi code để dễ đọc

Toàn bộ logic ở notebook, dạng hàm; **0 class tự viết**, **1 try/except** tại biên LLM để yêu cầu sửa JSON một lần.
Không có framework điều phối hoặc client class riêng. Cấu hình chung chỉ ở `settings.json` và hai biến notebook
`FORCE`, `RUN_AI_REVIEW`. Từng bước copy input rõ ràng rồi chỉ xử lý input/output của bước đó.

Code kiểm tra fingerprint/schema/context trước khi dùng; tự tạo IDs và thông tin vật lý;
LLM không quyết định bảng/cột/FK mới. Context Relations không mang Methods, giúp giảm input không cần thiết.
Trace lưu request không có Authorization, response, thinking, usage và lỗi sửa JSON.
Export được đọc lại để đối chiếu. Manifest giữ checksum và phiên bản thư viện để truy vết.

## Phạm vi QA

38 regression checks bao gồm composite/self/repeated FK, tên FK khác hoa/thường, nullable PK của SQLite,
STRICT/WITHOUT ROWID, generated columns, partial/expression UNIQUE, lookup thiếu key/range,
Method ngoài bảng, sai type, sai group/output/SUM, Property/cardinality/context bị sửa và thiếu Relation.
Có ca INSERT self-reference vào database fixture để chứng minh self-FK cho phép trỏ chính bản ghi.
Có kiểm tra việc chuyển schema API không sửa contract local và local vẫn phát hiện uniqueItems vi phạm.

Bài kiểm tra portable đã copy đúng notebook + folder sang thư mục riêng, không có src/config/scripts cũ,
không có secrets.local.json, không có SQLite chứa rows, và chặn toàn bộ HTTP. Chạy mọi code cell thành công
từ schema.sql và responses DC5 đã lưu; schema fingerprint và counts khớp, regression PASS.

## Đọc bằng chứng

- [Báo cáo cấu trúc/ngữ nghĩa](output/validation_report.json)
- [Báo cáo regression](output/regression_report.json)
- [Báo cáo chạy độc lập](output/portable_execution_report.json)
- [Tổng hợp calls/thinking/usage](output/run_summary.json)
- [Baseline thất bại](input/baseline_evidence/summary.json)
- [Trace JSON lỗi của baseline](input/baseline_evidence/inventory_snapshots_failed_trace.json)
- [Bản đầu của notebook trước tối ưu ngữ nghĩa](input/first_revision_evidence/validation_report.json)
- [Bản strict trước hai feedback cuối](input/strict_revision_evidence/validation_report.json)
- [HTTP 400 uniqueItems](input/strict_schema_http400_evidence.json)
- [JSON mode probe](input/baseline_json_mode_probe.json), [strict schema probe](input/baseline_strict_json_probe.json)

## Giới hạn cần giữ khi sử dụng

PASS xác nhận những điều đã kiểm tra, không phải chứng minh tuyệt đối mọi business meaning đúng.
AI reviewer dùng cùng model nên có thể mắc lỗi tương quan; ví dụ finding UNIQUE/PK đã cho thấy cần phân xử.
Confidence do LLM tự đánh giá, chưa hiệu chuẩn. Vai trò nghiệp vụ, đơn vị tiền, JSON structure, timezone và quy tắc
ngoài schema/metadata vẫn cần chủ dữ liệu xác nhận. Methods chưa triển khai/chạy SQL:
`runtime_readiness=NOT_IMPLEMENTED`, `human_review_status=PENDING`.

Bộ input/một số cell minh họa và regression được chuẩn bị cho Omnichannel Retail này. Khi thay database khác,
cần sửa các cell minh họa products và kiểm thử riêng categories/products tương ứng.
