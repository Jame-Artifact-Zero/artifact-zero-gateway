@echo off
REM ============================================
REM Artifact Zero — Manual Rollback
REM ============================================
REM Reverts ECS Fargate to the previous task definition.
REM Run from any machine with AWS CLI configured.
REM
REM Usage:
REM   rollback.bat              (rolls back to previous version)
REM   rollback.bat 42           (rolls back to specific revision number)
REM ============================================

set CLUSTER=artifact-zero-cluster
set SERVICE=artifact-zero-service
set FAMILY=artifact-zero-task
set REGION=us-east-1

echo.
echo ========================================
echo  ARTIFACT ZERO — ROLLBACK
echo ========================================
echo.

if "%1"=="" (
    echo Finding previous task definition...
    for /f "tokens=*" %%a in ('aws ecs list-task-definitions --family-prefix %FAMILY% --sort DESC --region %REGION% --query "taskDefinitionArns[1]" --output text') do set PREV_ARN=%%a
) else (
    set PREV_ARN=arn:aws:ecs:%REGION%:567282577590:task-definition/%FAMILY%:%1
    echo Using specified revision: %1
)

if "%PREV_ARN%"=="None" (
    echo ERROR: No previous task definition found.
    exit /b 1
)

echo.
echo Rolling back to: %PREV_ARN%
echo.

aws ecs update-service --cluster %CLUSTER% --service %SERVICE% --task-definition "%PREV_ARN%" --force-new-deployment --region %REGION% >nul 2>&1

if %ERRORLEVEL% EQU 0 (
    echo ROLLBACK INITIATED
    echo.
    echo Fargate will swap to the previous version in ~60 seconds.
    echo Health check: https://dontgofulltilt.com/health
    echo.
    echo To verify:
    echo   aws ecs describe-services --cluster %CLUSTER% --services %SERVICE% --region %REGION% --query "services[0].{status:status,running:runningCount,desired:desiredCount,taskDef:taskDefinition}"
) else (
    echo ROLLBACK FAILED — check AWS credentials and try again.
)
