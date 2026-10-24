from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = ['role_claim', 'check_claim', 'vote_intent', 'discussion', 'low_info', 'suspected_violation']
SEED = 20261024
MODEL_VERSION = 'small-intent-v0.1'
SCHEMA_VERSION = 'small-intent-schema-v0.3'
RULE_VERSION = 'entity-rules-v0.1'
ARRAY_FIELDS = ['player_ids', 'check_claims', 'excluded_vote_targets', 'advisory_flags',
                'agreement_targets', 'suspect_targets', 'support_targets',
                'withdrawn_vote_targets', 'conditional_vote_alternatives']
INT_FIELDS = ['speaker_id', 'check_target', 'vote_target', 'follow_target',
              'agreement_target', 'suspect_target', 'support_target']


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def write_csv(path, records, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in records:
            writer.writerow({k: json.dumps(row[k], ensure_ascii=False) if isinstance(row.get(k), (list, dict)) else row.get(k) for k in fields})


def load_rows(path):
    path = Path(path)
    if path.suffix == '.json':
        rows = json.loads(path.read_text(encoding='utf-8'))
    else:
        with path.open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            for field in ARRAY_FIELDS:
                if field in row:
                    row[field] = json.loads(row[field] or '[]')
            for field in INT_FIELDS:
                if field in row:
                    row[field] = int(row[field]) if row[field] else None
            for field in ['claimed_role', 'check_result', 'vote_condition']:
                if field in row and not row[field]:
                    row[field] = None
    seen = set()
    for row in rows:
        if row['sample_id'] in seen or not row['public_text'].strip() or row['intent'] not in LABELS:
            raise ValueError(f"Invalid record {row.get('sample_id')}")
        if not row.get('source_group'):
            raise ValueError('Every sample requires source_group')
        seen.add(row['sample_id'])
    return rows

