import re


ENTITY_PATTERNS = [
    re.compile(r"\b(?:AB|AS|Oy|A/S)\b", flags=re.IGNORECASE),
    re.compile(r"\b(?:holding|ventures|capital|partners|foundation)\b", flags=re.IGNORECASE),
]

NAME_PATTERN = re.compile(
    r"\b([A-Z][A-Za-z0-9&\.-]{1,30}\s(?:AB|AS|Oy|A/S|Holding|Ventures|Capital|Partners))\b"
)


def extract_related_entities(note_text):
    """
    Regex-based placeholder for related-party entity extraction from note text.
    Returns a dict for easy extension to NER later.
    """
    entities = []
    for match in NAME_PATTERN.finditer(note_text):
        entities.append(match.group(1).strip())

    keyword_hits = []
    for pat in ENTITY_PATTERNS:
        found = pat.findall(note_text)
        keyword_hits.extend(found)

    unique_entities = sorted(set(entities))
    unique_keywords = sorted(set(k.lower() for k in keyword_hits))

    return {
        "entities": unique_entities,
        "keyword_hits": unique_keywords,
        "entity_count": len(unique_entities)
    }


if __name__ == "__main__":
    sample = "Transactions include NordBridge Holding AB and Fjord Capital Partners AS."
    print(extract_related_entities(sample))
