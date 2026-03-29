@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%"

if defined LF_ENV_FILE (
  set "ENV_FILE=%LF_ENV_FILE%"
) else (
  set "ENV_FILE=%SCRIPT_DIR%.env"
)

if defined LF_DOCKER_PRUNE_STATE_FILE (
  set "DOCKER_PRUNE_STATE_FILE=%LF_DOCKER_PRUNE_STATE_FILE%"
) else (
  set "DOCKER_PRUNE_STATE_FILE=%SCRIPT_DIR%.git\vision-forge-docker-prune.last-run"
)

if exist "%ENV_FILE%" call :load_env "%ENV_FILE%"

if "%~1"=="" goto :usage

set "PROFILE_INPUT="
set "ACTION="
set "EXTRA_ARGS="

call :is_known_action "%~1"
if "!IS_KNOWN_ACTION!"=="1" (
  set "ACTION=%~1"
  shift
) else (
  if "%~2"=="" goto :usage
  set "PROFILE_INPUT=%~1"
  set "ACTION=%~2"
  shift
  shift
)

:collect_extra_args
if "%~1"=="" goto :after_collect_extra_args
if defined EXTRA_ARGS (
  set "EXTRA_ARGS=!EXTRA_ARGS! %1"
) else (
  set "EXTRA_ARGS=%1"
)
shift
goto :collect_extra_args

:after_collect_extra_args
call :normalize_profile "%PROFILE_INPUT%" PROFILE
if errorlevel 1 goto :end

if defined PROFILE_INPUT if defined LF_RUNTIME_PROFILE (
  call :normalize_profile "%LF_RUNTIME_PROFILE%" ENV_PROFILE
  if /I not "!ENV_PROFILE!"=="!PROFILE!" (
    echo [WARN] CLI profile '!PROFILE!' overrides LF_RUNTIME_PROFILE='!ENV_PROFILE!' from %ENV_FILE%.
    echo [WARN] Prefer copying the matching .env.*.example to .env before startup.
  )
)

if defined LF_PROJECT_NAME (
  set "PROJECT_NAME=%LF_PROJECT_NAME%"
) else (
  set "PROJECT_NAME=vision-forge-!PROFILE!"
)

set "COMPOSE_BASE_FILE=infra\compose.base.yaml"
set "COMPOSE_PROFILE_FILE=infra\compose.!PROFILE!.yaml"

if not exist "%COMPOSE_BASE_FILE%" (
  echo ERROR: Compose file not found: %COMPOSE_BASE_FILE%
  goto :end
)

if not exist "%COMPOSE_PROFILE_FILE%" (
  echo ERROR: Compose file not found: %COMPOSE_PROFILE_FILE%
  goto :end
)

docker compose version >nul 2>&1
if not errorlevel 1 (
  set "COMPOSE_VARIANT=plugin"
) else (
  docker-compose version >nul 2>&1
  if not errorlevel 1 (
    set "COMPOSE_VARIANT=legacy"
  ) else (
    echo ERROR: Neither 'docker compose' nor 'docker-compose' is available.
    goto :end
  )
)

if /I "%ACTION%"=="up" goto :up
if /I "%ACTION%"=="up-build" goto :up_build
if /I "%ACTION%"=="up_build" goto :up_build
if /I "%ACTION%"=="restart" goto :restart_action
if /I "%ACTION%"=="restart-build" goto :restart_build_action
if /I "%ACTION%"=="restart_build" goto :restart_build_action
if /I "%ACTION%"=="down" goto :down
if /I "%ACTION%"=="destroy" goto :destroy
if /I "%ACTION%"=="migrate" goto :migrate
if /I "%ACTION%"=="logs" goto :logs
if /I "%ACTION%"=="reset-db" goto :reset_db
if /I "%ACTION%"=="reset_db" goto :reset_db
if /I "%ACTION%"=="config" goto :config

echo ERROR: Unknown action: %ACTION%
goto :usage

:up
call :maybe_auto_prune_before_start
call :should_run_migrations_on_start
if "!RUN_MIGRATIONS_ON_START!"=="1" (
  call :start_database_service
  if errorlevel 1 goto :end
  call :run_migrations
  if errorlevel 1 goto :end
  echo [INFO] Starting API for '!PROFILE!' profile...
  call :start_api_service 0
) else (
  echo [INFO] Starting '!PROFILE!' profile ^(no rebuild^)...
  call :compose_profile up -d %EXTRA_ARGS%
)
goto :end

:up_build
call :get_api_image_id PREVIOUS_API_IMAGE_ID
call :maybe_auto_prune_before_start
call :should_run_migrations_on_start
if "!RUN_MIGRATIONS_ON_START!"=="1" (
  call :build_api_image
  if errorlevel 1 goto :end
  call :start_database_service
  if errorlevel 1 goto :end
  call :run_migrations
  if errorlevel 1 goto :end
  echo [INFO] Starting API for '!PROFILE!' profile...
  call :start_api_service 1
) else (
  echo [INFO] Starting '!PROFILE!' profile with rebuild...
  call :compose_profile up -d --build %EXTRA_ARGS%
)
if errorlevel 1 goto :end
call :remove_previous_api_image "!PREVIOUS_API_IMAGE_ID!"
call :prune_build_artifacts
goto :end

:restart_action
call :should_run_migrations_on_start
call :start_database_service
if errorlevel 1 goto :end
if "!RUN_MIGRATIONS_ON_START!"=="1" (
  call :run_migrations
  if errorlevel 1 goto :end
)
echo [INFO] Recreating API for '!PROFILE!' profile...
call :start_api_service 1
goto :end

:restart_build_action
call :get_api_image_id PREVIOUS_API_IMAGE_ID
call :maybe_auto_prune_before_start
call :should_run_migrations_on_start
if "!RUN_MIGRATIONS_ON_START!"=="1" (
  call :build_api_image
  if errorlevel 1 goto :end
  call :start_database_service
  if errorlevel 1 goto :end
  call :run_migrations
  if errorlevel 1 goto :end
  echo [INFO] Recreating API for '!PROFILE!' profile...
  call :start_api_service 1
) else (
  echo [INFO] Rebuilding and restarting '!PROFILE!' profile...
  call :compose_profile up -d --build %EXTRA_ARGS%
)
if errorlevel 1 goto :end
call :remove_previous_api_image "!PREVIOUS_API_IMAGE_ID!"
call :prune_build_artifacts
goto :end

:down
echo [INFO] Stopping '!PROFILE!' profile...
call :compose_profile down
goto :end

:destroy
echo [WARN] Destroying '!PROFILE!' profile ^(containers + volumes^)...
echo        This will remove DB volumes and data.
set /p CONFIRM=Are you sure? (type 'yes' to continue): 
if /I not "%CONFIRM%"=="yes" (
  echo [INFO] Aborted.
  goto :end
)
call :compose_profile down -v
goto :end

:migrate
call :run_migrations
goto :end

:logs
if not exist logs mkdir logs
call :get_timestamp TIMESTAMP
set "LOG_FILE=logs\vision-forge-!PROFILE!-!TIMESTAMP!.log"
echo [INFO] Writing a current log snapshot to: !LOG_FILE!
call :compose_profile logs api %EXTRA_ARGS% > "!LOG_FILE!"
echo [INFO] Streaming API logs for '!PROFILE!' profile...
echo [INFO] Press Ctrl+C to stop.
call :compose_profile logs -f api %EXTRA_ARGS%
goto :end

:reset_db
call :get_api_image_id PREVIOUS_API_IMAGE_ID
echo [WARN] Resetting DB for '!PROFILE!' profile ^(containers + volumes^)...
echo        This will ERASE ALL DATA in the database.
set /p CONFIRM=Type 'reset' to continue: 
if /I not "%CONFIRM%"=="reset" (
  echo [INFO] Aborted.
  goto :end
)
call :compose_profile down -v
call :maybe_auto_prune_before_start
call :should_run_migrations_on_start
if "!RUN_MIGRATIONS_ON_START!"=="1" (
  call :build_api_image
  if errorlevel 1 goto :end
  call :start_database_service
  if errorlevel 1 goto :end
  call :run_migrations
  if errorlevel 1 goto :end
  echo [INFO] Starting API for '!PROFILE!' profile...
  call :start_api_service 1
) else (
  call :compose_profile up -d --build %EXTRA_ARGS%
)
if errorlevel 1 goto :end
call :remove_previous_api_image "!PREVIOUS_API_IMAGE_ID!"
call :prune_build_artifacts
goto :end

:config
call :compose_profile config %EXTRA_ARGS%
goto :end

:is_known_action
set "IS_KNOWN_ACTION=0"
for %%A in (
  up
  up-build
  up_build
  restart
  restart-build
  restart_build
  down
  destroy
  migrate
  logs
  reset-db
  reset_db
  config
) do (
  if /I "%~1"=="%%~A" set "IS_KNOWN_ACTION=1"
)
exit /b 0

:normalize_profile
set "REQUESTED=%~1"
if not defined REQUESTED (
  if defined LF_RUNTIME_PROFILE (
    set "REQUESTED=%LF_RUNTIME_PROFILE%"
  ) else (
    set "REQUESTED=gpu"
  )
)

if /I "%REQUESTED%"=="dev" set "REQUESTED=gpu"
if /I "%REQUESTED%"=="stg" set "REQUESTED=cloud"
if /I "%REQUESTED%"=="prod" set "REQUESTED=cloud"

if /I "%REQUESTED%"=="cpu" (
  set "%~2=cpu"
  exit /b 0
)
if /I "%REQUESTED%"=="gpu" (
  set "%~2=gpu"
  exit /b 0
)
if /I "%REQUESTED%"=="cloud" (
  set "%~2=cloud"
  exit /b 0
)

echo ERROR: Unknown profile: %REQUESTED%
echo        Use one of: cpu ^| gpu ^| cloud
exit /b 1

:compose_raw
if /I "%COMPOSE_VARIANT%"=="plugin" (
  docker compose %*
) else (
  docker-compose %*
)
exit /b %ERRORLEVEL%

:compose_profile
if exist "%ENV_FILE%" (
  call :compose_raw --env-file "%ENV_FILE%" -p "%PROJECT_NAME%" -f "%COMPOSE_BASE_FILE%" -f "%COMPOSE_PROFILE_FILE%" %*
) else (
  call :compose_raw -p "%PROJECT_NAME%" -f "%COMPOSE_BASE_FILE%" -f "%COMPOSE_PROFILE_FILE%" %*
)
exit /b %ERRORLEVEL%

:get_api_image_id
set "%~1="
set "__LF_CONTAINER_ID="
for /f "usebackq delims=" %%I in (`call :compose_profile ps -q api 2^>nul`) do (
  if not defined __LF_CONTAINER_ID set "__LF_CONTAINER_ID=%%I"
)
if not defined __LF_CONTAINER_ID exit /b 0
for /f "usebackq delims=" %%I in (`docker inspect -f "{{.Image}}" "!__LF_CONTAINER_ID!" 2^>nul`) do (
  set "%~1=%%I"
  goto :get_api_image_id_done
)
:get_api_image_id_done
exit /b 0

:remove_previous_api_image
set "OLD_API_IMAGE_ID=%~1"
if not defined OLD_API_IMAGE_ID exit /b 0
call :get_api_image_id NEW_API_IMAGE_ID
if not defined NEW_API_IMAGE_ID exit /b 0
if /I "%OLD_API_IMAGE_ID%"=="%NEW_API_IMAGE_ID%" exit /b 0
echo [INFO] Removing previous API image: %OLD_API_IMAGE_ID%
docker image rm -f "%OLD_API_IMAGE_ID%" >nul 2>&1
if errorlevel 1 echo [WARN] Failed to remove previous API image. It may still be in use.
exit /b 0

:run_migrations_if_enabled
call :should_run_migrations_on_start
if /I not "!RUN_MIGRATIONS_ON_START!"=="1" exit /b 0
call :run_migrations
exit /b %ERRORLEVEL%

:run_migrations
echo [INFO] Running Alembic migrations for profile '!PROFILE!'...
call :compose_profile run --rm api alembic upgrade head
exit /b %ERRORLEVEL%

:should_run_migrations_on_start
if defined LF_RUN_MIGRATIONS_ON_START (
  set "RUN_MIGRATIONS_ON_START=%LF_RUN_MIGRATIONS_ON_START%"
) else (
  set "RUN_MIGRATIONS_ON_START=1"
)
exit /b 0

:build_api_image
echo [INFO] Building API image for '!PROFILE!' profile...
call :compose_profile build api
exit /b %ERRORLEVEL%

:start_database_service
echo [INFO] Starting database for '!PROFILE!' profile...
call :compose_profile up -d db
exit /b %ERRORLEVEL%

:start_api_service
set "RECREATE_API=%~1"
if "%RECREATE_API%"=="1" (
  call :compose_profile up -d --force-recreate %EXTRA_ARGS% api
) else (
  call :compose_profile up -d %EXTRA_ARGS% api
)
exit /b %ERRORLEVEL%

:maybe_auto_prune_before_start
if /I "%LF_SKIP_DOCKER_PRUNE%"=="1" (
  echo [INFO] Skipping Docker auto-prune because LF_SKIP_DOCKER_PRUNE=1.
  exit /b 0
)

call :get_date_stamp TODAY_STAMP
if exist "%DOCKER_PRUNE_STATE_FILE%" (
  set /p LAST_PRUNE_STAMP=<"%DOCKER_PRUNE_STATE_FILE%"
  if "!LAST_PRUNE_STAMP!"=="!TODAY_STAMP!" (
    echo [INFO] Skipping Docker auto-prune; it already ran today.
    exit /b 0
  )
)

echo [INFO] Auto-pruning unused Docker images...
docker image prune -af
if errorlevel 1 echo [WARN] Failed to prune unused Docker images. Continuing.

echo [INFO] Auto-pruning unused Docker build cache...
docker builder prune -af
if errorlevel 1 echo [WARN] Failed to prune unused Docker build cache. Continuing.

call :record_docker_prune_timestamp "!TODAY_STAMP!"
exit /b 0

:record_docker_prune_timestamp
set "STAMP_VALUE=%~1"
if not defined STAMP_VALUE exit /b 0
for %%I in ("%DOCKER_PRUNE_STATE_FILE%") do (
  if not exist "%%~dpI" mkdir "%%~dpI" >nul 2>&1
)
> "%DOCKER_PRUNE_STATE_FILE%" echo %STAMP_VALUE%
exit /b 0

:prune_build_artifacts
echo [INFO] Pruning dangling Docker images...
docker image prune -f >nul 2>&1
if errorlevel 1 echo [WARN] Failed to prune dangling Docker images.
exit /b 0

:get_date_stamp
set "__LF_NOW="
for /f "tokens=2 delims==" %%I in ('wmic os get LocalDateTime /value 2^>nul ^| find "="') do (
  if not defined __LF_NOW set "__LF_NOW=%%I"
)
if defined __LF_NOW (
  set "%~1=!__LF_NOW:~0,8!"
  exit /b 0
)
call :fallback_date_parts
set "%~1=%YYYY%%MM%%DD%"
exit /b 0

:get_timestamp
set "__LF_NOW="
for /f "tokens=2 delims==" %%I in ('wmic os get LocalDateTime /value 2^>nul ^| find "="') do (
  if not defined __LF_NOW set "__LF_NOW=%%I"
)
if defined __LF_NOW (
  set "%~1=!__LF_NOW:~0,8!_!__LF_NOW:~8,6!"
  exit /b 0
)
call :fallback_date_parts
set "%~1=%YYYY%%MM%%DD%_%HH%%MN%00"
exit /b 0

:fallback_date_parts
set "D1="
set "D2="
set "D3="
set "D4="
for /f "tokens=1-4 delims=/.- " %%A in ("%DATE%") do (
  set "D1=%%A"
  set "D2=%%B"
  set "D3=%%C"
  set "D4=%%D"
)

call :is_four_digits "!D1!" D1_IS_YEAR
if "!D1_IS_YEAR!"=="1" (
  set "YYYY=!D1!"
  set "RAW_MM=!D2!"
  set "RAW_DD=!D3!"
) else (
  set "YYYY=!D4!"
  set "RAW_MM=!D2!"
  set "RAW_DD=!D3!"
)

call :pad2 "!RAW_MM!" MM
call :pad2 "!RAW_DD!" DD

for /f "tokens=1-2 delims=:." %%H in ("%TIME%") do (
  set "RAW_HH=%%H"
  set "RAW_MN=%%I"
)

call :pad2 "!RAW_HH!" HH
call :pad2 "!RAW_MN!" MN
exit /b 0

:pad2
set "PAD_VALUE=%~1"
set "PAD_VALUE=0%PAD_VALUE%"
set "%~2=%PAD_VALUE:~-2%"
exit /b 0

:is_four_digits
echo(%~1| findstr /r "^[0-9][0-9][0-9][0-9]$" >nul
if errorlevel 1 (
  set "%~2=0"
) else (
  set "%~2=1"
)
exit /b 0

:load_env
for /f "usebackq tokens=1* delims==" %%A in ("%~1") do (
  set "ENV_NAME=%%A"
  if defined ENV_NAME (
    if not "!ENV_NAME:~0,1!"=="#" (
      set "ENV_VALUE=%%B"
      set "!ENV_NAME!=!ENV_VALUE!"
    )
  )
)
exit /b 0

:usage
echo Usage:
echo   %~nx0 [profile] ^<action^> [extra docker compose args]
echo.
echo Profiles:
echo   cpu    - local CPU development
echo   gpu    - local GPU workstation
echo   cloud  - single-host self-hosting
echo.
echo Legacy aliases:
echo   dev  - maps to gpu
echo   stg  - maps to cloud
echo   prod - maps to cloud
echo.
echo Actions:
echo   up
echo   up-build
echo   restart
echo   restart-build
echo   down
echo   destroy
echo   migrate
echo   logs
echo   reset-db
echo   config
echo.
echo Recommended Windows flow:
echo   copy .env.example .env
echo   %~nx0 up-build
goto :end

:end
popd
endlocal
exit /b %ERRORLEVEL%
