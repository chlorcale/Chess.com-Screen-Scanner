@echo off
setlocal
".venv\Scripts\python.exe" -m pip install pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --windowed --name ChessMoveReader --icon=chess.ico main.py
echo.
echo EXE: dist\ChessMoveReader.exe
pause
