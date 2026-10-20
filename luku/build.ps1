# Build script for LUKU Live APK on Windows
$ErrorActionPreference = "Stop"

$JDK_BIN = "C:\Program Files\Android\Android Studio\jbr\bin"
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
$env:Path = "$JDK_BIN;" + $env:Path

$BUILD_TOOLS = "C:\Users\YOHANNA\AppData\Local\Android\Sdk\build-tools\34.0.0"
$ANDROID_JAR = "C:\Users\YOHANNA\AppData\Local\Android\Sdk\platforms\android-34\android.jar"
$AAPT2 = "$BUILD_TOOLS\aapt2.exe"
$D8 = "$BUILD_TOOLS\d8.bat"
$ZIPALIGN = "$BUILD_TOOLS\zipalign.exe"
$APKSIGNER = "$BUILD_TOOLS\apksigner.bat"
$KEYSTORE = "$env:USERPROFILE\.android\debug.keystore"

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$OUT = Join-Path $SCRIPT_DIR "build"

Write-Host "== Cleaning and setting up directories =="
if (Test-Path $OUT) { Remove-Item -Recurse -Force $OUT }
New-Item -ItemType Directory -Path "$OUT\compiled", "$OUT\gen", "$OUT\classes", "$OUT\dex" | Out-Null

Write-Host "== aapt2 compile =="
& $AAPT2 compile --dir "$SCRIPT_DIR\res" -o "$OUT\compiled\res.zip"
if ($LASTEXITCODE -ne 0) { throw "aapt2 compile failed" }

Write-Host "== aapt2 link =="
& $AAPT2 link -I $ANDROID_JAR --manifest "$SCRIPT_DIR\AndroidManifest.xml" `
  --min-sdk-version 26 --target-sdk-version 34 --version-code 11 --version-name 6.4 `
  --java "$OUT\gen" -o "$OUT\base.apk" "$OUT\compiled\res.zip"
if ($LASTEXITCODE -ne 0) { throw "aapt2 link failed" }

Write-Host "== javac =="
$javaFiles = Get-ChildItem -Path "$SCRIPT_DIR\src", "$OUT\gen" -Filter "*.java" -Recurse | ForEach-Object { '"' + $_.FullName.Replace('\', '/') + '"' }
$sourcesFile = "$OUT\sources.txt"
[System.IO.File]::WriteAllLines($sourcesFile, $javaFiles, (New-Object System.Text.UTF8Encoding($false)))

& "$JDK_BIN\javac.exe" -nowarn -Xlint:-options -source 8 -target 8 -encoding UTF-8 `
  -bootclasspath $ANDROID_JAR -classpath $ANDROID_JAR `
  -d "$OUT\classes" "@$sourcesFile"
if ($LASTEXITCODE -ne 0) { throw "javac failed" }

Write-Host "== d8 dexing =="
& "$JDK_BIN\jar.exe" -cf "$OUT\classes.jar" -C "$OUT\classes" .
& $D8 --min-api 26 --output "$OUT\dex" "$OUT\classes.jar"
if ($LASTEXITCODE -ne 0) { throw "d8 failed" }

Write-Host "== Adding classes.dex to base.apk =="
Copy-Item "$OUT\base.apk" "$OUT\unsigned.apk"
$dexFile = "$OUT\dex\classes.dex"
& "$JDK_BIN\jar.exe" -uf "$OUT\unsigned.apk" -C "$OUT\dex" classes.dex
if ($LASTEXITCODE -ne 0) { throw "jar update failed" }

Write-Host "== zipalign =="
& $ZIPALIGN -f -p 4 "$OUT\unsigned.apk" "$OUT\aligned.apk"
if ($LASTEXITCODE -ne 0) { throw "zipalign failed" }

Write-Host "== apksigner =="
& $APKSIGNER sign --ks $KEYSTORE --ks-pass pass:android --ks-key-alias androiddebugkey --out "$SCRIPT_DIR\LUKU-Live.apk" "$OUT\aligned.apk"
if ($LASTEXITCODE -ne 0) { throw "apksigner failed" }

Write-Host "== Verifying signed APK =="
& $APKSIGNER verify "$SCRIPT_DIR\LUKU-Live.apk"
if ($LASTEXITCODE -ne 0) { throw "apksigner verify failed" }

$apk = Get-Item "$SCRIPT_DIR\LUKU-Live.apk"
Write-Host "SUCCESS: Built APK at $($apk.FullName) ($($apk.Length) bytes)"
