"""Text from outside (file names from the sources) made safe to store and to send as JSON."""


def clean_text(text: str) -> str:
    """Join UTF-16 surrogate pairs ("&#55357;&#56832;" unescaped → one emoji) and drop lone
    surrogates — JSON encoding fails on them and one bad file name broke the whole search."""
    if not text:
        return text
    try:
        return text.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeDecodeError:
        return text.encode("utf-8", "replace").decode("utf-8")
