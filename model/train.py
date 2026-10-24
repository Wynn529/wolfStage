"""Train only on the explicit training file; group CV fits TF-IDF inside each fold."""
from __future__ import annotations
import argparse
import json
import platform
import sys
import time
import warnings
from collections import Counter
from pathlib import Path
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import joblib
import numpy as np
import scipy
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import f1_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC
from sklearn.exceptions import ConvergenceWarning
from model.common import ROOT, LABELS, SEED, MODEL_VERSION, SCHEMA_VERSION, RULE_VERSION, file_hash, load_rows, write_json


def pipeline(ngram, c):
    return Pipeline([
        ('tfidf', TfidfVectorizer(analyzer='char', ngram_range=ngram, min_df=1, max_features=50000, sublinear_tf=True, lowercase=False)),
        ('classifier', LinearSVC(C=c, dual=True, random_state=SEED, max_iter=10000)),
    ])


def train(train_path, output):
    started = time.perf_counter()
    rows = load_rows(train_path)
    if any(r.get('dataset_usage') not in (None, '', 'train_candidate') for r in rows):
        raise ValueError('Training input contains reserved test/probe records')
    if set(r['intent'] for r in rows) != set(LABELS):
        raise ValueError('All six classes required for this model version')
    texts = np.array([r['public_text'] for r in rows])
    labels = np.array([r['intent'] for r in rows])
    groups = np.array([r['source_group'] for r in rows])
    splitter = StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=SEED)
    splits = list(splitter.split(texts, labels, groups))
    fold_manifest = []
    for k, (a, b) in enumerate(splits):
        assert not set(groups[a]) & set(groups[b])
        assert set(labels[a]) == set(labels[b]) == set(LABELS)
        fold_manifest.append({'fold': k, 'fit_ids': [rows[i]['sample_id'] for i in a],
                              'validation_ids': [rows[i]['sample_id'] for i in b],
                              'fit_class_counts': dict(Counter(labels[a])), 'validation_class_counts': dict(Counter(labels[b]))})
    trials = []
    with warnings.catch_warnings():
        warnings.simplefilter('error', ConvergenceWarning)
        for ng in [(1, 3), (2, 4)]:
            for c in [0.5, 1.0, 2.0]:
                scores = []
                for a, b in splits:
                    estimator = pipeline(ng, c)
                    estimator.fit(texts[a], labels[a])
                    scores.append(float(f1_score(labels[b], estimator.predict(texts[b]), labels=LABELS, average='macro', zero_division=0)))
                trials.append({'ngram_range': list(ng), 'C': c, 'macro_f1_by_fold': scores, 'macro_f1_mean': float(np.mean(scores)), 'macro_f1_std': float(np.std(scores))})
        best = max(trials, key=lambda r: r['macro_f1_mean'])  # stable first candidate wins exact ties
        estimator = pipeline(tuple(best['ngram_range']), best['C'])
        estimator.fit(texts, labels)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    versions = {m.__name__: m.__version__ for m in [numpy_module for numpy_module in [np, scipy, sklearn, joblib]]}
    manifest = {
        'model_version': MODEL_VERSION, 'schema_version': SCHEMA_VERSION, 'rule_version': RULE_VERSION,
        'data_version': 'data-approved-v0.3', 'training_file': str(Path(train_path).resolve().relative_to(ROOT)) if Path(train_path).resolve().is_relative_to(ROOT) else str(Path(train_path).resolve()),
        'training_sha256': file_hash(train_path), 'training_count': len(rows), 'class_counts': dict(Counter(labels)),
        'fit_feature_columns': ['public_text'], 'excluded_feature_columns': ['sample_id','intent','source_group','source_type','speaker_id','entities','review_fields'],
        'group_count': len(set(groups)), 'cv': '3-fold StratifiedGroupKFold; vectorizer fit separately inside each fold',
        'random_seed': SEED, 'selected_parameters': {'ngram_range': best['ngram_range'], 'C':best['C'], 'min_df':1, 'max_features':50000, 'sublinear_tf':True, 'lowercase':False},
        'cv_macro_f1_mean': best['macro_f1_mean'], 'selection_data': 'training-file group CV only',
        'feature_count': len(estimator.named_steps['tfidf'].vocabulary_),
        'classes': estimator.classes_.tolist(), 'confidence_type':'not_calibrated',
        'score_type':'linear_svc_ovr_decision_function', 'python': platform.python_version(), 'dependencies': versions,
        'model_path': output.name, 'training_elapsed_seconds': time.perf_counter()-started,
        'source_test_author_independence':False, 'real_game_validation_count':0,
    }
    joblib.dump({'pipeline': estimator, 'manifest': manifest}, output, compress=3)
    manifest['model_sha256'] = file_hash(output)
    write_json(output.parent/'model_manifest.json', manifest)
    write_json(output.parent/'training_cv.json', {'candidate_grid_predeclared':True, 'trials':trials, 'folds':fold_manifest, 'selected':best})
    print(json.dumps({'model_version':MODEL_VERSION, 'training_count':len(rows), 'selected':best, 'feature_count':manifest['feature_count']}, ensure_ascii=True))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--train', type=Path, default=ROOT/'data/train.csv')
    parser.add_argument('--output', type=Path, default=ROOT/'model/classifier.joblib')
    args = parser.parse_args()
    train(args.train, args.output)
