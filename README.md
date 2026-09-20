# Chess Move Reader - DOM Edition

Ứng dụng Tkinter đọc danh sách nước đi Chess.com trực tiếp từ DOM thay vì OCR, sau đó dựng vị trí bằng `python-chess` và phân tích nước đi tốt nhất tiếp theo bằng Stockfish.

## Cấu trúc

```text
Chess.com screen reader/
├── main.py
├── requirements.txt
├── README.md
├── run.bat
├── bridge/
│   └── chess_dom_reader.js
├── stockfish/
│   └── stockfish.exe
└── chess_reader/
    ├── __init__.py
    ├── app.py
    ├── config.py
    ├── core/
    │   ├── __init__.py
    │   ├── models.py
    │   ├── dom_parser.py
    │   └── chess_logic.py
    ├── services/
    │   ├── __init__.py
    │   ├── dom_bridge.py
    │   └── stockfish.py
    ├── controllers/
    │   ├── __init__.py
    │   ├── dom_controller.py
    │   └── analysis_controller.py
    └── ui/
        ├── __init__.py
        ├── main_window.py
        └── settings_panel.py
```

## Cài đặt

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Hoặc chạy `run.bat`.

## Cách dùng

1. Mở ván cờ Chess.com trong Chrome/Edge.
2. Chạy ứng dụng bằng `python main.py`.
3. Bấm **Khởi động DOM Bridge**.
4. Trong DevTools của tab Chess.com, mở **Console**.
5. Mở file `bridge/chess_dom_reader.js`, copy toàn bộ và paste vào Console.
6. Enter. Console sẽ bắt đầu theo dõi `<wc-simple-move-list>` bằng `MutationObserver`.
7. Quay lại app và chọn:
   - **Trắng**: chỉ cập nhật phân tích cho nước Trắng.
   - **Đen**: chỉ cập nhật phân tích cho nước Đen.
   - **Cả hai**: cập nhật sau mọi thay đổi hợp lệ.
8. App sẽ hiển thị nước đi tốt nhất tiếp theo, evaluation, depth và danh sách nước hiện tại.

## Bridge

Server local chạy trên `127.0.0.1:8765`.

Endpoint:

- `GET /health`
- `POST /moves`
- `OPTIONS /moves`

JS trong `bridge/chess_dom_reader.js` tự gửi dữ liệu khi DOM thay đổi và dùng `copy()` để có thể lấy plaintext bằng tay nếu cần.

## Lưu ý

Bridge DOM đọc dữ liệu từ giao diện trang, không dùng OCR. Nếu Chess.com thay đổi HTML component hoặc đóng Shadow DOM, selector trong `bridge/chess_dom_reader.js` có thể cần chỉnh lại.

`stockfish/stockfish.exe` trong package này là file Windows đã được cung cấp cùng project. Trên máy Windows, app tự tìm executable này; không cần thêm Stockfish vào PATH.
