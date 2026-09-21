$turnoPath = 'C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\turno.txt'
$avisoPath = 'C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\datos\aviso_gemini.txt'

Write-Host "Monitor de Gemini activo. Esperando turno..."
while ($true) {
    $content = Get-Content $turnoPath -Raw -ErrorAction SilentlyContinue
    if ($content -match 'GEMINI') {
        'TU TURNO' | Out-File $avisoPath -Encoding utf8
        Write-Host "TU TURNO detectado en turno.txt"
        break
    }
    Start-Sleep -Seconds 15
}
