@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d %~dp0
"C:\Users\4v\AppData\Local\Programs\Python\Python311\python.exe" -X utf8 install_autostart.py
pause
