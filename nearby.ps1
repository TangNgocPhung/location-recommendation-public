<#
.SYNOPSIS
    Chay cac buoc Phase 3-8 cua Nearby ma khong phai go lenh docker dai.

.DESCRIPTION
    Moi lenh chay BEN TRONG container backend, vi hai ly do:

    1. Cong 5432 tren may nay dang bi Postgres native chiem, nen script chay
       tren host se ket noi nham database va bao "password authentication
       failed".
    2. Ma nguon duoc MOUNT vao container. Neu khong mount, container chay ban
       code da nuong trong image tu lan build truoc — lenh van chay, van in ra
       so lieu, nhung la so lieu cua code cu. Day la loi im lang, khong bao gi.

.EXAMPLE
    .\nearby.ps1 up          # bat toan bo stack
    .\nearby.ps1 status      # kiem tra du lieu + mo hinh LTR
    .\nearby.ps1 audit       # Phase 6: chi kiem tra dataset, KHONG train
    .\nearby.ps1 train       # Phase 4 + 5: dung tap huan luyen roi huan luyen
    .\nearby.ps1 eval        # Phase 7: baseline vs LambdaMART
    .\nearby.ps1 ablation    # Phase 8: thang A-E
    .\nearby.ps1 labels      # sinh lai khung 400 cap can gan nhan
    .\nearby.ps1 test        # chay unit test
    .\nearby.ps1 down        # tat stack
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet('up', 'down', 'status', 'train', 'eval', 'eval-holdout', 'ablation', 'audit', 'labels', 'ratings', 'test', 'logs')]
    [string]$Command = 'status',

    # Tham so truyen thang cho script Python ben trong, vi du:
    #   .\nearby.ps1 train -- --source both --force
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

# KHONG dat 'Stop': docker ghi tien do ra stderr, va voi ErrorActionPreference
# = Stop thi PowerShell 5.1 coi moi dong stderr cua chuong trinh ngoai la loi
# ket thuc (NativeCommandError) — lenh chay thanh cong van bi bao that bai.
# Thay vao do kiem tra $LASTEXITCODE, day moi la ma thoat that.
$ErrorActionPreference = 'Continue'
$Root = $PSScriptRoot
$Project = 'nearby-dev'

# Mount ma nguon de container luon chay code hien tai tren dia.
$Mounts = @(
    '-v', "${Root}/backend/app:/app/app",
    '-v', "${Root}/backend/scripts:/app/scripts",
    '-v', "${Root}/backend/results:/app/results",
    '-v', "${Root}/backend/tests:/app/tests"
)

function Invoke-InBackend {
    param([string]$Service = 'backend', [string[]]$CommandArgs, [string[]]$ExtraEnv = @())
    $argv = @('compose', '-p', $Project, 'run', '--rm', '--no-deps', '-e', 'PYTHONIOENCODING=utf-8')
    foreach ($item in $ExtraEnv) { $argv += @('-e', $item) }
    $argv += $Mounts
    $argv += $Service
    $argv += $CommandArgs
    & docker @argv
    if ($LASTEXITCODE -ne 0) { throw "Lenh that bai voi ma $LASTEXITCODE" }
}

switch ($Command) {

    'up' {
        Write-Host '== Bat cac dich vu du lieu ==' -ForegroundColor Cyan
        & docker compose -p $Project up -d database redis opensearch neo4j
        Write-Host '== Cho database san sang ==' -ForegroundColor Cyan
        for ($i = 0; $i -lt 20; $i++) {
            $state = (& docker inspect --format '{{.State.Health.Status}}' "$Project-database-1" 2>$null)
            if ($state -eq 'healthy') { break }
            Start-Sleep -Seconds 3
        }
        Write-Host '== Chay migration ==' -ForegroundColor Cyan
        & docker compose -p $Project run --rm migrate
        Write-Host '== Bat backend + giao dien ==' -ForegroundColor Cyan
        & docker compose -p $Project up -d backend stream-worker frontend
        Write-Host ''
        Write-Host 'Giao dien : http://localhost:3001   <- mo cai nay' -ForegroundColor Green
        Write-Host 'API       : http://localhost:8000   (khong co trang o /)' -ForegroundColor DarkGray
        Write-Host '  suc khoe: http://localhost:8000/health' -ForegroundColor DarkGray
        Write-Host '  tai lieu: http://localhost:8000/docs' -ForegroundColor DarkGray
        Write-Host '  mo hinh : http://localhost:8000/api/v1/ltr/status' -ForegroundColor DarkGray
        Write-Host 'Frontend can ~30 giay de bien dich lan dau.' -ForegroundColor DarkGray
    }

    'down' {
        & docker compose -p $Project down
    }

    'logs' {
        & docker compose -p $Project logs -f --tail 100 backend
    }

    'status' {
        Write-Host '== Do phu du lieu POI ==' -ForegroundColor Cyan
        $sql = @'
SELECT 'POI tong' AS chi_so, COUNT(*)::text AS gia_tri FROM pois
UNION ALL SELECT 'co rating', COUNT(*)::text FROM pois WHERE rating IS NOT NULL
UNION ALL SELECT 'co gio mo cua', COUNT(*)::text FROM pois
         WHERE opening_hours IS NOT NULL AND opening_hours <> '{}'::jsonb
UNION ALL SELECT 'danh gia nguoi dung', COUNT(*)::text FROM poi_reviews
UNION ALL SELECT 'nhom request co click', COUNT(DISTINCT metadata->>'request_id')::text
         FROM ingestion_events WHERE event_type = 'poi_click' AND metadata ? 'request_id';
'@
        & docker exec "$Project-database-1" psql -U nearby_dev -d nearby_dev -c $sql

        Write-Host '== Truy van da gan nhan ==' -ForegroundColor Cyan
        $judgments = Join-Path $Root 'backend/tests/fixtures/relevance_judgments.json'
        if (Test-Path $judgments) {
            $count = (Get-Content $judgments -Raw | ConvertFrom-Json).Count
            Write-Host "  $count truy van (can >= 30 de huan luyen LTR co co so)"
        }

        Write-Host '== Mo hinh LTR ==' -ForegroundColor Cyan
        try {
            $info = Invoke-RestMethod -Uri 'http://localhost:8000/api/v1/ltr/status' -TimeoutSec 10
            $info | Format-List
        } catch {
            Write-Host '  Backend chua chay. Chay ".\nearby.ps1 up" truoc.' -ForegroundColor Yellow
        }
    }

    'train' {
        # Mac dinh --source judgment. Them tham so khac o sau, vi du:
        #   .\nearby.ps1 train --source both
        $argv = @('python', 'scripts/train_ltr.py')
        if ($Rest -and $Rest.Count -gt 0) { $argv += $Rest } else { $argv += @('--source', 'judgment') }

        # Container "backend" (service dang chay that) KHONG mount code song —
        # no dung image da build san (xem docker-compose.yml, khong co
        # 'volumes:' o service backend). Neu train ra model.txt moi ma khong
        # rebuild lai image, production van phuc vu model CU mot cach im lang
        # — day la loi da gap that su (xem Phase 8): API tra ranker=ltr nhung
        # thuc chat van la model train tu vai chuc dong du lieu cu.
        $modelPath = Join-Path $Root 'backend/app/ltr/model.txt'
        $before = if (Test-Path $modelPath) { (Get-Item $modelPath).LastWriteTimeUtc } else { $null }

        Invoke-InBackend -CommandArgs $argv

        $after = if (Test-Path $modelPath) { (Get-Item $modelPath).LastWriteTimeUtc } else { $null }
        $modelUpdated = ($null -ne $after) -and ($after -ne $before)

        if ($modelUpdated) {
            Write-Host ''
            Write-Host 'Model moi da ghi vao backend/app/ltr/model.txt.' -ForegroundColor Green
            Write-Host 'Container "backend" dang chay dung IMAGE CU, chua thay model nay.' -ForegroundColor Yellow
            $answer = Read-Host 'Rebuild + restart backend de dua model moi vao production ngay? [y/N]'
            if ($answer -match '^[Yy]') {
                Write-Host '== Rebuild backend ==' -ForegroundColor Cyan
                & docker compose -p $Project build backend
                if ($LASTEXITCODE -ne 0) { throw "Build that bai voi ma $LASTEXITCODE" }
                Write-Host '== Restart backend ==' -ForegroundColor Cyan
                & docker compose -p $Project up -d backend
                Write-Host 'Xong. Kiem tra: http://localhost:8000/api/v1/ltr/status' -ForegroundColor Green
            } else {
                Write-Host 'Bo qua. Production van dung model CU cho toi khi ban build lai backend.' -ForegroundColor DarkGray
            }
        }
    }

    'eval' {
        $argv = @('python', 'scripts/eval_rankers.py')
        if ($Rest -and $Rest.Count -gt 0) { $argv += $Rest } else { $argv += @('--k', '10') }
        Invoke-InBackend -CommandArgs $argv
    }

    'eval-holdout' {
        # Phase 9 — huan luyen model TAM chi tren phan train, danh gia tren
        # nhom qid CHUA TUNG thay. Khac 'eval': khong dung app/ltr/model.txt
        # production, khong dung 3 truy van judgment lam toan bo test set.
        $argv = @('python', 'scripts/eval_holdout.py')
        if ($Rest -and $Rest.Count -gt 0) { $argv += $Rest } else { $argv += @('--k', '5') }
        Invoke-InBackend -CommandArgs $argv
    }

    'ablation' {
        Invoke-InBackend -CommandArgs @('python', 'scripts/run_ablation.py')
    }

    'audit' {
        # Phase 6 — chi kiem tra dataset, KHONG train. Chay lai lenh nay dinh
        # ky de xem traffic that da tich luy toi dau, khong phai quyet dinh
        # bang mot con so cung nhu "1000 dong la du".
        $argv = @('python', 'scripts/audit_dataset.py')
        if ($Rest -and $Rest.Count -gt 0) { $argv += $Rest }
        Invoke-InBackend -CommandArgs $argv
    }

    'labels' {
        # Can backend dang chay: script goi API that de lay ung vien.
        # Dung anh backend-tests vi chi anh do co httpx.
        Invoke-InBackend -Service 'backend-tests' `
            -ExtraEnv @('API_BASE_URL=http://backend:8000') `
            -CommandArgs @('python', 'scripts/build_judgment_template.py')
        Write-Host ''
        Write-Host 'Khung gan nhan: backend/tests/fixtures/judgment_template.json' -ForegroundColor Green
        Write-Host 'Dien cot "grade" (0-3) roi gop vao relevance_judgments.json.' -ForegroundColor Green
    }

    'ratings' {
        # Mac dinh CHAY THU 20 POI, khong ghi database. Moi truy van deu tinh
        # tien nen phai xem ti le khop trung binh truoc khi chay 3000 cai.
        #   nearby ratings                -> thu 20 POI, khong ghi
        #   nearby ratings --limit 500    -> ghi that 500 POI
        $argv = @('python', 'scripts/enrich_ratings_google.py')
        if ($Rest -and $Rest.Count -gt 0) { $argv += $Rest } else { $argv += @('--limit', '20', '--dry-run') }
        $key = $env:GOOGLE_MAPS_API_KEY
        $envFile = Join-Path $Root '.env'
        if (-not $key -and (Test-Path $envFile)) {
            $line = Select-String -Path $envFile -Pattern '^GOOGLE_MAPS_API_KEY=(.+)$' | Select-Object -First 1
            if ($line) { $key = $line.Matches[0].Groups[1].Value.Trim() }
        }
        if (-not $key) {
            Write-Host 'Thieu GOOGLE_MAPS_API_KEY.' -ForegroundColor Yellow
            Write-Host 'Tao key o Google Cloud Console (bat "Places API (New)" + gan billing),' -ForegroundColor Yellow
            Write-Host 'roi them mot dong vao file .env o thu muc goc:' -ForegroundColor Yellow
            Write-Host '    GOOGLE_MAPS_API_KEY=<key cua ban>' -ForegroundColor Yellow
            return
        }
        Invoke-InBackend -ExtraEnv @("GOOGLE_MAPS_API_KEY=$key") -CommandArgs $argv
    }

    'test' {
        $argv = @()
        if ($Rest -and $Rest.Count -gt 0) { $argv = @('pytest') + $Rest }
        Invoke-InBackend -Service 'backend-tests' -CommandArgs $argv
    }
}
