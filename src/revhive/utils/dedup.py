"""Shared deduplication and sorting utilities for review findings."""

from __future__ import annotations

import re

from revhive.agents.base import SEVERITY_ORDER, ReviewFinding

# Common English stop words for keyword extraction
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "to", "of",
    "in", "for", "on", "with", "at", "by", "from", "as", "into", "through",
    "during", "before", "after", "above", "below", "between", "and", "but",
    "or", "nor", "not", "so", "yet", "both", "either", "neither", "each",
    "every", "all", "any", "few", "more", "most", "other", "some", "such",
    "no", "only", "own", "same", "than", "too", "very", "just", "that",
    "this", "these", "those", "it", "its", "they", "them", "their", "we",
    "our", "you", "your", "he", "him", "his", "she", "her",
})


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text, removing stop words."""
    words = re.findall(r"[a-z0-9_]+", text.lower())
    return {w for w in words if w not in _STOP_WORDS and len(w) > 2}


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Compute Jaccard similarity between two keyword sets."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _title_similarity(title_a: str, title_b: str) -> float:
    """Compute title similarity using Jaccard on keywords.

    Returns 1.0 if titles match exactly (case-insensitive), otherwise
    falls back to Jaccard on title keywords.
    """
    if title_a.strip().lower() == title_b.strip().lower():
        return 1.0
    kw_a = _extract_keywords(title_a)
    kw_b = _extract_keywords(title_b)
    return _jaccard_similarity(kw_a, kw_b)


def deduplicate_and_sort(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Remove duplicate findings and sort by severity.

    Four-layer dedup:
    1. Same file+line: keep highest severity, merge descriptions
    2. Exact title match (case-insensitive)
    3. Title similarity: title Jaccard ≥ 0.8 → direct duplicate
    4. Semantic similarity: Jaccard on title+description keywords.
       For short texts (< 5 keywords), uses a stricter threshold (0.8).
       Otherwise uses 0.5 with title overlap requirement (0.3).

    When duplicates are found, the higher-severity one is kept and
    descriptions are merged.  Each kept finding gets a
    ``matched_existing`` field (True if it was deduplicated against
    an existing finding, False otherwise).
    """
    if not findings:
        return []

    # Layer 1: file+line dedup (same location = same issue)
    location_map: dict[str, int] = {}
    for i, f in enumerate(findings):
        if f.file_path and f.line_number:
            key = f"{f.file_path}:{f.line_number}"
            if key in location_map:
                existing_idx = location_map[key]
                existing = findings[existing_idx]
                if SEVERITY_ORDER.get(f.severity.value, 99) < SEVERITY_ORDER.get(existing.severity.value, 99):
                    location_map[key] = i
            else:
                location_map[key] = i

    # Keep findings with unique locations + findings without file_path
    layer1: list[ReviewFinding] = []
    seen_locations = set()
    for f in findings:
        if f.file_path and f.line_number:
            key = f"{f.file_path}:{f.line_number}"
            if key in seen_locations:
                continue
            seen_locations.add(key)
            # Keep the best one for this location
            best_idx = location_map[key]
            layer1.append(findings[best_idx])
        else:
            layer1.append(f)

    # Layer 2: exact title dedup (keep higher severity)
    title_map: dict[str, int] = {}
    for i, f in enumerate(layer1):
        key = f.title.lower().strip()
        if key in title_map:
            existing_idx = title_map[key]
            if SEVERITY_ORDER.get(f.severity.value, 99) < SEVERITY_ORDER.get(layer1[existing_idx].severity.value, 99):
                title_map[key] = i
        else:
            title_map[key] = i

    layer2: list[ReviewFinding] = [layer1[i] for i in title_map.values()]

    # Layer 3: title similarity dedup (title Jaccard ≥ 0.8 → direct duplicate)
    layer3: list[ReviewFinding] = []
    layer3_titles: list[str] = []
    for f in layer2:
        merged = False
        f_title_kw = _extract_keywords(f.title)
        for j, existing in enumerate(layer3):
            existing_title_kw = _extract_keywords(existing.title)
            # Skip if both title keyword sets are too small (≤1) — high false positive risk
            if len(f_title_kw) <= 1 and len(existing_title_kw) <= 1:
                continue
            if _jaccard_similarity(f_title_kw, existing_title_kw) >= 0.8:
                # Merge: keep higher severity
                if SEVERITY_ORDER.get(f.severity.value, 99) < SEVERITY_ORDER.get(existing.severity.value, 99):
                    merged_desc = existing.description
                    if f.description and f.description not in merged_desc:
                        merged_desc = f.description + " " + merged_desc
                    layer3[j] = ReviewFinding(
                        agent=f.agent,
                        severity=f.severity,
                        title=f.title,
                        description=merged_desc,
                        file_path=f.file_path or existing.file_path,
                        line_number=f.line_number or existing.line_number,
                        code_snippet=f.code_snippet or existing.code_snippet,
                        suggestion=f.suggestion or existing.suggestion,
                    )
                else:
                    if f.description and f.description not in existing.description:
                        layer3[j] = ReviewFinding(
                            agent=existing.agent,
                            severity=existing.severity,
                            title=existing.title,
                            description=existing.description + " " + f.description,
                            file_path=existing.file_path or f.file_path,
                            line_number=existing.line_number or f.line_number,
                            code_snippet=existing.code_snippet or f.code_snippet,
                            suggestion=existing.suggestion or f.suggestion,
                        )
                layer3_titles[j] = layer3[j].title
                merged = True
                break
        if not merged:
            layer3.append(f)
            layer3_titles.append(f.title)

    # Layer 4: semantic similarity dedup
    unique: list[ReviewFinding] = []
    unique_keywords: list[set[str]] = []

    for f in layer3:
        f_keywords = _extract_keywords(f.title + " " + f.description)
        f_title_kw = _extract_keywords(f.title)
        merged = False

        for j, existing_kw in enumerate(unique_keywords):
            # Skip Jaccard dedup if both keyword sets are too small (high false positive risk)
            if len(f_keywords) <= 1 and len(existing_kw) <= 1:
                continue
            # Adaptive threshold: stricter for short texts
            kw_count = min(len(f_keywords), len(existing_kw))
            similarity_threshold = 0.8 if kw_count < 5 else 0.5
            # Title similarity acts as a gate: if titles are very similar, lower the bar
            title_sim = _jaccard_similarity(f_title_kw, _extract_keywords(unique[j].title))
            body_sim = _jaccard_similarity(f_keywords, existing_kw)
            if body_sim >= similarity_threshold and title_sim >= 0.3:
                # Merge: keep higher severity, combine descriptions
                existing = unique[j]
                if SEVERITY_ORDER.get(f.severity.value, 99) < SEVERITY_ORDER.get(existing.severity.value, 99):
                    merged_desc = existing.description
                    if f.description and f.description not in merged_desc:
                        merged_desc = f.description + " " + merged_desc
                    unique[j] = ReviewFinding(
                        agent=f.agent,
                        severity=f.severity,
                        title=f.title,
                        description=merged_desc,
                        file_path=f.file_path or existing.file_path,
                        line_number=f.line_number or existing.line_number,
                        code_snippet=f.code_snippet or existing.code_snippet,
                        suggestion=f.suggestion or existing.suggestion,
                    )
                else:
                    if f.description and f.description not in existing.description:
                        unique[j] = ReviewFinding(
                            agent=existing.agent,
                            severity=existing.severity,
                            title=existing.title,
                            description=existing.description + " " + f.description,
                            file_path=existing.file_path or f.file_path,
                            line_number=existing.line_number or f.line_number,
                            code_snippet=existing.code_snippet or f.code_snippet,
                            suggestion=existing.suggestion or f.suggestion,
                        )
                unique_keywords[j] = _extract_keywords(unique[j].title + " " + unique[j].description)
                merged = True
                break

        if not merged:
            unique.append(f)
            unique_keywords.append(f_keywords)

    return merge_adjacent(unique)


#: Two findings this far apart in the same file are close enough to be the
#: same defect reported twice — an off-by-one in a line estimate is the usual
#: reason agents disagree on the number.
_PROXIMITY_LINES = 3

#: Lower than the 0.8 used for a same-line match: at the same spot in the file
#: a partial title overlap is already strong evidence, because the alternative
#: explanation would be two unrelated defects within three lines that happen to
#: share their subject words.
_PROXIMITY_TITLE_SIMILARITY = 0.34


def merge_adjacent(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Collapse findings that describe one defect at nearly the same line.

    Nine agents review the same diff independently, so the same bug is often
    reported two or three times under different titles and line numbers —
    "SQL Injection in get_order()" at line 8 from SecurityAgent and the same
    injection at line 7 from FixAgent. Without this pass the customer sees
    duplicate inline comments on consecutive lines, which reads as noise and
    makes a correct finding easier to dismiss.

    Merged findings keep the highest severity, the earliest line and every
    contributing agent name, so agreement between agents stays visible.
    """
    if len(findings) < 2:
        return findings

    grouped: dict[str, list[ReviewFinding]] = {}
    passthrough: list[ReviewFinding] = []
    for f in findings:
        if f.file_path and f.line_number:
            grouped.setdefault(f.file_path, []).append(f)
        else:
            passthrough.append(f)

    merged_out: list[ReviewFinding] = []
    for group in grouped.values():
        group.sort(key=lambda f: (f.line_number, SEVERITY_ORDER.get(f.severity.value, 99)))
        kept: list[ReviewFinding] = []
        for f in group:
            target = None
            for idx, existing in enumerate(kept):
                if abs(existing.line_number - f.line_number) > _PROXIMITY_LINES:
                    continue
                if (
                    _jaccard_similarity(
                        _extract_keywords(existing.title), _extract_keywords(f.title)
                    )
                    >= _PROXIMITY_TITLE_SIMILARITY
                ):
                    target = idx
                    break

            if target is None:
                kept.append(f)
                continue

            existing = kept[target]
            primary, other = (
                (f, existing)
                if SEVERITY_ORDER.get(f.severity.value, 99)
                < SEVERITY_ORDER.get(existing.severity.value, 99)
                else (existing, f)
            )
            agents = primary.agent
            if other.agent and other.agent not in agents:
                agents = f"{agents} + {other.agent}"
            description = primary.description
            if other.description and other.description not in description:
                description = f"{description}\n\n_Also flagged by {other.agent}:_ {other.description}"
            kept[target] = ReviewFinding(
                agent=agents,
                severity=primary.severity,
                title=primary.title,
                description=description,
                file_path=primary.file_path or other.file_path,
                line_number=min(primary.line_number, other.line_number),
                code_snippet=primary.code_snippet or other.code_snippet,
                suggestion=primary.suggestion or other.suggestion,
            )

        merged_out.extend(kept)

    merged_out.extend(passthrough)
    merged_out.sort(key=lambda f: SEVERITY_ORDER.get(f.severity.value, 99))
    return merged_out
