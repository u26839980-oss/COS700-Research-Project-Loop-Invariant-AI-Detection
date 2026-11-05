"""Read-only evidence browsing; historical cohorts are never silently aggregated."""
import json
from pathlib import Path

import pandas as pd
import streamlit as st


def evidence_files(root, index_path=None):
    root = Path(root).resolve()
    if not root.is_dir():
        return []
    if index_path is not None and Path(index_path).is_file():
        index_path = Path(index_path)
        base = index_path.parent.resolve()
        candidates = []
        for name in json.loads(index_path.read_text(encoding='utf-8')):
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Invalid historical evidence index path')
            candidate = base / relative
            if candidate.is_relative_to(root) and candidate.suffix.lower() in {'.csv', '.json', '.jsonl', '.txt', '.log', '.md'}:
                candidates.append(candidate)
        return sorted(candidates)
    return sorted(path for path in root.rglob('*') if path.is_file()
                  and path.resolve().is_relative_to(root)
                  and path.suffix.lower() in {'.csv', '.json', '.jsonl', '.txt', '.log', '.md'})


def render_history(root):
    root = Path(root)
    st.subheader('Historical research evidence')
    st.caption('Original runs and post-hoc analyses are separate. Read-only previews; no combined success score.')
    categories = {'Saved runs, prompts and traces': root / 'results',
                  'Post-hoc analyses': root / 'artifacts/diagnostics',
                  'Frozen pilot comparison': root / 'artifacts/poster',
                  'Report evidence and reference comparisons': root / 'report_evidence'}
    category = st.selectbox('Evidence collection', list(categories))
    directory = categories[category]
    paths = evidence_files(directory, root / 'evidence_index.json')
    query = st.text_input('Filter evidence paths').strip().lower()
    paths = [path for path in paths if query in path.relative_to(directory).as_posix().lower()]
    if not paths:
        st.info('No matching historical files. Import the separate results package and enable the history override.')
        return
    selected = st.selectbox('Evidence file', paths, format_func=lambda p: p.relative_to(directory).as_posix())
    if not selected.resolve().is_relative_to(directory.resolve()):
        st.error('Historical evidence path escapes its collection.')
        return
    st.caption(f'{selected.stat().st_size:,} bytes | Preview limited to 200 rows or 200,000 characters.')
    try:
        if selected.suffix == '.csv':
            st.dataframe(pd.read_csv(selected, nrows=200).fillna(''), hide_index=True)
        elif selected.suffix == '.json' and selected.stat().st_size <= 2_000_000:
            st.json(json.loads(selected.read_text(encoding='utf-8')))
        else:
            with selected.open(encoding='utf-8', errors='replace') as stream:
                if selected.suffix == '.jsonl':
                    rows = []
                    for _, line in zip(range(200), stream):
                        if line.strip():
                            rows.append(json.loads(line))
                    st.json(rows)
                else:
                    st.code(stream.read(200_000), language=None)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        st.error('Could not preview saved evidence: ' + str(exc))
