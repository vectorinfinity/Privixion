import re
import uuid
import regex

# Aadhaar Verhoeff checksum, PAN syntax/context, Luhn for payment card candidates.
_D = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]]
_P = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]


def digits(value):
    return re.sub(r'\D', '', value)


def aadhaar(value):
    value = digits(value)
    if len(value) != 12 or value[0] in '01':
        return False
    checksum = 0
    for i, digit in enumerate(reversed(value)):
        checksum = _D[checksum][_P[i % 8][int(digit)]]
    return checksum == 0


def luhn(value):
    value = digits(value)
    if not 13 <= len(value) <= 19 or len(set(value)) == 1:
        return False
    total = 0
    for i, digit in enumerate(reversed(value)):
        n = int(digit) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


PATTERNS = [
    ('EMAIL', r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', None, None),
    ('IN_PHONE', r'(?<!\d)(?:\+91[ -]?)?[6-9]\d{4}[ -]?\d{5}(?!\d)', None, None),
    ('IN_AADHAAR', r'(?<!\d)[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?!\d)', aadhaar, None),
    ('IN_PAN', r'\b[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z]\b', None, None),
    ('IN_IFSC', r'\b[A-Z]{4}0[A-Z0-9]{6}\b', None, None),
    ('IN_PASSPORT', r'\b[A-PR-WY][1-9]\d{6}\b', None, r'passport'),
    ('BANK_ACCOUNT', r'(?<!\d)\d{9,18}(?!\d)', None, r'account|a/c|bank'),
    ('CREDIT_CARD', r'(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)', luhn, None),
]
_analyzer = None


def load_analyzer():
    global _analyzer
    if _analyzer is None:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        provider = NlpEngineProvider(nlp_configuration={'nlp_engine_name': 'spacy', 'models': [{'lang_code': 'en', 'model_name': 'en_core_web_sm'}]})
        _analyzer = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=['en'])
    return _analyzer


def matches(text, custom_rules=(), use_nlp=True):
    output = []
    for category, pattern, validator, context in PATTERNS:
        for match in re.finditer(pattern, text, re.I):
            if validator and not validator(match.group()):
                continue
            if context and not re.search(context, text[max(0, match.start()-60):match.start()], re.I):
                continue
            output.append((match.start(), match.end(), category, .95 if validator else .8))
    for rule in custom_rules:
        # The API only permits a bounded regex subset; no quantifier nesting or lookaround.
        for match in regex.finditer(rule['pattern'], text, regex.I, timeout=.1):
            if match.end() > match.start():
                output.append((match.start(), match.end(), rule['category'], .8))
    if use_nlp:
        # Built-in email regex runs above. Presidio's email validator can refresh
        # public suffix data over HTTP; exclude it to guarantee local detection.
        for found in load_analyzer().analyze(text=text, language='en', entities=['PERSON', 'LOCATION', 'PHONE_NUMBER'], score_threshold=.45):
            category = {'PERSON': 'NAME', 'LOCATION': 'ADDRESS', 'EMAIL_ADDRESS': 'EMAIL', 'PHONE_NUMBER': 'PHONE'}.get(found.entity_type, found.entity_type)
            if category == 'PHONE' and any(c == 'IN_PHONE' and a < found.end and b > found.start for a,b,c,_ in output):
                continue
            output.append((found.start, found.end, category, found.score))
    deduplicated = {}
    for start,end,category,score in output:
        key = (start,end,category)
        deduplicated[key] = max(score, deduplicated.get(key, 0))
    return [(a,b,c,score) for (a,b,c),score in deduplicated.items()]


def findings(page, custom_rules=(), use_nlp=True):
    words = page['words']
    parts, offsets, cursor = [], [], 0
    for word in words:
        parts.append(word['text'])
        offsets.append((cursor, cursor + len(word['text']), word['rect']))
        cursor += len(word['text']) + 1
    text = ' '.join(parts)
    output = []
    for start, end, category, confidence in matches(text, custom_rules, use_nlp):
        rects = [r for a, b, r in offsets if a < end and b > start]
        if rects:
            output.append({'id': str(uuid.uuid4()), 'page': page['index'], 'rects': rects,
                           'category': category, 'confidence': round(confidence, 3), 'text': text[start:end]})
    return output


def validate_pattern(pattern):
    if not pattern or len(pattern) > 200:
        raise ValueError('Patterns must contain 1–200 characters')
    # Deliberately limited regular expressions prevent catastrophic backtracking.
    if any(c in pattern for c in '()*+|') or re.search(r'\\[1-9]', pattern):
        raise ValueError('Use literals, character classes, ?, and bounded {n,m} repetition; groups, *, + and alternation are unsupported')
    for quantifier in re.finditer(r'\{(\d+)(?:,(\d+))?\}', pattern):
        if max(int(quantifier[1]), int(quantifier[2] or quantifier[1])) > 100:
            raise ValueError('Maximum repetition is 100')
    if re.search(r'\{\d+,\}', pattern):
        raise ValueError('Repetition must have a finite upper bound')
    re.compile(pattern)
