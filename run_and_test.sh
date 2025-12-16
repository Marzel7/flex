#!/bin/bash
# Auto-test and commit Python changes without manual confirmation
# Usage: ./run_and_test.sh script_name.py [args]

set -e

SCRIPT="$1"
shift
ARGS="$@"

if [ -z "$SCRIPT" ]; then
    echo "❌ Usage: ./run_and_test.sh script_name.py [args]"
    exit 1
fi

if [ ! -f "$SCRIPT" ]; then
    echo "❌ File not found: $SCRIPT"
    exit 1
fi

echo "🚀 Running: python $SCRIPT $ARGS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

python "$SCRIPT" $ARGS

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Test completed successfully"

# Auto-commit if there are changes
if git diff --quiet; then
    echo "📝 No changes to commit"
else
    echo "💾 Committing changes..."
    git add "$SCRIPT"
    git commit -m "Auto-update: $SCRIPT" || true
    echo "✅ Changes committed"
fi
