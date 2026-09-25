# Pre-push audit. Run from the repo root.
# Every check must return zero matches. If any returns a hit, do not push.

$ErrorActionPreference = "Stop"
$failed = $false

Write-Host "`n[1/4] Tracked files that look like secrets or state" -ForegroundColor Cyan
$hits = git ls-files | Select-String -Pattern `
    '\.env$|\.pem$|\.key$|_state\.json$|_mappings\.json$|optimal_config|param_history|\.db$|\.log$|\.pkl$|\.joblib$'
if ($hits) { Write-Host $hits -ForegroundColor Red; $failed = $true }
else { Write-Host "  clean" -ForegroundColor Green }

Write-Host "`n[2/4] API keys or secrets anywhere in the tree" -ForegroundColor Cyan
$hits = git grep -n -i -E `
    'PRIVATE_KEY\s*=\s*[0-9a-fA-F]{16,}|ODDS_API_KEY\s*=\s*\S+|SHARP_API_KEY\s*=\s*\S+|SX_API_KEY\s*=\s*\S+|POLYGON_RPC\s*=\s*\S+|bearer\s+[A-Za-z0-9._~+/-]{20,}|api[_-]?key\s*=\s*["'']?[A-Za-z0-9._~+/-]{20,}' `
    -- ':!*.example' ':!*.md' ':!scripts/audit.ps1' 2>$null
if ($hits) { Write-Host $hits -ForegroundColor Red; $failed = $true }
else { Write-Host "  clean" -ForegroundColor Green }

Write-Host "`n[3/4] Named strategy parameters anywhere in the tree" -ForegroundColor Cyan
$hits = git grep -n -E `
    'maker_taker_hit_prob|adverse_selection_factor|exit_ladder|kelly_fraction|capture_fraction|maker_min_edge|quadrant_matrix|apply_quadrant_multipliers|edge_engine|autonomous_orchestrator|ctf_manager' `
    -- ':!*.example' ':!*.md' ':!scripts/audit.ps1' 2>$null
if ($hits) { Write-Host $hits -ForegroundColor Red; $failed = $true }
else { Write-Host "  clean" -ForegroundColor Green }

Write-Host "`n[4/4] Polymarket token IDs (70+ digit numeric literals)" -ForegroundColor Cyan
# Narrowed to shaped match: quoted string of 70+ digits OR JSON-style assignment.
$hits = git grep -n -E `
    '["'']([0-9]{70,})["'']|:\s*[0-9]{70,}' -- ':!scripts/audit.ps1' 2>$null
if ($hits) { Write-Host $hits -ForegroundColor Red; $failed = $true }
else { Write-Host "  clean" -ForegroundColor Green }

Write-Host ""
if ($failed) {
    Write-Host "AUDIT FAILED — do not push." -ForegroundColor Red
    exit 1
} else {
    Write-Host "AUDIT PASSED — safe to push." -ForegroundColor Green
    exit 0
}
