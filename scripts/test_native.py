#!/usr/bin/env python3
"""Compile and exercise the real Swift fade callbacks without Bluetooth or keys."""
import pathlib
import subprocess
import tempfile

root = pathlib.Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="cync-native-tests-") as directory:
    source = pathlib.Path(directory) / "main.swift"
    executable = pathlib.Path(directory) / "native-tests"
    source.write_text((root / "native/main.swift").read_text() + "\n" +
                      (root / "tests/native_fade.swift").read_text())
    subprocess.run(["swiftc", "-D", "NATIVE_TESTS", str(source), "-o", str(executable),
                    "-framework", "Cocoa", "-framework", "CoreBluetooth", "-framework", "Security"], check=True)
    subprocess.run([str(executable)], check=True, timeout=15)
