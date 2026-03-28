@echo off
set ANTHROPIC_API_KEY=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
cd /d "%~dp0"
start http://localhost:5000
python "%~dp0app.py"
