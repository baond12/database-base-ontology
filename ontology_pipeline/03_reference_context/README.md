# Bước 3 — Context tham chiếu

Input: inventory và `base_ontologies/` copy từ các bước trước.

Output: `reference_contexts/<bảng>.json`.

Không gọi LLM. Ghép Class/Properties hiện tại với Class/Properties liên kết FK một bước, cả incoming/outgoing/self. Không đệ quy. Không mang Methods vào context Relations vì không cần cho mô tả FK và dễ gây nhầm owner.
