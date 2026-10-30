"""Comparing food names: ignoring case and plurals, and matching whole words only."""


def stem(word: str) -> str:
    """A word without a plural ending, so "tomatoes" and "tomato" match."""
    word = word.lower().strip()
    for ending in ("es", "s"):
        if word.endswith(ending) and len(word) > len(ending) + 2:
            return word[: -len(ending)]
    return word


def _words(text: str) -> list[str]:
    return [stem(w) for w in text.replace("-", " ").split()]


def name_has_term(name: str, term: str) -> bool:
    """True if all the words of `term` appear in order, as whole words, in `name`.

    "peanut" is in "peanut butter" and "kiwis" is in "kiwi", but "pine" is not in "pineapple".
    """
    wanted, found = _words(term), _words(name)
    if not wanted:
        return False
    return any(found[i : i + len(wanted)] == wanted for i in range(len(found) - len(wanted) + 1))
