---
name: خبير PowerShell
description: أوامر ويندوز الصحيحة لمعرفة معلومات الجهاز وإدارة الملفات والبرامج
triggers: powershell, باورشل, ويندوز, windows, جهازي, هارد, رام, عملية, عمليات, برامج, خدمة, service, شبكة, ip, مساحة, disk
---
- Prefer PowerShell cmdlets over old commands and keep output short: pipe to `Select-Object` / `Format-Table -AutoSize`, `Select-Object -First 15`.
- Disk: `Get-PSDrive -PSProvider FileSystem | Select Name,@{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}},@{n='UsedGB';e={[math]::Round($_.Used/1GB,1)}}`
- RAM/CPU/OS: `Get-CimInstance Win32_OperatingSystem`, `Get-CimInstance Win32_Processor`.
- Heaviest processes: `Get-Process | Sort-Object WorkingSet64 -Descending | Select -First 10 Name,Id,@{n='MB';e={[math]::Round($_.WorkingSet64/1MB)}}`
- Network: `Get-NetIPAddress -AddressFamily IPv4`, `Test-NetConnection host -Port 443`, `ipconfig /all`.
- Installed programs: `winget list`; install: `winget install --id <Id> -e --silent --accept-package-agreements --accept-source-agreements`.
- Files: `Get-ChildItem -Path $env:USERPROFILE\Downloads | Sort LastWriteTime -Desc | Select -First 20 Name,Length,LastWriteTime`.
- Never delete or change system settings without the user's clear request; show what will change first.
