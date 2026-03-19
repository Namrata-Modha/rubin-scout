@echo off
title Rubin Scout - Shutting Down
echo.
echo  Stopping Rubin Scout services...
echo.

:: Kill backend and frontend processes
taskkill /fi "WINDOWTITLE eq Rubin Scout Backend*" /f 2>nul
taskkill /fi "WINDOWTITLE eq Rubin Scout Frontend*" /f 2>nul

:: Stop Docker containers
docker compose down 2>nul

echo.
echo  All services stopped.
echo.
pause
