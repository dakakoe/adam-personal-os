#!/usr/bin/env bash
# Self-gating SetFit retrain (triage iteration). Trains a candidate model to
# $MODEL_DIR.new and promotes it ONLY if held-out accuracy clears the gate —
# a bad training run can never replace a good model. Run weekly by
# memory-mail-ml-retrain.timer; a gate failure exits 1 → OnFailure → Telegram,
# and model.new is LEFT IN PLACE for post-mortem (the next run cleans it).
#
# The flock covers the WHOLE run: training peaks ~3.6GB RSS and a concurrent
# `classify` torch-load could OOM the box. memory-mail-ml.service takes the
# same lock non-blocking and skips its tick while we hold it.
set -euo pipefail

MODEL_DIR="${MAIL_ML_MODEL_DIR:-/srv/memory/data/mail_ml/model}"
GATE="${MAIL_ML_GATE:-0.85}"
PER_CLASS="${MAIL_ML_PER_CLASS:-150}"
VENV="${MAIL_ML_VENV:-/srv/memory/apps/mail_ml/.venv}"
LOCK="$(dirname "$MODEL_DIR")/.mail_ml.lock"
METRICS="$MODEL_DIR.new.metrics.json"

exec 9>"$LOCK"
flock 9

rm -rf "$MODEL_DIR.new"
"$VENV/bin/python" -m mail_ml train \
    --per-class "$PER_CLASS" \
    --model-dir "$MODEL_DIR.new" \
    --eval-user \
    --metrics-json "$METRICS"

ACC=$("$VENV/bin/python" -c "import json; print(json.load(open('$METRICS'))['metrics']['accuracy'])")
USER_ACC=$("$VENV/bin/python" -c "import json; m=json.load(open('$METRICS')); print(f\"{m.get('user_accuracy','n/a')} (n={m.get('user_n',0)})\")")
echo "retrain: held-out acc=$ACC gate=$GATE user_acc=$USER_ACC"

PASS=$("$VENV/bin/python" -c "print(1 if float('$ACC') >= float('$GATE') else 0)")
if [ "$PASS" != "1" ]; then
    echo "retrain: GATE FAILED ($ACC < $GATE) — keeping current model; model.new left for post-mortem"
    exit 1
fi

rm -rf "$MODEL_DIR.prev"
[ -d "$MODEL_DIR" ] && mv "$MODEL_DIR" "$MODEL_DIR.prev"
mv "$MODEL_DIR.new" "$MODEL_DIR"
echo "retrain: PROMOTED (acc=$ACC >= $GATE); previous model at $MODEL_DIR.prev"
