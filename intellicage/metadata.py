from __future__ import annotations

import json
from pathlib import Path

import polars as pl

METADATA_FILE = 'intellicage.json'
LABELS = ('group', 'lot', 'phase')


def folder_labels(folder: Path) -> dict:
    path = folder / METADATA_FILE
    if not path.is_file():
        return dict.fromkeys(LABELS, '')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f'{folder.name}: invalid {METADATA_FILE}') from exc
    if not isinstance(data, dict) or any(not isinstance(data.get(key, ''), str) for key in LABELS):
        raise ValueError(f'{folder.name}: metadata labels must be text')
    return {key: data.get(key, '').strip() for key in LABELS}


def resolve_labels(spec: dict) -> dict:
    # project labels take precedence over the source folder
    labels = folder_labels(Path(spec['path']).expanduser()) if any(key not in spec for key in LABELS) else {}
    result = {**labels, **spec}
    for key in LABELS:
        value = result.get(key, '')
        if not isinstance(value, str):
            raise ValueError(f'{key} must be text')
        result[key] = value.strip()
    return result


def validate_labels(spec: dict):
    if not spec.get('group', '').strip() or not spec.get('phase', '').strip():
        raise ValueError('Group and phase names are required; enter dataset labels or add intellicage.json to the export folder')


def group_hint(folder: Path) -> str:
    animals = pl.read_csv(folder / 'Animal.txt', separator='\t', infer_schema_length=0)
    if 'Group' not in animals.columns:
        return ''
    groups = animals['Group'].drop_nulls().str.strip_chars().unique().to_list()
    return groups[0] if len(groups) == 1 else ''
