# tests/demo.ps1
param([int]$Workers = 2)

# ensure venv active before running this file in your shell
# .\.venv\Scripts\Activate.ps1

# Reset basic config
python -m queuectl config-set max_retries 2
python -m queuectl config-set backoff_base 2
python -m queuectl config-set shutdown false

Write-Host "`n== Start workers ==" -ForegroundColor Cyan
Start-Process -WindowStyle Minimized powershell -ArgumentList "python -m queuectl worker-start --count $Workers"
Start-Sleep -Seconds 2

Write-Host "`n== Basic success ==" -ForegroundColor Cyan
python -m queuectl enqueue --id ok1 --command "cmd /c echo hello"
Start-Sleep -Seconds 1
python -m queuectl list --state completed

Write-Host "`n== Parallel (no overlap) ==" -ForegroundColor Cyan
python -m queuectl enqueue --id p1 --command "timeout /T 10 /NOBREAK >NUL"
python -m queuectl enqueue --id p2 --command "timeout /T 10 /NOBREAK >NUL"
python -m queuectl enqueue --id p3 --command "timeout /T 10 /NOBREAK >NUL"
python -m queuectl list --state processing
Start-Sleep -Seconds 3
python -m queuectl status

Write-Host "`n== Retries + DLQ ==" -ForegroundColor Cyan
python -m queuectl enqueue --id bad1 --command "idontexist_123"
Start-Sleep -Seconds 6
python -m queuectl dlq-list
python -m queuectl dlq-retry bad1

Write-Host "`n== Graceful stop ==" -ForegroundColor Cyan
python -m queuectl enqueue --id long1 --command "timeout /T 6 /NOBREAK >NUL"
Start-Sleep -Seconds 1
python -m queuectl worker-stop
