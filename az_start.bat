@echo off
powershell.exe -NoExit -Command "cd C:\code\artifact-zero-gateway; $env:DATABASE_URL='postgresql://artifactzero:usRrhmpjyYK3ZHClmteyWk6k@artifact-zero-db.c6xkgmi4um8q.us-east-1.rds.amazonaws.com:5432/artifactzero'; Write-Host 'AZ Ready' -ForegroundColor Green"
