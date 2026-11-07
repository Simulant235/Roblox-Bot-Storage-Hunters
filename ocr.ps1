# Windows-Texterkennung (eingebaut in Windows 10/11) für die Timer der Bots.
# Aufruf: powershell -File ocr.ps1 bild1.png bild2.png ...   -> pro Bild eine Zeile "OCR|<text>"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

function Await($op, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    $task.Result
}

# Ausgabe als UTF-8 (sonst gehen kyrillische / japanische / ... Zeichen kaputt)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Sprache aus BOT_OCR_LANG (z.B. "ru-RU") nehmen, wenn Windows sie installiert hat - sonst die Windows-Sprache
$engine = $null
if ($env:BOT_OCR_LANG) {
    try {
        $null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
        $lang = New-Object Windows.Globalization.Language($env:BOT_OCR_LANG)
        if ([Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($lang)) {
            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
        }
    } catch { }
}
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
foreach ($f in $args) {
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($f)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
        "OCR|" + ($res.Text -replace "`r|`n", " ")
        $stream.Dispose()
    } catch {
        "OCR|"
    }
}
