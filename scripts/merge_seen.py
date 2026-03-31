"""Merge local seen.json with remote origin/main:seen.json to avoid git conflicts."""
import json
import subprocess

remote_json = subprocess.check_output(["git", "show", "origin/main:seen.json"])
remote = set(json.loads(remote_json))

with open("seen.json") as f:
    local = set(json.load(f))

merged = sorted(local | remote)

with open("seen.json", "w") as f:
    json.dump(merged, f, indent=2)

print(f"Merged seen.json: {len(merged)} URLs ({len(local - remote)} new this run)")
