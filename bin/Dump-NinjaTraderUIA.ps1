# Diagnostic: dump the UI Automation tree of all NinjaTrader windows.
$ErrorActionPreference = "Continue"
Add-Type -AssemblyName UIAutomationClient

$out = New-Object System.Text.StringBuilder

$procs = Get-Process | Where-Object { $_.ProcessName -like "*NinjaTrader*" }
[void]$out.AppendLine("Processes: " + ($procs | ForEach-Object { "$($_.Id):$($_.ProcessName)" }) -join ", ")

$desktop = [System.Windows.Automation.AutomationElement]::RootElement
$allWindows = $desktop.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)

foreach ($proc in $procs) {
    [void]$out.AppendLine("=== Windows for PID $($proc.Id) ===")
    for ($i = 0; $i -lt $allWindows.Count; $i++) {
        $win = $allWindows[$i]
        if ($win.Current.ProcessId -ne $proc.Id) { continue }
        [void]$out.AppendLine("WINDOW: '$($win.Current.Name)' Class='$($win.Current.ClassName)' ControlType=$($win.Current.ControlType.ProgrammaticName)")

        $conds = @(
            [System.Windows.Automation.ControlType]::Button,
            [System.Windows.Automation.ControlType]::Hyperlink,
            [System.Windows.Automation.ControlType]::Text,
            [System.Windows.Automation.ControlType]::ListItem,
            [System.Windows.Automation.ControlType]::MenuItem,
            [System.Windows.Automation.ControlType]::TabItem,
            [System.Windows.Automation.ControlType]::Image,
            [System.Windows.Automation.ControlType]::Pane
        )
        foreach ($ct in $conds) {
            $cond = [System.Windows.Automation.PropertyCondition]::new(
                [System.Windows.Automation.AutomationElement]::ControlTypeProperty, $ct)
            $els = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
            for ($j = 0; $j -lt $els.Count; $j++) {
                $el = $els[$j]
                $name = $el.Current.Name
                if ([string]::IsNullOrWhiteSpace($name)) { continue }
                $hasInvoke = $false
                try {
                    $p = $el.GetCurrentPattern([System.Windows.Automation.PatternIdentifiers]::InvokePattern)
                    $hasInvoke = ($null -ne $p)
                } catch {}
                [void]$out.AppendLine("  [$($ct.ProgrammaticName)] '$name' Invoke=$hasInvoke Class='$($el.Current.ClassName)' AutomationId='$($el.Current.AutomationId)'")
            }
        }
    }
}

$outFile = "$env:TEMP\nt_uia_dump.txt"
[System.IO.File]::WriteAllText($outFile, $out.ToString())
Write-Output "Dumped to $outFile"
