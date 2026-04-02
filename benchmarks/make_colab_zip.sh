#!/usr/bin/env bash
# make_colab_zip.sh — package ATTEST source for Google Colab upload
#
# Usage (run from 4_implementation/):
#   sh benchmarks/make_colab_zip.sh
#
# Output:
#   4_implementation/attest_src.zip   (~20–30 KB)
#
# The zip contains attest/ and compass/ — the two Python packages that
# attest_benchmark.ipynb imports.  Upload it when the Colab setup cell
# prompts you, and it will be extracted automatically.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # 4_implementation/
ZIP_OUT="$SRC_DIR/attest_src.zip"

cd "$SRC_DIR"

echo "Packaging from: $SRC_DIR"
echo "Output:         $ZIP_OUT"

rm -f "$ZIP_OUT"

zip -r "$ZIP_OUT" attest compass \
    --exclude "**/__pycache__/*" \
    --exclude "**/*.pyc"

SIZE=$(du -sh "$ZIP_OUT" | cut -f1)
echo "Done: $ZIP_OUT  ($SIZE)"
echo
echo "Next steps:"
echo "  1. Open attest_benchmark.ipynb in Google Colab"
echo "  2. Run the setup cell — it will prompt for a file upload"
echo "  3. Upload $ZIP_OUT"
echo "  4. Run all remaining cells"
