# Reproduces the results of this repository from a fresh clone, on Windows (the twin of reproduce.sh):
#   1. installs the pinned Python (through uv) and Rust toolchain (through rustup),
#   2. regenerates every example and checks it is byte-identical to what is committed,
#   3. runs the test suite,
#   4. runs the scheduler demo with short waits,
#   5. checks the refinement and the properties of every example with the mCRL2 tools.
# Steps that need a tool the machine does not have are skipped, and the summary at the end says which.
# The versions are in versions.env. Usage: powershell -File scripts\reproduce.ps1 [-NoTests]
param([switch]$NoTests)
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$V = @{}
Get-Content (Join-Path $Root "versions.env") | ForEach-Object {
  if ($_ -match '^\s*([A-Z0-9_]+)=(\S+)\s*$') { $V[$Matches[1]] = $Matches[2] }
}

$script:Lines = New-Object System.Collections.Generic.List[string]
$script:Failed = $false
function Ok($m)   { $script:Lines.Add("  ok       $m"); Write-Host "ok: $m" }
function Skip($m) { $script:Lines.Add("  skipped  $m"); Write-Host "skipped: $m" }
function Bad($m)  { $script:Lines.Add("  FAILED   $m"); $script:Failed = $true; Write-Host "FAILED: $m" }
function Have($n) { return [bool](Get-Command $n -ErrorAction SilentlyContinue) }

$Out = Join-Path ([System.IO.Path]::GetTempPath()) ("naima-codegen-reproduce-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Out | Out-Null

try {
  # --- uv and the Python
  $Uv = $env:NAIMA_UV
  if (-not $Uv -and (Have "uv")) { $Uv = (Get-Command uv).Source }
  if (-not $Uv -and (Test-Path "$env:USERPROFILE\.local\bin\uv.exe")) { $Uv = "$env:USERPROFILE\.local\bin\uv.exe" }
  if (-not $Uv) { Write-Host "reproduce: uv is not installed. Install it from https://docs.astral.sh/uv/ and run this again."; exit 2 }
  & $Uv python install $V.PYTHON *> "$Out\python.log"
  if ($LASTEXITCODE -eq 0) { Ok "Python $($V.PYTHON) (uv)" } else { Get-Content "$Out\python.log"; Bad "Python $($V.PYTHON) (uv)" }
  $CG = Join-Path $Root "naima-codegen.cmd"

  # --- the Rust toolchain
  $HaveCargo = $false
  if (Have "rustup") {
    rustup toolchain install $V.RUST --profile minimal -c clippy -c rustfmt *> "$Out\rust.log"
    if ($LASTEXITCODE -eq 0) { Ok "Rust $($V.RUST) (rustup)"; $HaveCargo = $true } else { Get-Content "$Out\rust.log"; Bad "Rust $($V.RUST) (rustup)" }
  } elseif ((Have "cargo") -and ((cargo --version) -match " $([regex]::Escape($V.RUST)) ")) {
    Ok "Rust $($V.RUST) (found on PATH)"; $HaveCargo = $true
  } else {
    Skip "Rust: neither rustup nor cargo $($V.RUST) found, so the Rust tests and the scheduler demo are skipped"
  }

  # --- mCRL2
  $HaveMcrl2 = $false
  $Found = ""
  if (Have "mcrl22lps") {
    $first = (& mcrl22lps --version 2>&1 | Select-Object -First 1) | Out-String
    if ($first -match 'mCRL2 toolset (\S+)') { $Found = $Matches[1] }
  }
  if (-not $Found) { Skip "mCRL2: the tools are not on PATH (release $($V.MCRL2) is needed), so regeneration, refinement and properties are skipped" }
  elseif ($Found -ne $V.MCRL2) { Skip "mCRL2: release $Found is on PATH, the pin is $($V.MCRL2), so regeneration, refinement and properties are skipped" }
  else { Ok "mCRL2 $($V.MCRL2) on PATH"; $HaveMcrl2 = $true }

  $Examples = Get-ChildItem (Join-Path $Root "examples") -Directory | Sort-Object Name

  # --- regenerate and compare
  if ($HaveMcrl2) {
    foreach ($e in $Examples) {
      & $CG generate --check --project "examples\$($e.Name)" *> "$Out\gen-$($e.Name).log"
      if ($LASTEXITCODE -eq 0) { Ok "examples/$($e.Name): generate --check, byte-identical" }
      else { Get-Content "$Out\gen-$($e.Name).log"; Bad "examples/$($e.Name): generate --check" }
    }
  } else { Skip "generate --check of every example" }

  # --- the test suite
  if (-not $NoTests) {
    & $Uv run --frozen --no-dev python -B -m unittest discover -s tests *> "$Out\tests.log"
    if ($LASTEXITCODE -eq 0) { Ok ("test suite: " + ((Select-String -Path "$Out\tests.log" -Pattern '^(Ran|OK|FAILED)').Line -join " ")) }
    else { Get-Content "$Out\tests.log" -Tail 40; Bad "test suite" }
    if (-not $HaveMcrl2) { Skip "test suite: the cases that need mCRL2 skip themselves" }
    if (-not (Have "deno")) { Skip "test suite: the TypeScript backend's tests need Deno $($V.DENO)" }
  } else { Skip "test suite (-NoTests)" }

  # --- the scheduler demo, with short waits
  if ($HaveCargo) {
    cargo run -q --release --manifest-path examples/scheduler/app/Cargo.toml -- --min 0.2 --max 0.6 --seed 1 > "$Out\demo.log" 2> "$Out\demo.err"
    if (($LASTEXITCODE -eq 0) -and (Select-String -Path "$Out\demo.log" -SimpleMatch "the run is over" -Quiet)) {
      Ok ("scheduler demo: " + @(Select-String -Path "$Out\demo.log" -Pattern ' ends$').Count + " tasks ran and the run is over")
    } else { Get-Content "$Out\demo.log"; Get-Content "$Out\demo.err"; Bad "scheduler demo" }
  } else { Skip "scheduler demo" }

  # --- refinement and properties
  if ($HaveMcrl2) {
    foreach ($e in $Examples) {
      $n = $e.Name
      & $CG check-refinement --project "examples\$n" *> "$Out\ref-$n.log"
      if ($LASTEXITCODE -eq 0) { Ok "examples/${n}: refinement checks" } else { Get-Content "$Out\ref-$n.log"; Bad "examples/${n}: refinement checks" }
      if (Test-Path "examples\$n\properties") {
        & $CG check-refinement --properties --project "examples\$n" *> "$Out\prop-$n.log"
        if ($LASTEXITCODE -eq 0) { Ok ("examples/${n}: " + @(Select-String -Path "$Out\prop-$n.log" -SimpleMatch ": holds").Count + " properties hold") }
        else { Get-Content "$Out\prop-$n.log"; Bad "examples/${n}: properties" }
      }
    }
    # The generic event loop, modelled over an abstract stepper (docs/event-loop.md).
    mcrl22lps -q tests\loop-model\event-loop.mcrl2 "$Out\loop.lps" *> "$Out\loop.log"
    if ($LASTEXITCODE -eq 0) { lps2lts -q "$Out\loop.lps" "$Out\loop.lts" *>> "$Out\loop.log" }
    if (($LASTEXITCODE -eq 0) -and (Test-Path "$Out\loop.lts")) {
      foreach ($f in Get-ChildItem tests\loop-model\*.mcf) {
        lts2pbes -q -f $f.FullName "$Out\loop.lts" "$Out\p.pbes" *>> "$Out\loop.log"
        $r = if ($LASTEXITCODE -eq 0) { (pbessolve -q "$Out\p.pbes" 2>> "$Out\loop.log" | Out-String).Trim() } else { "" }
        if ($r -eq "true") { Ok "event loop: $($f.BaseName) holds" } else { Get-Content "$Out\loop.log"; Bad "event loop: $($f.BaseName)" }
      }
    } else { Get-Content "$Out\loop.log"; Bad "event loop model: state space" }
  } else { Skip "refinement checks and properties of every example, and of the event loop's model" }
}
finally {
  Remove-Item -Recurse -Force $Out -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Summary"
$script:Lines | ForEach-Object { Write-Host $_ }
if ($script:Failed) { exit 1 } else { exit 0 }
