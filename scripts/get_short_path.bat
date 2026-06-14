@echo off
for %%I in ("%~dp0.") do set SHORT=%%~sI
echo %SHORT% > "%~dp0\short_path.txt"