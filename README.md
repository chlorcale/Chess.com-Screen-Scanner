
# Chess Move Reader — fixed selection + OCR test

Bản này sửa lỗi quan trọng của bản trước: app không còn hiện lại trước khi
chụp screenshot chọn vùng. Vì app đang Always-on-top, việc hiện app trong
lúc chụp có thể khiến chính cửa sổ app che danh sách nước đi.

## Chạy

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## Quy trình

1. Đặt chess.com và move list nhìn rõ.
2. Bấm `Chọn vùng OCR`.
3. App sẽ biến mất.
4. Kéo quanh đúng khu vực move list.
5. Thả chuột.
6. App xuất hiện lại.
7. Bấm `Test OCR`.
8. Nếu Test OCR nhìn thấy chữ/nước đi thì bấm `Bắt đầu`.

## Rất quan trọng

Đừng để cửa sổ Chess Move Reader che lên chính vùng OCR khi đang đọc.
Bạn có thể kéo app sang góc khác màn hình.

Ví dụ:

```text
CHESS.COM
+--------------------------------------------+
|                                            |
|              bàn cờ         MOVE LIST     |
|                             1. e4 e5       |
|                             2. Nf3 Nc6     |
|                             3. Bb5 a6      |
|                                            |
+--------------------------------------------+

                  [Chess Move Reader]
```

`Always on top` có nghĩa app luôn nổi phía trên; nó không thể đọc được chữ
nằm phía sau chính cửa sổ của nó nếu hai vùng chồng lên nhau.

## Test OCR

`Test OCR` hiển thị raw plaintext mà Tesseract nhận được từ vùng đang chọn.
Đây là bước debug quan trọng:

- Nếu raw text có `e4`, `e5`, ... nhưng danh sách Moves chưa cập nhật:
  vấn đề nằm ở bộ parser SAN.
- Nếu raw text trống/rác:
  vấn đề nằm ở vùng chọn hoặc preprocessing OCR.
