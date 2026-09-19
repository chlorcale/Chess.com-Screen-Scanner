
# Chess Move Reader (Windows)

Ứng dụng Python đọc **vùng danh sách nước đi đang hiển thị trên màn hình** bằng OCR và tích lũy thành plaintext.

Không kết nối vào chess.com, không điều khiển bàn cờ, không dùng chess engine và không đưa ra nước đi/gợi ý.

## 1. Cài Python packages

Mở PowerShell trong thư mục này:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Nếu PowerShell chặn Activate.ps1:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Cài Tesseract OCR

Cài Tesseract OCR cho Windows. Sau khi cài, kiểm tra:

```powershell
tesseract --version
```

Nếu lệnh trên không nhận, có thể thêm đường dẫn Tesseract vào PATH hoặc sửa `main.py`:

```python
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
```

đặt ngay sau phần import `pytesseract`.

## 3. Chạy app

```powershell
python main.py
```

Trên app:

1. Mở chess.com và vào trận.
2. Bấm **Chọn vùng OCR**.
3. Kéo quanh **chỉ vùng Move List**, tránh đồng hồ, tên người chơi và chat.
4. Bấm **Bắt đầu**.
5. Cửa sổ sẽ luôn nằm trên cửa sổ khác.
6. `Copy plaintext` để lấy danh sách nước đi.
7. `Xóa` trước khi bắt đầu một trận mới.

Ví dụ output:

```text
1. e4 e5
2. Nf3 Nc6
3. Bb5 a6
4. Ba4 Nf6
```

## 4. Build thành EXE có icon

Cài PyInstaller:

```powershell
pip install pyinstaller
```

Build:

```powershell
pyinstaller --noconfirm --onefile --windowed --name ChessMoveReader --icon=chess.ico main.py
```

File EXE sẽ nằm ở:

```text
dist\ChessMoveReader.exe
```

Bạn có thể tạo shortcut của EXE ra Desktop và đổi tên thành `Chess Move Reader`.

## 5. OCR không bắt đúng nước đi

Vùng OCR nên:

- đủ rộng để chứa số nước và cả White/Black;
- không chứa đồng hồ, avatar hoặc thành phần UI khác;
- có chữ đủ lớn;
- tránh zoom trình duyệt quá nhỏ.

Nếu chữ quá nhỏ, tăng zoom chess.com lên 110–125% rồi chọn lại vùng.

## 6. Giới hạn hiện tại

App tích lũy những nước đã nhìn thấy. Nó không thể suy ra một nước mà OCR chưa từng nhìn thấy.

Nếu bạn mở app giữa trận thì app chỉ biết phần nước đi đang hiện trên màn hình; để có đầy đủ từ nước đầu tiên, hãy chạy app ngay từ đầu trận hoặc chọn vùng có toàn bộ Move List.

Nếu chuyển sang một trận mới, bấm `Xóa`.
