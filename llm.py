import os
import json
import requests
from dotenv import load_dotenv
from prompts import ROLE_PROMPTS

load_dotenv(override=True)

DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY', '')
DEEPSEEK_URL = 'https://api.deepseek.com/chat/completions'
MODEL = 'deepseek-chat'


class StreamResult:
    """Collect streaming text while exposing degraded flag."""
    def __init__(self):
        self.text = ''
        self.degraded = False


def chat_complete(messages, role='companion', temperature=0.7):
    parts = []
    degraded = False
    for chunk, meta in stream_complete_meta(messages, role=role, temperature=temperature):
        parts.append(chunk)
        if meta.get('degraded'):
            degraded = True
    text = ''.join(parts)
    if text:
        return text
    return _fallback(role, messages)


def stream_complete(messages, role='companion', temperature=0.7):
    """Yield text deltas only (backward compatible)."""
    for delta, _meta in stream_complete_meta(messages, role=role, temperature=temperature):
        yield delta


def stream_complete_meta(messages, role='companion', temperature=0.7):
    """Yield (delta, meta) where meta may include degraded=True."""
    system = ROLE_PROMPTS.get(role, ROLE_PROMPTS['companion'])
    payload_messages = [{'role': 'system', 'content': system}] + messages
    if not DEEPSEEK_API_KEY:
        text = _fallback(role, messages)
        for i in range(0, len(text), 8):
            yield text[i:i + 8], {'degraded': True}
        return
    try:
        with requests.post(
            DEEPSEEK_URL,
            headers={
                'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
                'Content-Type': 'application/json',
            },
            json={
                'model': MODEL,
                'messages': payload_messages,
                'temperature': temperature,
                'stream': True,
            },
            stream=True,
            timeout=120,
        ) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                if raw.startswith('data: '):
                    raw = raw[6:]
                if raw.strip() == '[DONE]':
                    break
                try:
                    data = json.loads(raw)
                    delta = data['choices'][0]['delta'].get('content') or ''
                    if delta:
                        yield delta, {'degraded': False}
                except Exception:
                    continue
    except Exception:
        text = _fallback(role, messages)
        for i in range(0, len(text), 8):
            yield text[i:i + 8], {'degraded': True}


def _fallback(role, messages):
    last = ''
    for m in reversed(messages):
        if m.get('role') == 'user':
            last = m.get('content', '')
            break
    snippets = {
        'companion': f'关键词：{last[:60]}。请给三个情绪词、句长偏好、是否要主韵。',
        'examples': __import__('stage_schema').FALLBACK_EXAMPLES_JSON,
        'structure': '体裁：自由诗\n字数序列：7-7-3-3-7-7\n主韵：ang\n禁忌：否定式',
        'symbols': '主体四维：抽象/流变/内在分裂\n测试句：时针的步履在桌沿变沉',
        'verb': '稀薄的余晖沉一寸尘埃\n未命名的一行漫半屏冷光',
        'logic': '稀薄的余晖沉一寸尘埃\n未命名的一行漫半屏冷光\n骤然间\n空阔的回声',
        'status': '情绪偏压抑；张力偏好偏高；下一步锁定主韵。',
        'thought': '升tension：单字动词沉/漫，锁定主韵ang。',
        'canvas': (
            '{"ops":[{"type":"init","intent":"先立四行骨架",'
            '"lines":['
            '{"slots":[{"id":"L0S0","pos":"A"},{"id":"L0S1","pos":"N"},{"id":"L0S2","pos":"V"},{"id":"L0S3","pos":"N"}]},'
            '{"slots":[{"id":"L1S0","pos":"ADV"},{"id":"L1S1","pos":"V"},{"id":"L1S2","pos":"N"}]},'
            '{"slots":[{"id":"L2S0","pos":"N"},{"id":"L2S1","pos":"V"},{"id":"L2S2","pos":"A"},{"id":"L2S3","pos":"N"}]},'
            '{"slots":[{"id":"L3S0","pos":"V"},{"id":"L3S1","pos":"N"},{"id":"L3S2","pos":"N"}]}'
            ']}]}'
        ),
    }
    return snippets.get(role, snippets['companion'])
