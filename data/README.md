# Database đầu vào

Copy SQLite v06 đã chốt vào `data/omnichannel_retail.sqlite3`, hoặc sửa
`sqlite_path` trong cấu hình để trỏ tới file hiện có. Database được mở read-only.
Không đưa database hoặc API key lên GitHub mặc định.

Metadata mô tả là file JSON riêng theo `configs/metadata.example.json`.
`metadata_path: null`, file `{}`, hoặc description `""` đều là trường hợp hợp lệ.
Khi có mô tả, cập nhật file này rồi chạy lại cùng run_id; cache bị ảnh hưởng sẽ
được làm mới. Không dùng dữ liệu của cột `products.description` như mô tả schema.
