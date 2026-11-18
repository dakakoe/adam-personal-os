# mail_ml — SetFit content classifier (Mail auto-triage Phase C)

Classifies ingested Gmail into `newsletter | transactional | personal` with a
SetFit model trained on weak-supervision labels (Gmail categories + keyword rules,
see `mail_ml/labeling.py`). merge_api never loads torch — it reads
`memory.mail_class` and surfaces a content badge/filter in `/mail`.

## Deploy (on the droplet `memory`)

1. rsync the package → `/srv/memory/apps/mail_ml/` (exclude `.venv`).
2. Build its venv (torch is large — use the CPU wheel index):
   ```
   cd /srv/memory/apps/mail_ml
   ~/.local/bin/uv sync --extra-index-url https://download.pytorch.org/whl/cpu
   ```
3. Apply migration `20260701120000_mail_class.sql` (`scripts/dbmate.sh up`).
4. Train once (a few minutes on CPU; writes the artifact):
   ```
   .venv/bin/python -m mail_ml train --sample 8000 --per-class 200
   ```
   Review the printed held-out accuracy vs the weak labels before trusting it.
5. Backfill classify the corpus (repeat until it reports 0):
   ```
   .venv/bin/python -m mail_ml classify --limit 2000
   ```
6. Install the timer (classifies new mail every 30 min):
   ```
   sudo cp deploy/mail_ml/memory-mail-ml.{service,timer} /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now memory-mail-ml.timer
   ```

## Retraining (weekly, self-gating — triage iteration)

`memory-mail-ml-retrain.timer` runs `retrain.sh` every Sunday 03:00:
- trains a candidate to `model.new` (`--per-class 150 --eval-user
  --metrics-json`), holding a flock the classify/refine ticks respect
  (they skip silently while training runs — RAM peaks ~3.6GB);
- promotes (`model` → `model.prev`, `model.new` → `model`) ONLY if held-out
  accuracy ≥ `MAIL_ML_GATE` (default 0.85; current model is 0.887);
- a gate failure exits 1 → OnFailure → Telegram, and leaves `model.new`
  on disk for post-mortem. Disk peak ≈ 3× artifact (~400MB) — fine.
- `user_accuracy`/`user_n` (accuracy on user-corrected rows,
  `mail_class.model_version='user'`) is REPORTED in the metrics JSON and log
  but never gated — revisit once n ≥ 100.
- Memory (Nov 2026): the training working set is ~0.8GB (same versions on macOS)
  but the droplet process holds 2.3–2.9GB RSS, and only ~2.3GB is free at 03:00
  — Oct 4 → Nov 1 all died in a global OOM (exit 137). **The host needs swap**
  for the run to fit (idle pages of the resident services get paged out):
  ```
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile
  sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf && sudo sysctl --system
  ```
  The unit also sets `OOMScoreAdjust=500` (if it still doesn't fit, the retrain is
  the OOM victim, not Postgres) and `MALLOC_ARENA_MAX=2` (unproven — a 1.5GB-capped
  probe still OOMed with it). The 03:00 Postgres backup overlaps but costs ~50MB —
  not the cause. Batch size / seq length barely move the peak.

Install:
```
sudo cp deploy/mail_ml/memory-mail-ml-retrain.{service,timer} /etc/systemd/system/
sudo cp deploy/mail_ml/memory-mail-ml.service /etc/systemd/system/   # flock wrap
sudo systemctl daemon-reload && sudo systemctl enable --now memory-mail-ml-retrain.timer
# + the OnFailure drop-in for the new unit (deploy/alerting/README.md loop)
```

Gate test without waiting a week: `sudo systemctl set-environment MAIL_ML_GATE=0.99`
(or edit + daemon-reload), start the service → must exit 1 + alert, model untouched.

Manual full re-classify after a promoted model (user corrections survive):
```
DELETE FROM memory.mail_class WHERE model_version <> 'user';
-- then let the 30-min timer drain (~2000/run), or loop classify manually
```

User corrections (`model_version='user'`, set via the badge menu in /mail) are
ground truth: `upsert_classes`/`mark_refine_visited` never overwrite them.
Rule-tuning feed: `scripts/mail_class_corrections.sql`.

## Notes
- Weak labels lean on Gmail's category (`promotions→newsletter`,
  `updates→transactional`, `personal→personal`) with keyword/​header refinements
  and a conflict-abstain guard. The trained model generalizes to mail the rules
  abstain on. Accuracy is measured vs the (noisy) weak labels — treat as a
  baseline and iterate.
- Ollama tier (LLM for low-confidence cases) SHIPPED with Personal OS Phase 5 —
  `refine` runs on the same timer (qwen2.5:3b at 127.0.0.1:11434).
