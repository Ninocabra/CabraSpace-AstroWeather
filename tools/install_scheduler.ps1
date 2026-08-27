# Registra el colector de AstroWeather en el Programador de tareas de Windows.
#
# Arranca al iniciar sesion y se queda corriendo en segundo plano. Mientras
# viva en este PC solo recoge dato con el equipo encendido: el VPS de la fase 1
# es lo que lo convierte en 24/7.
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File tools\install_scheduler.ps1 -Startup
#       -> carpeta de Inicio, SIN administrador (lo normal en este PC)
#   powershell -ExecutionPolicy Bypass -File tools\install_scheduler.ps1
#       -> Programador de tareas; necesita PowerShell como administrador
#   powershell -ExecutionPolicy Bypass -File tools\install_scheduler.ps1 -Remove

param(
    [switch]$Remove,
    [switch]$Startup,   # sin permisos de administrador: carpeta de Inicio
    [string]$TaskName = "CabraSpace AstroWeather"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$startupDir = [Environment]::GetFolderPath("Startup")
$startupFile = Join-Path $startupDir "CabraSpace-AstroWeather.vbs"

if ($Remove) {
    if (Test-Path $startupFile) {
        Remove-Item $startupFile
        Write-Output "Lanzador de Inicio eliminado."
    }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "Tarea '$TaskName' eliminada."
    } else {
        Write-Output "No habia ninguna tarea '$TaskName'."
    }
    return
}

$python = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = (Get-Command python).Source }

# --quiet: sin consola, todo va a data\scheduler.log
if ($Startup) {
    # El Programador de tareas exige elevacion; la carpeta de Inicio no.
    # Un .vbs arranca pythonw sin ventana de consola.
    $vbs = @"
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "$projectRoot"
shell.Run """$python"" -m astroweather.scheduler --quiet", 0, False
"@
    Set-Content -Path $startupFile -Value $vbs -Encoding ASCII
    Write-Output "Lanzador instalado en la carpeta de Inicio:"
    Write-Output "  $startupFile"
    Write-Output "  python : $python"
    Write-Output "  log    : $projectRoot\data\scheduler.log"
    Write-Output ""
    Write-Output "Arranca solo al iniciar sesion. Para lanzarlo ya:"
    Write-Output "  wscript ""$startupFile"""
    return
}

$action = New-ScheduledTaskAction -Execute $python `
    -Argument "-m astroweather.scheduler --quiet" `
    -WorkingDirectory $projectRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)   # 0 = sin limite: es un servicio

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description `
    "Recoge los datos que caducan de AstroWeather (miembros EPS, sensores de AstroCamp)." | Out-Null

Write-Output "Tarea '$TaskName' registrada."
Write-Output "  python : $python"
Write-Output "  carpeta: $projectRoot"
Write-Output "  log    : $projectRoot\data\scheduler.log"
Write-Output ""
Write-Output "Arrancarla ahora sin reiniciar sesion:"
Write-Output "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Output "Ver si esta viva:"
Write-Output "  Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
