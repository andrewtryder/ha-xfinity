#!/usr/bin/env python3
"""Record visible Android accessibility labels for a contributor's review."""

import argparse
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serial", help="ADB device serial (required with multiple devices)"
    )
    parser.add_argument("--output", type=Path, default=Path("xfinity-features.txt"))
    args = parser.parse_args()
    if not shutil.which("adb"):
        parser.error("adb is required; install Android platform tools first")
    adb = ["adb"] + (["-s", args.serial] if args.serial else [])
    print("Navigate to a screen in the signed-in Xfinity app, then name it below.")
    print("Enter a screen name, or leave it blank to finish. No taps are automated.")
    print("Labels can contain private information. Review the output before sharing.")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    sections = []
    while True:
        try:
            name = input("Screen name (blank to finish): ").strip()
        except EOFError:
            break
        if not name:
            break
        try:
            subprocess.run(
                [*adb, "shell", "uiautomator", "dump", "/sdcard/xfinity-discovery.xml"],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            xml = subprocess.check_output(
                [*adb, "shell", "cat", "/sdcard/xfinity-discovery.xml"]
            )
            root = ET.fromstring(xml)
        except (OSError, subprocess.CalledProcessError, ET.ParseError) as exc:
            print(f"Could not read screen: {exc}", file=sys.stderr)
            continue
        finally:
            subprocess.run(
                [*adb, "shell", "rm", "-f", "/sdcard/xfinity-discovery.xml"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        labels = []
        for node in root.iter("node"):
            for attr in ("text", "content-desc"):
                value = node.get(attr, "").strip()
                if value and value not in labels and node.get("password") != "true":
                    labels.append(value)
        sections.append((name, labels))
        print(f"Captured {len(labels)} distinct labels.")
    if sections:
        with args.output.open("x", encoding="utf-8") as output:
            for name, labels in sections:
                output.write(f"## {name}\n")
                output.writelines(f"- {label}\n" for label in labels)
                output.write("\n")
        print(f"Saved {args.output}. Review and redact it before sharing.")
    else:
        print("No screens captured.")


if __name__ == "__main__":
    main()
