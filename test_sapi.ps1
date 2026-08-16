Add-Type -AssemblyName System.Speech
Write-Host "SPEAK NOW for 8 seconds..."
$culture = [System.Globalization.CultureInfo]::GetCultureInfo("zh-CN")
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)
$dictation = New-Object System.Speech.Recognition.DictationGrammar
$engine.LoadGrammar($dictation)
$engine.SetInputToDefaultAudioDevice()
$result = $engine.Recognize([TimeSpan]::FromSeconds(8))
if ($result) { Write-Output ("SAPI_RESULT: " + $result.Text) } else { Write-Output "SAPI_RESULT: (empty)" }
