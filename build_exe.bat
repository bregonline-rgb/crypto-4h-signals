:: Save this as build_exe.bat in the same folder as crypto_4h_signal_app.py
:: Requires: Python installed on Windows and pip

python -m pip install --upgrade pip
pip install pyinstaller ccxt pandas numpy rich

:: Create a one-file executable with PyInstaller
pyinstaller --noconfirm --onefile --console --name crypto_4h_signal_app crypto_4h_signal_app.py

echo Build finished. The EXE will be in the 'dist' folder.
pause
