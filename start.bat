@echo off
cd /d "%~dp0"
call venv\Scripts\activate
set PATH=C:\Windows\System32;C:\Windows;%PATH%
wsl -d Ubuntu --user root -- bash -lc "service postgresql start >/dev/null 2>&1 || true"
python refresh_db_url.py
python app.py
