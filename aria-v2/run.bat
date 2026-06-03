@echo off
REM Launch ARIA v2 from the repo root.
cd /d "%~dp0"
python -m aria2 %*
