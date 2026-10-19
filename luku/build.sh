#!/usr/bin/env bash
# Builds LUKU Live without Gradle: aapt2 -> javac -> dx -> zip -> zipalign+sign.
set -euo pipefail
T=/home/claude/tools
P=/home/claude/luku
ANDROID_JAR=$T/sable/android-34/android.jar
AAPT2=$T/prebuilt/linux/aapt2_64
OUT=$P/build
rm -rf "$OUT" && mkdir -p "$OUT"/{compiled,gen,classes,dex}

echo "== aapt2 compile/link"
"$AAPT2" compile --dir "$P/res" -o "$OUT/compiled/res.zip"
"$AAPT2" link -I "$ANDROID_JAR" --manifest "$P/AndroidManifest.xml" \
  --min-sdk-version 26 --target-sdk-version 34 --version-code 10 --version-name 6.3 \
  --java "$OUT/gen" -o "$OUT/base.apk" "$OUT/compiled/res.zip"

echo "== javac"
find "$P/src" "$OUT/gen" -name '*.java' > "$OUT/sources.txt"
javac -nowarn -Xlint:-options -source 8 -target 8 -encoding UTF-8 \
  -bootclasspath "$ANDROID_JAR" -classpath "$ANDROID_JAR" \
  -d "$OUT/classes" @"$OUT/sources.txt"

echo "== dx"
java -cp "$T/dx.jar" com.android.dx.command.Main --dex --min-sdk-version=26 \
  --output="$OUT/dex/classes.dex" "$OUT/classes"

echo "== package"
cp "$OUT/base.apk" "$OUT/unsigned.apk"
(cd "$OUT/dex" && zip -q -j "$OUT/unsigned.apk" classes.dex)


echo "== align + sign"
java -jar "$T/uber-signer.jar" -a "$OUT/unsigned.apk" -o "$OUT/signed" --allowResign >"$OUT/sign.log" 2>&1 || { cat "$OUT/sign.log"; exit 1; }
APK=$(ls "$OUT"/signed/*.apk | head -1)
cp "$APK" "$P/LUKU-Live.apk"
tail -5 "$OUT/sign.log"
ls -la "$P/LUKU-Live.apk"
