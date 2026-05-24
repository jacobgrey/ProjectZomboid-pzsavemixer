@echo off
setlocal
set "HERE=%~dp0"
python "%HERE%pzmix\main.py" %*
endlocal
