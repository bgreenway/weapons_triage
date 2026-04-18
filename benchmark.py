#!/usr/bin/env python3
"""
Benchmark script for triage endpoint.
Runs all test packages, collects full response data, and generates a report.
"""

import argparse
import json
import glob
import os
import sys
import time
from datetime import datetime

import requests

PACKAGES_DIR = os.path.expanduser("~/qwen_sandbox/triage_packages")


def load_ground_truth(pkg_dir):
    """Extract visibleWeapon from training_example.json."""
    path = os.path.join(pkg_dir, "training_example.json")
    with open(path) as f:
        data = json.load(f)
    assistant_content = data["messages"][2]["content"]
    parsed = json.loads(assistant_content)
    return parsed["visibleWeapon"]


def get_images(pkg_dir):
    """Return (anchor_path, sorted list of crop paths)."""
    anchor = os.path.join(pkg_dir, "anchor.jpg")
    crops = sorted(glob.glob(os.path.join(pkg_dir, "crop_*.jpg")))
    return anchor, crops


def submit_triage(base_url, anchor_path, crop_paths):
    """Submit images to the triage endpoint and return the response."""
    url = f"{base_url}/v1/triage"
    files = [("anchor", ("anchor.jpg", open(anchor_path, "rb"), "image/jpeg"))]
    for cp in crop_paths:
        files.append(("crops", (os.path.basename(cp), open(cp, "rb"), "image/jpeg")))

    resp = requests.post(url, files=files, timeout=60)
    for _, fobj in files:
        fobj[1].close()

    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Benchmark triage accuracy")
    parser.add_argument("--url", default="http://localhost:8001", help="Triage service base URL")
    parser.add_argument("--packages", default=PACKAGES_DIR, help="Path to triage_packages directory")
    parser.add_argument("--output", default=None, help="Output report file (default: benchmark_report_<timestamp>.txt)")
    args = parser.parse_args()

    # Discover packages
    pkg_dirs = sorted([
        d for d in glob.glob(os.path.join(args.packages, "*"))
        if os.path.isdir(d) and os.path.exists(os.path.join(d, "training_example.json"))
    ])

    if not pkg_dirs:
        print(f"No packages found in {args.packages}")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output_file = args.output or f"benchmark_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

    results = []
    tp = tn = fp = fn = 0
    inference_times = []

    print(f"Running {len(pkg_dirs)} test packages...\n")

    for i, pkg_dir in enumerate(pkg_dirs, 1):
        pkg_name = os.path.basename(pkg_dir)
        expected = load_ground_truth(pkg_dir)
        anchor, crops = get_images(pkg_dir)

        print(f"  [{i:2d}/{len(pkg_dirs)}] {pkg_name}...", end=" ", flush=True)

        start = time.time()
        try:
            response = submit_triage(args.url, anchor, crops)
            elapsed = time.time() - start
            predicted = response["visible_weapon"]
            error = response.get("error")
        except Exception as e:
            elapsed = time.time() - start
            response = {}
            predicted = None
            error = str(e)

        inference_ms = response.get("inference_time_ms", 0)
        inference_times.append(inference_ms)

        if predicted is None:
            match = "ERROR"
        elif predicted == expected:
            match = "CORRECT"
            if expected and predicted:
                tp += 1
            else:
                tn += 1
        else:
            match = "WRONG"
            if not expected and predicted:
                fp += 1
            else:
                fn += 1

        print(f"{match} ({inference_ms:.0f}ms)")

        results.append({
            "package": pkg_name,
            "expected": expected,
            "response": response,
            "match": match,
            "round_trip_s": elapsed,
        })

    # Build report
    total = tp + tn + fp + fn
    accuracy = (tp + tn) / total * 100 if total > 0 else 0
    avg_inference = sum(inference_times) / len(inference_times) if inference_times else 0
    min_inference = min(inference_times) if inference_times else 0
    max_inference = max(inference_times) if inference_times else 0
    total_time = sum(r["round_trip_s"] for r in results)

    lines = []
    lines.append("=" * 90)
    lines.append("TRIAGE BENCHMARK REPORT")
    lines.append("=" * 90)
    lines.append(f"Date:       {timestamp}")
    lines.append(f"Endpoint:   {args.url}/v1/triage")
    lines.append(f"Packages:   {len(pkg_dirs)}")
    lines.append(f"Total time: {total_time:.1f}s")
    lines.append("")
    lines.append(f"ACCURACY:   {tp + tn}/{total} ({accuracy:.1f}%)")
    lines.append(f"  True Positives:  {tp:>3}  (weapon correctly detected)")
    lines.append(f"  True Negatives:  {tn:>3}  (clean correctly identified)")
    lines.append(f"  False Positives: {fp:>3}  (clean misidentified as weapon)")
    lines.append(f"  False Negatives: {fn:>3}  (weapon missed)")
    lines.append("")
    lines.append(f"INFERENCE TIME (model only):")
    lines.append(f"  Average: {avg_inference:>8.0f} ms")
    lines.append(f"  Min:     {min_inference:>8.0f} ms")
    lines.append(f"  Max:     {max_inference:>8.0f} ms")
    lines.append("")
    lines.append("=" * 90)
    lines.append("DETAILED RESULTS")
    lines.append("=" * 90)

    for r in results:
        resp = r["response"]
        exp_str = "weapon" if r["expected"] else "clean"
        pred_str = "weapon" if resp.get("visible_weapon") else "clean"

        lines.append("")
        lines.append(f"--- {r['package']} ---")
        lines.append(f"  Expected:           {exp_str}")
        lines.append(f"  Predicted:          {pred_str}")
        lines.append(f"  Result:             {r['match']}")
        lines.append(f"  Weapon types:       {resp.get('weapon_types', [])}")
        lines.append(f"  Confidence:         {resp.get('confidence', 'N/A')}")
        lines.append(f"  Law enforcement:    {resp.get('law_enforcement', 'N/A')}")
        lines.append(f"  Environment:        {resp.get('environment_type', 'N/A')}")
        lines.append(f"  Person description: {resp.get('person_description', 'N/A')}")
        lines.append(f"  Should alert:       {resp.get('should_alert', 'N/A')}")
        lines.append(f"  Images used:        {resp.get('images_used', 'N/A')}")
        lines.append(f"  Inference time:     {resp.get('inference_time_ms', 0):.0f} ms")
        lines.append(f"  Round-trip time:    {r['round_trip_s']:.1f}s")
        if resp.get("error"):
            lines.append(f"  Error:              {resp['error']}")

    report = "\n".join(lines) + "\n"

    with open(output_file, "w") as f:
        f.write(report)

    print(f"\n{report}")
    print(f"Report saved to: {output_file}")


if __name__ == "__main__":
    main()
