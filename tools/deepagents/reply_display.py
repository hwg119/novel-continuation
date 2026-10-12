"""Extract explicitly separated public text; never guess from language or headings."""
import re


def public_reply(content):
    if isinstance(content, list):
        content = ''.join(part.get('text', '') for part in content
                          if isinstance(part, dict) and part.get('type') in ('text', 'output_text'))
    text = str(content or '')
    # Only explicit reasoning channels are removable. Ordinary prose is preserved.
    text = re.sub(r'<(think|analysis|reasoning)\b[^>]*>.*?</\1\s*>', '', text,
                  flags=re.S | re.I)
    finals = re.findall(r'<final\b[^>]*>(.*?)</final\s*>', text, flags=re.S | re.I)
    if finals:
        return finals[-1].strip()
    # An incomplete reasoning block must not leak its contents.
    text = re.sub(r'<(?:think|analysis|reasoning)\b[^>]*>.*$', '', text, flags=re.S | re.I)
    return text.strip()
