"""Build / refresh style cards from 诗.docx into style_corpus/user_poems.json."""
from __future__ import annotations

import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCX = ROOT / '诗.docx'
OUT = Path(__file__).resolve().parent / 'user_poems.json'
W_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def docx_paragraphs(path: Path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('word/document.xml'))
    paras = []
    for p in root.iter(f'{W_NS}p'):
        texts = [t.text or '' for t in p.iter(f'{W_NS}t')]
        line = ''.join(texts).strip()
        if line:
            paras.append(line)
    return paras


def split_poems(paras):
    text = '\n'.join(paras)
    # Poems often start with a digit glued to first char: 1我 / 2迷茫
    parts = re.split(r'(?=\d{1,2}[\u4e00-\u9fffA-Za-z])', text)
    poems = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r'^(\d{1,2})([\s\S]+)$', part)
        if not m:
            continue
        body = m.group(2).strip()
        # strip trailing next-number bleed handled by split
        poems.append({'id': f'user_{int(m.group(1)):02d}', 'text': body})
    return poems


def analyze(poem: dict) -> dict:
    text = poem['text']
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) <= 1:
        # some poems are single-paragraph with commas
        lines = [x.strip() for x in re.split(r'[，。；]', text) if x.strip()]
    seq = [len(re.findall(r'[\u4e00-\u9fff]', ln)) or len(ln) for ln in lines]
    q = len(re.findall(r'[？?]', text))
    bang = len(re.findall(r'[！!]', text))
    light = len(re.findall(r'月|星|光|阳|曙|夕|暗|黑|影', text))
    time_w = len(re.findall(r'时间|时光|昨日|明日|永恒|回忆', text))
    return {
        **poem,
        'line_count': len(lines),
        'breath': '-'.join(str(x) for x in seq[:20]),
        'questions': q,
        'imperatives': bang,
        'light_dark_hits': light,
        'time_hits': time_w,
        'has_not_but': bool(re.search(r'不是.+而是', text)),
        'has_itself': '本身' in text,
    }


def main():
    if not DOCX.exists():
        raise SystemExit(f'missing {DOCX}')
    paras = docx_paragraphs(DOCX)
    poems = [analyze(p) for p in split_poems(paras)]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'source': '诗.docx',
        'count': len(poems),
        'poems': poems,
        'aggregate': {
            'avg_questions': round(sum(p['questions'] for p in poems) / max(1, len(poems)), 2),
            'avg_imperatives': round(sum(p['imperatives'] for p in poems) / max(1, len(poems)), 2),
            'not_but_rate': sum(1 for p in poems if p['has_not_but']) / max(1, len(poems)),
            'itself_rate': sum(1 for p in poems if p['has_itself']) / max(1, len(poems)),
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'wrote {OUT} poems={len(poems)}')


if __name__ == '__main__':
    main()
