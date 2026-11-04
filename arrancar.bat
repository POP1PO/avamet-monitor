@echo off
chcp 65001 >nul
title AVAMET Monitor (con reinicio automatico)
cd /d "%~dp0"
:inicio
echo.
echo === %date% %time% : arrancando AVAMET Monitor ===
python run.py
echo.
echo === %date% %time% : la aplicacion se ha cerrado (codigo %errorlevel%). Reinicio en 15 s. Cierra esta ventana para parar. ===
echo %date% %time% cierre codigo %errorlevel% >> reinicios_automaticos.log
timeout /t 15 /nobreak >nul
goto inicio
