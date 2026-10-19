"""Attach document citations only to answers supported by document evidence."""
import re

_DOCUMENT_QUERY = re.compile(r'\b(?:policy|policies|procedure|runbook|document|manual|handbook|SOP|ticket|incident|time\s*off|office\s+hours|working\s+hours|timezone|time\s+zone)\b', re.I)
_LIVE_TIME = re.compile(r'\b(?:(?:what(?:\s+is|.s)?|tell\s+me)\s+(?:the\s+)?(?:current\s+|local\s+)?time(?:\s+(?:in|at)\s+[^?.!]+|\s+(?:now|right\s+now))?\s*[?.!]*$|what\s+time\s+is\s+it\b|current\s+time\s+(?:in|at)\b)', re.I)
_LIVE_WEATHER = re.compile(r'\b(?:(?:what(?:\s+is|.s)?|how(?:\s+is|.s)?|tell\s+me)\s+(?:the\s+)?(?:current\s+)?weather\b|(?:weather|temperature)\s+(?:in|at)\b)', re.I)
_NO_EVIDENCE = re.compile(
    r'^(?:[-*\s_]*)(?:(?:i(?:.m|\s+am)\s+sorry[,.:]?\s*(?:but\s+)?)|'
    r'(?:based\s+on|according\s+to)\s+(?:the\s+)?(?:available|provided)\s+(?:context|documentation|documents)[,.:]?\s*)?'
    r'(?:there\s+is\s+no\s+(?:specific\s+|relevant\s+|sufficient\s+)?(?:information|documentation|evidence)|'
    r'(?:the\s+)?(?:provided\s+|available\s+)?(?:context|documentation|documents|knowledge\s+base)\s+'
    r'(?:does\s+not|doesn.t|do\s+not|don.t)\s+(?:contain|include|provide|cover)|'
    r'i\s+(?:couldn.t|cannot|can.t|could\s+not|didn.t|did\s+not)\s+find\s+'
    r'(?:(?:enough|any|sufficient|relevant|confident)\s+)*(?:information|evidence|documentation|details|matches)|'
    r'i\s+(?:don.t|do\s+not)\s+have\s+(?:enough|sufficient|any)\s+(?:information|context|evidence)|'
    r'there\s+(?:isn.t|is\s+not)\s+(?:enough|sufficient)\s+(?:information|context|evidence)|'
    r'i\s+found\s+some\s+partial\s+matches\s+but\s+need\s+more\s+context)', re.I)
_MARKER = re.compile(r'\[Source\s+\d+(?:\s*[,;]\s*(?:Source\s+)?\d+)*\]', re.I)
_SOURCE_LINE = re.compile(r'^\s*(?:[📎*_-]\s*)*(?:sources?|references?)\s*(?:\*\*)?\s*:', re.I)


def non_document_reply(query):
    # Explicit document questions still need retrieval, even if they discuss
    # time or weather (for example, a severe-weather work-from-home policy).
    if _DOCUMENT_QUERY.search(query):
        return None
    if _LIVE_TIME.search(query):
        return "I don't have access to a live clock in this chat. Please check your device's world clock for the current local time."
    if _LIVE_WEATHER.search(query):
        return "I don't have live weather data in this chat. Please check a weather service for current conditions."
    return None


def strip_citations(answer):
    lines = [line for line in answer.splitlines() if not _SOURCE_LINE.search(line)]
    return re.sub(r'[ \t]+\n', '\n', _MARKER.sub('', '\n'.join(lines))).strip()


def apply_citation_policy(payload, query=None):
    """Works on API responses and saved messages without changing grounded answers."""
    result = dict(payload)
    answer = result.get('answer') or ''
    live_reply = non_document_reply(query) if query else None
    status = ('not_applicable' if live_reply else 'unsupported'
              if result.get('clarification_needed') or _NO_EVIDENCE.search(answer) else None)
    if status is None:
        return result
    result.update(answer=live_reply or strip_citations(answer), citations=[],
        citation_status=status, confidence=0, confidence_label='NO_SOURCES',
        confidence_breakdown={}, chunks_used=0, low_confidence=True,
        retrieval_transparency=[], ticket_candidates=[])
    if result.get('clarification_question'):
        result['clarification_question'] = strip_citations(result['clarification_question'])
    return result
