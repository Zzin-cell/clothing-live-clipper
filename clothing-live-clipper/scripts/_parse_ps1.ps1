$e = $null
$t = $null
$path = $args[0]
[void][System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$t, [ref]$e)
if ($e) {
  $e | ForEach-Object { $_.ToString() }
  exit 1
}
Write-Host "PARSE_OK"
exit 0
