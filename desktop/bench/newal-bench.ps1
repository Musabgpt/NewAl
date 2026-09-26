# NewAl — phase 1: measure which local models suit this computer.
#
# Downloads llama.cpp (CPU and Vulkan builds) and the candidate models, measures speed with
# llama-bench, checks quality with llama-server (Arabic answer, Python code that is really run,
# routing accuracy as JSON), and writes a report to send back.
#
# Run in PowerShell (no admin needed):
#   powershell -ExecutionPolicy Bypass -File .\newal-bench.ps1
#   powershell -ExecutionPolicy Bypass -File .\newal-bench.ps1 -Big     # also the 30B-A3B coder (13.8 GB)
#
# Works with Windows PowerShell 5.1 and PowerShell 7. Everything goes to %USERPROFILE%\NewAl.

param(
    [switch]$Big,                       # add Qwen3-Coder 30B-A3B (13.8 GB download)
    [switch]$SkipDownload,              # reuse what is already downloaded
    [string]$Root = (Join-Path $HOME "NewAl"),
    [string]$LlamaCpu = "",             # folder with llama-server / llama-bench (default: downloaded)
    [string]$LlamaVulkan = "",
    [string]$Only = ""                  # measure only models whose name contains this text
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is many times faster without the progress bar
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$IsWin = ($env:OS -eq "Windows_NT")
$Exe = ""
if ($IsWin) { $Exe = ".exe" }

$Models = Join-Path $Root "models"
$Llama = Join-Path $Root "llama"
$BenchDir = Join-Path $Root "bench"
foreach ($d in @($Root, $Models, $Llama, $BenchDir)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
$Report = New-Object System.Collections.Generic.List[string]
function Say([string]$s) { Write-Host $s; $Report.Add($s) }
function Step([string]$s) { Write-Host ""; Write-Host "==> $s" -ForegroundColor Cyan }

# ------------------------------------------------------------------ candidates
# Name, role, file, URL. Chosen from our phone measurements; this script decides for this PC.
$Candidates = @(
    @{ Name = "LFM2.5 1.2B";  Role = "router";  File = "LFM2.5-1.2B-Instruct-QAD-Q4_0.gguf";
       Url = "https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF/resolve/main/LFM2.5-1.2B-Instruct-QAD-Q4_0.gguf" },
    @{ Name = "LFM2.5 2.6B";  Role = "search";  File = "LFM2.5-2.6B-QAD-Q4_0.gguf";
       Url = "https://huggingface.co/LiquidAI/LFM2.5-2.6B-GGUF/resolve/main/LFM2.5-2.6B-QAD-Q4_0.gguf" },
    @{ Name = "Qwen3.5 4B";   Role = "prompt+judge"; File = "Qwen3.5-4B-Q4_K_M.gguf";
       Url = "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf" },
    @{ Name = "Qwen2.5-Coder 7B"; Role = "code"; File = "qwen2.5-coder-7b-instruct-q4_k_m.gguf";
       Url = "https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf" }
)
if ($Big) {
    $Candidates += @{ Name = "Qwen3-Coder 30B-A3B"; Role = "code (MoE)"; File = "Qwen3-Coder-30B-A3B-Instruct-UD-Q3_K_XL.gguf";
        Url = "https://huggingface.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF/resolve/main/Qwen3-Coder-30B-A3B-Instruct-UD-Q3_K_XL.gguf" }
}
if ($Only) { $Candidates = @($Candidates | Where-Object { $_.Name -like "*$Only*" }) }

# ------------------------------------------------------------------ helpers

function Download([string]$url, [string]$dest) {
    # curl.exe (built into Windows 10/11) resumes interrupted downloads; Invoke-WebRequest is the fallback.
    $curl = Get-Command "curl$Exe" -ErrorAction SilentlyContinue
    if ($IsWin) { $curl = Get-Command "curl.exe" -ErrorAction SilentlyContinue }
    if ($curl) {
        & $curl.Source -L --fail --retry 3 -C - -o $dest $url
        if ($LASTEXITCODE -ne 0) { throw "download failed ($LASTEXITCODE): $url" }
    } else {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    }
}

function IsGguf([string]$path) {
    if (-not (Test-Path $path)) { return $false }
    $fs = [IO.File]::OpenRead($path)
    try { $b = New-Object byte[] 4; [void]$fs.Read($b, 0, 4); return ([Text.Encoding]::ASCII.GetString($b) -eq "GGUF") }
    finally { $fs.Dispose() }
}

Add-Type -AssemblyName System.Net.Http
$Http = New-Object System.Net.Http.HttpClient
$Http.Timeout = [TimeSpan]::FromMinutes(15)

# JSON over HTTP as UTF-8 both ways (Windows PowerShell 5.1 would otherwise garble Arabic).
function PostJson([string]$url, $body) {
    $json = $body | ConvertTo-Json -Depth 20 -Compress
    $content = New-Object System.Net.Http.StringContent($json, [Text.Encoding]::UTF8, "application/json")
    $resp = $Http.PostAsync($url, $content).Result
    $bytes = $resp.Content.ReadAsByteArrayAsync().Result
    $text = [Text.Encoding]::UTF8.GetString($bytes)
    if (-not $resp.IsSuccessStatusCode) { throw "HTTP $([int]$resp.StatusCode): $text" }
    return ($text | ConvertFrom-Json)
}

function FindTool([string]$dir, [string]$name) {
    $f = Get-ChildItem -Path $dir -Recurse -Filter "$name$Exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($f) { return $f.FullName }
    return $null
}

# ------------------------------------------------------------------ 1. this computer

Step "Computer"
Say "# NewAl benchmark report"
Say ""
Say "Date: $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
if ($IsWin) {
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $cs = Get-CimInstance Win32_ComputerSystem
    $os = Get-CimInstance Win32_OperatingSystem
    $gpus = (Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) -join ", "
    Say "CPU: $($cpu.Name) — $($cpu.NumberOfCores) cores / $($cpu.NumberOfLogicalProcessors) threads"
    Say ("RAM: {0:N1} GB total, {1:N1} GB free now" -f ($cs.TotalPhysicalMemory / 1GB), ($os.FreePhysicalMemory * 1KB / 1GB))
    Say "GPU: $gpus"
    Say "Windows: $($os.Caption) $($os.Version)"
    $plan = (powercfg /getactivescheme) 2>$null
    Say "Power plan: $plan"
    $battery = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue
    if ($battery) { Say "On charger: $(@('?','no','yes')[[Math]::Min(2,[int]$battery.BatteryStatus)])  (plug the charger in for honest numbers)" }
} else {
    Say "OS: $([Environment]::OSVersion.VersionString) (test run, not Windows)"
    Say "CPU threads: $([Environment]::ProcessorCount)"
}
$physical = [Math]::Max(1, [Environment]::ProcessorCount / 2)
if ($IsWin) { $physical = [int]$cpu.NumberOfCores }
$python = $null
foreach ($p in @("python", "py", "python3")) {
    $c = Get-Command $p -ErrorAction SilentlyContinue
    if ($c -and $c.Source -notlike "*WindowsApps*") { $python = $c.Source; break }
}
if ($python) { Say "Python: $python" } else { Say "Python: not found (code test will be skipped)" }

# ------------------------------------------------------------------ 2. llama.cpp

Step "llama.cpp"
if ((-not $LlamaCpu) -and $IsWin) {
    $cpuDir = Join-Path $Llama "cpu"
    $vkDir = Join-Path $Llama "vulkan"
    if ((-not $SkipDownload) -or (-not (FindTool $cpuDir "llama-server"))) {
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" -Headers @{ "User-Agent" = "NewAl" }
        Say "llama.cpp release: $($rel.tag_name)"
        foreach ($pair in @(@("bin-win-cpu-x64\.zip$", $cpuDir), @("bin-win-vulkan-x64\.zip$", $vkDir))) {
            $asset = $rel.assets | Where-Object { $_.name -match $pair[0] } | Select-Object -First 1
            if (-not $asset) { Say "  (no asset matching $($pair[0]))"; continue }
            $zip = Join-Path $Llama $asset.name
            if (-not (Test-Path $zip)) { Write-Host "  downloading $($asset.name)"; Download $asset.browser_download_url $zip }
            if (Test-Path $pair[1]) { Remove-Item -Recurse -Force $pair[1] }
            Expand-Archive -Path $zip -DestinationPath $pair[1] -Force
        }
    }
    $LlamaCpu = $cpuDir
    if (-not $LlamaVulkan) { $LlamaVulkan = $vkDir }
}
$Server = FindTool $LlamaCpu "llama-server"
$Bench = FindTool $LlamaCpu "llama-bench"
if (-not $Server -or -not $Bench) { throw "llama-server / llama-bench not found in $LlamaCpu" }
$VkBench = $null
if ($LlamaVulkan) { $VkBench = FindTool $LlamaVulkan "llama-bench" }
Say "Engine: $Server"

# ------------------------------------------------------------------ 3. models

Step "Models"
$present = @()
foreach ($m in $Candidates) {
    $m.Path = Join-Path $Models $m.File
    if (-not $SkipDownload) {
        Write-Host "  $($m.Name) -> $($m.File)"
        Download $m.Url $m.Path
    }
    if (IsGguf $m.Path) { $present += $m } else { Say "Skipped $($m.Name): $($m.File) is missing" }
}
$Candidates = $present
if ($Candidates.Count -eq 0) { throw "no models in $Models" }

# ------------------------------------------------------------------ 4. speed

function BenchRun([string]$exe, [string]$model, [int]$threads, [int]$ngl) {
    $benchArgs = @("-m", $model, "-p", "256", "-n", "64", "-r", "2", "-t", "$threads", "-ngl", "$ngl", "-o", "json")
    $out = & $exe @benchArgs 2>$null | Out-String
    $rows = $out | ConvertFrom-Json
    $pp = ($rows | Where-Object { $_.n_prompt -gt 0 -and $_.n_gen -eq 0 } | Select-Object -First 1).avg_ts
    $tg = ($rows | Where-Object { $_.n_gen -gt 0 -and $_.n_prompt -eq 0 } | Select-Object -First 1).avg_ts
    return @([double]$pp, [double]$tg)
}

Step "Speed (llama-bench: reading 256 prompt tokens, writing 64 tokens)"
$logical = [Environment]::ProcessorCount
$threadChoices = @($physical, $logical) | Select-Object -Unique
$bestThreads = $physical
$speed = @{}
Say ""
Say "## Speed (tokens per second, CPU)"
Say ""
Say "| Model | Size | Threads | Read prompt | Write |"
Say "|---|---|---|---|---|"
$first = $true
foreach ($m in $Candidates) {
    $size = "{0:N1} GB" -f ((Get-Item (Resolve-Path $m.Path)).Length / 1GB)
    # The thread count that writes fastest is found on the first model and reused.
    $tries = @($bestThreads)
    if ($first) { $tries = $threadChoices }
    $bestTg = 0
    foreach ($t in $tries) {
        Write-Host "  $($m.Name), $t threads"
        $r = BenchRun $Bench $m.Path $t 0
        Say ("| {0} | {1} | {2} | {3:N1} | {4:N1} |" -f $m.Name, $size, $t, $r[0], $r[1])
        if ($r[1] -gt $bestTg) { $bestTg = $r[1]; if ($first) { $bestThreads = $t } }
        $speed[$m.Name] = $r
    }
    $first = $false
}
if ($VkBench) {
    Say ""
    Say "## Intel GPU through Vulkan (whole model on the GPU)"
    Say ""
    Say "| Model | Read prompt | Write |"
    Say "|---|---|---|"
    foreach ($m in ($Candidates | Select-Object -First 3)) {
        Write-Host "  Vulkan: $($m.Name)"
        try {
            $r = BenchRun $VkBench $m.Path $bestThreads 99
            Say ("| {0} | {1:N1} | {2:N1} |" -f $m.Name, $r[0], $r[1])
        } catch { Say "| $($m.Name) | failed | $($_.Exception.Message) |" }
    }
}

# ------------------------------------------------------------------ 5. quality

$RouteCases = @(
    @("اكتب لي سكربت بايثون ينظم ملفات التنزيلات حسب النوع", "code"),
    @("fix this error: ModuleNotFoundError: No module named 'requests'", "code"),
    @("شو آخر أخبار الذكاء الاصطناعي هالأسبوع؟", "search"),
    @("what is the current price of bitcoin", "search"),
    @("حوّل فكرتي لطلب واضح: بدي موقع بسيط لمطعم", "prompt"),
    @("rewrite my request as a precise prompt: an app to track expenses", "prompt"),
    @("مرحبا كيفك؟", "chat"),
    @("tell me a short joke", "chat")
)
$RouteSchema = @{ type = "object"; properties = @{ route = @{ type = "string"; enum = @("code", "search", "prompt", "chat") } }; required = @("route") }
$RouteSystem = @"
Classify the user's request into one route. Reply with JSON only.
- code: write, run, explain or fix code, scripts, commands, programs, or error messages from programs.
- search: needs fresh facts from the internet: news, prices, weather, recent events, "latest".
- prompt: the user asks to turn an idea into a clear prompt/request/specification, or to rewrite their request.
- chat: greetings, jokes, opinions, general talk.
Examples:
"اكتب دالة بايثون تعكس نص" -> code
"TypeError: 'NoneType' object is not subscriptable" -> code
"كم سعر الذهب اليوم" -> search
"who won the match yesterday" -> search
"صغلي فكرتي كطلب واضح: تطبيق مذاكرة" -> prompt
"make this a better prompt: a logo for my shop" -> prompt
"صباح الخير" -> chat
"what do you think about cats" -> chat
"@

function Ask($port, $messages, $maxTokens, $extra) {
    $body = @{ messages = $messages; max_tokens = $maxTokens; temperature = 0.2; chat_template_kwargs = @{ enable_thinking = $false } }
    if ($extra) { foreach ($k in $extra.Keys) { $body[$k] = $extra[$k] } }
    $r = PostJson "http://127.0.0.1:$port/v1/chat/completions" $body
    if (-not $r.choices[0].message.content) {
        # LFM2.5: the server's reasoning parser swallows the answer (and skips the JSON grammar).
        # reasoning_format=none fixes that, but breaks the grammar of Qwen3.5, hence only as a retry.
        $body["reasoning_format"] = "none"
        $r = PostJson "http://127.0.0.1:$port/v1/chat/completions" $body
    }
    $text = [string]$r.choices[0].message.content
    $tps = 0
    if ($r.timings) { $tps = [double]$r.timings.predicted_per_second }
    return @($text, $tps)
}

Step "Quality (Arabic answer, Python code that is run, routing)"
Say ""
Say "## Quality"
$port = 18089
foreach ($m in $Candidates) {
    Write-Host "  $($m.Name): starting llama-server"
    $log = Join-Path $BenchDir ("server-" + ($m.File -replace '\.gguf$', '') + ".log")
    $start = @{ FilePath = $Server; PassThru = $true; RedirectStandardError = $log; RedirectStandardOutput = "$log.out"
        ArgumentList = @("-m", "`"$($m.Path)`"", "--port", "$port", "-c", "4096", "-t", "$bestThreads", "--jinja", "--no-webui") }
    if ($IsWin) { $start.WindowStyle = "Hidden" }
    $proc = Start-Process @start
    try {
        $ready = $false
        for ($i = 0; $i -lt 180 -and -not $ready; $i++) {
            Start-Sleep -Seconds 1
            try { $h = $Http.GetAsync("http://127.0.0.1:$port/health").Result; $ready = $h.IsSuccessStatusCode } catch {}
            if ($proc.HasExited) { throw "llama-server stopped, see $log" }
        }
        if (-not $ready) { throw "llama-server did not start in 3 minutes" }

        Say ""
        Say "### $($m.Name) ($($m.Role))"
        # Arabic
        $a = Ask $port @(@{ role = "user"; content = "اشرح باختصار شو هو الذكاء الاصطناعي بثلاث نقاط، باللهجة الشامية." }) 300 $null
        Say ("**Arabic** ({0:N1} tok/s):" -f $a[1])
        Say ""
        Say ("> " + (($a[0] -replace "`r", "").Trim() -replace "`n", "`n> "))
        # Python, really run
        $c = Ask $port @(@{ role = "user"; content = "Write a Python function is_prime(n) that returns True if n is a prime number. Reply with one python code block only." }) 400 $null
        $code = $c[0]
        if ($code -match '(?s)```(?:python|py)?\s*\n(.*?)```') { $code = $Matches[1] }
        $verdict = "skipped (no Python)"
        if ($python) {
            $test = $code + "`n`nassert [n for n in range(30) if is_prime(n)] == [2,3,5,7,11,13,17,19,23,29]`nassert not is_prime(1) and not is_prime(0) and is_prime(97)`nprint('PASS')`n"
            $file = Join-Path $BenchDir "is_prime_test.py"
            [IO.File]::WriteAllText($file, $test, (New-Object Text.UTF8Encoding($false)))
            $out = & $python $file 2>&1 | Out-String
            if ($out -match "PASS") { $verdict = "PASS" } else { $verdict = "FAIL: " + ($out.Trim() -split "`n" | Select-Object -Last 1) }
        }
        Say ""
        Say ("**Python is_prime** ({0:N1} tok/s): {1}" -f $c[1], $verdict)
        # Routing as constrained JSON
        $ok = 0
        $wrong = @()
        foreach ($rc in $RouteCases) {
            $r = Ask $port @(@{ role = "system"; content = $RouteSystem }, @{ role = "user"; content = $rc[0] }) 30 @{ temperature = 0; response_format = @{ type = "json_schema"; json_schema = @{ name = "route"; schema = $RouteSchema } } }
            $route = ""
            try { $route = ($r[0] | ConvertFrom-Json).route } catch { $route = "invalid" }
            if ($route -eq $rc[1]) { $ok++ } else { $wrong += "$($rc[1])→$route" }
        }
        Say ""
        Say ("**Routing**: {0}/{1} correct {2}" -f $ok, $RouteCases.Count, ($(if ($wrong.Count) { "(wrong: " + ($wrong -join ", ") + ")" } else { "" })))
    } catch {
        Say ""
        Say "### $($m.Name): error — $($_.Exception.Message)"
    } finally {
        if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
        Start-Sleep -Seconds 2
    }
}

# ------------------------------------------------------------------ 6. report

$out = Join-Path $BenchDir "report.md"
[IO.File]::WriteAllLines($out, $Report, (New-Object Text.UTF8Encoding($false)))
Step "Done"
Write-Host "Report: $out" -ForegroundColor Green
if ($IsWin) {
    try { Set-Clipboard -Value ($Report -join "`n"); Write-Host "The report is also copied: paste it into the chat." -ForegroundColor Green } catch {}
    Start-Process notepad.exe $out
}
