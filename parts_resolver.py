from __future__ import annotations
import csv
import re
import logging
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional

logger = logging.getLogger(__name__)

# part_name (lowercase, as returned by resolve()) -> list of (cat_id, cat_label)
_PART_NAME_TO_CATS: Dict[str, List[Tuple[str, str]]] = {
    "масляный фильтр":          [("7",    "Масляный фильтр")],
    "воздушный фильтр":         [("8",    "Воздушный фильтр")],
    "топливный фильтр":         [("9",    "Топливный фильтр")],
    "салонный фильтр":          [("424",  "Салонный фильтр")],
    "тормозные колодки":        [("281",  "Колодки передние"), ("282", "Колодки задние")],
    "тормозной диск":           [("82",   "Тормозной диск передний"), ("84", "Тормозной диск задний")],
    "амортизатор":              [("1041", "Амортизатор передний"), ("1042", "Амортизатор задний")],
    "пружина подвески":         [("188",  "Пружина передняя"), ("189", "Пружина задняя")],
    "рычаг подвески":           [("273",  "Рычаг подвески передний"), ("274", "Рычаг подвески задний")],
    "шаровая опора":            [("1037", "Шаровая опора")],
    "свеча зажигания":          [("686",  "Свеча зажигания")],
    "катушка зажигания":        [("689",  "Катушка зажигания")],
    "ремень грм":               [("306",  "Ремень ГРМ")],
    "комплект грм":             [("307",  "Комплект ГРМ")],
    "радиатор":                 [("470",  "Радиатор")],
    "подшипник ступицы":        [("655",  "Подшипник ступицы")],
    "шрус":                     [("5",    "ШРУС")],
}

# part_name -> (position_str_or_None, side_str_or_None, explicit_side_bool)
_PART_NAME_TO_POSITION: Dict[str, Tuple[Optional[str], Optional[str], bool]] = {
    "масляный фильтр":          (None,      None,    False),
    "воздушный фильтр":         (None,      None,    False),
    "топливный фильтр":         (None,      None,    False),
    "салонный фильтр":          (None,      None,    False),
    "тормозные колодки":        (None,      None,    False),
    "тормозной диск":           (None,      None,    False),
    "амортизатор":              (None,      None,    False),
    "пружина подвески":         (None,      None,    False),
    "рычаг подвески":           (None,      None,    False),
    "шаровая опора":            (None,      None,    False),
    "свеча зажигания":          (None,      None,    False),
    "катушка зажигания":        (None,      None,    False),
    "ремень грм":               (None,      None,    False),
    "комплект грм":             (None,      None,    False),
    "радиатор":                 (None,      None,    False),
    "подшипник ступицы":        (None,      None,    False),
    "шрус":                     (None,      None,    False),
}

# part_name -> group_num (for /help grouping)
_PART_NAME_TO_GROUP: Dict[str, int] = {
    "масляный фильтр":          1,
    "воздушный фильтр":         1,
    "топливный фильтр":         3,
    "салонный фильтр":          10,
    "тормозные колодки":        5,
    "тормозной диск":           5,
    "амортизатор":              6,
    "пружина подвески":         6,
    "рычаг подвески":           6,
    "шаровая опора":            6,
    "свеча зажигания":          9,
    "катушка зажигания":        9,
    "ремень грм":               1,
    "комплект грм":             1,
    "радиатор":                 2,
    "подшипник ступицы":        11,
    "шрус":                     8,
}

_POSITION_TOKENS = {
    "передний": "front", "передняя": "front", "передние": "front",
    "передн": "front",
    "задний": "rear",  "задняя": "rear",  "задние": "rear",
    "задн": "rear",
}
_SIDE_TOKENS = {
    "левый": "left", "левая": "left", "лев": "left",
    "правый": "right", "правая": "right", "прав": "right",
}


class PartsResolver:
    def __init__(self, data_dir: str = "parts"):
        self.data_dir = Path(data_dir)
        self.alias_map: Dict[str, dict] = {}
        self.part_name_map: Dict[str, dict] = {}
        self.synonym_map: Dict[str, dict] = {}
        self.modifier_map: Dict[str, str] = {}
        self._load_all()
        logger.info(
            "PartsResolver: %d aliases, %d parts, %d synonyms, %d modifiers",
            len(self.alias_map), len(self.part_name_map),
            len(self.synonym_map), len(self.modifier_map),
        )

    def _read_csv(self, filename: str) -> List[Dict[str, str]]:
        path = self.data_dir / filename
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f, delimiter=";"))

    def _norm(self, text: str) -> str:
        text = (text or "").strip().lower()
        text = text.replace("ё", "е")
        text = re.sub(r"[^a-zа-я0-9\s-]", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _tokenize(self, text: str) -> List[str]:
        return [x for x in self._norm(text).split() if x]

    def _load_all(self):
        for row in self._read_csv("parts_dictionary_v3.csv"):
            pname = self._norm(row.get("part_name", ""))
            if pname:
                self.part_name_map[pname] = row
            for s in (row.get("synonyms") or "").split(","):
                s = self._norm(s)
                if s:
                    self.synonym_map[s] = row

        for row in self._read_csv("query_aliases_v3.csv"):
            alias = self._norm(row.get("normalized_alias") or row.get("alias") or "")
            if alias:
                self.alias_map[alias] = row

        for row in self._read_csv("query_modifiers_v3.csv"):
            key = self._norm(row.get("modifier", ""))
            if key:
                self.modifier_map[key] = row.get("modifier_type", "attribute")

    def _extract_modifiers(self, text: str) -> Dict[str, List[str]]:
        tokens = self._tokenize(text)
        out: Dict[str, List[str]] = {"side": [], "position": [], "attribute": []}
        for token in tokens:
            t = self._norm(token)
            mtype = self.modifier_map.get(t)
            if t in ("левый", "правый"):
                out["side"].append(t)
            elif t in ("передний", "задний", "верхний", "нижний", "внутренний", "наружный"):
                out["position"].append(t)
            elif mtype:
                out["attribute"].append(t)
        return out

    def _strip_modifiers(self, text: str) -> str:
        tokens = self._tokenize(text)
        return " ".join(t for t in tokens if t not in self.modifier_map).strip()

    def _sorted_key(self, text: str) -> str:
        return " ".join(sorted(self._tokenize(text)))

    def _make_result(self, row: dict, original: str, normalized: str,
                     modifiers: dict, match_type: str, confidence: float) -> dict:
        return {
            "original_query": original,
            "normalized_query": normalized,
            "matched": True,
            "part_name": row.get("part_name", ""),
            "group_name": row.get("group_name", ""),
            "subgroup_name": row.get("subgroup_name", ""),
            "part_key": row.get("part_key", self._norm(row.get("part_name", "")).replace(" ", "_")),
            "modifiers": modifiers,
            "match_type": match_type,
            "confidence": confidence,
        }

    def resolve(self, query: str) -> Dict[str, Any]:
        original = query
        normalized = self._norm(query)
        modifiers = self._extract_modifiers(normalized)
        stripped = self._strip_modifiers(normalized)

        if normalized in self.alias_map:
            return self._make_result(self.alias_map[normalized], original, normalized,
                                     modifiers, "alias_exact", 0.99)
        if stripped and stripped in self.alias_map:
            return self._make_result(self.alias_map[stripped], original, normalized,
                                     modifiers, "alias_stripped", 0.95)
        if stripped in self.part_name_map:
            return self._make_result(self.part_name_map[stripped], original, normalized,
                                     modifiers, "part_name_exact", 0.90)
        if stripped in self.synonym_map:
            return self._make_result(self.synonym_map[stripped], original, normalized,
                                     modifiers, "synonym_exact", 0.85)

        stripped_sorted = self._sorted_key(stripped)
        for alias, row in self.alias_map.items():
            if self._sorted_key(alias) == stripped_sorted:
                return self._make_result(row, original, normalized,
                                         modifiers, "alias_token_sorted", 0.75)

        tokens = set(self._tokenize(stripped))
        best_score, best_row = 0, None
        for alias, row in self.alias_map.items():
            score = len(tokens & set(self._tokenize(alias)))
            if score > best_score and score >= 2:
                best_score, best_row = score, row
        if best_row:
            return self._make_result(best_row, original, normalized,
                                     modifiers, "alias_partial", 0.60)

        suggestions = self._suggest(stripped)
        return {
            "original_query": original,
            "normalized_query": normalized,
            "matched": False,
            "part_name": None,
            "group_name": None,
            "subgroup_name": None,
            "part_key": None,
            "modifiers": modifiers,
            "match_type": "not_found",
            "confidence": 0.0,
            "suggestions": suggestions[:5],
        }

    def _suggest(self, text: str) -> List[str]:
        tokens = set(self._tokenize(text))
        scored = []
        for alias in self.alias_map:
            score = len(tokens & set(self._tokenize(alias)))
            if score > 0:
                scored.append((score, alias))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [x[1] for x in scored[:5]]

    # ------------------------------------------------------------------
    # Compatibility layer: find_part() and get_all_parts()
    # These are called by test_vin_bot.py and must return the old format.
    # ------------------------------------------------------------------

    def find_part(
        self, text: str
    ) -> Optional[Tuple[List[Tuple[str, str]], str, Optional[str], Optional[str], bool]]:
        """
        Returns (cats_list, part_name, position, side, explicit_side) or None.
        cats_list: list of (cat_id, cat_label)
        position:  "front"/"rear"/None  (derived from query tokens)
        side:      "left"/"right"/None  (derived from query tokens)
        explicit_side: True if side was explicitly mentioned in query
        """
        result = self.resolve(text)
        if not result.get("matched"):
            return None

        part_name_raw = result.get("part_name") or ""
        part_name_key = self._norm(part_name_raw)

        cats_list = _PART_NAME_TO_CATS.get(part_name_key)
        if not cats_list:
            # fallback: single unknown cat
            cats_list = []

        # Extract position and side from query tokens
        tokens = self._tokenize(self._norm(text))
        position: Optional[str] = None
        side: Optional[str] = None
        explicit_side = False

        for tok in tokens:
            if tok in _POSITION_TOKENS and position is None:
                position = _POSITION_TOKENS[tok]
            if tok in _SIDE_TOKENS and side is None:
                side = _SIDE_TOKENS[tok]
                explicit_side = True

        # Filter cats_list by position if specified
        if position and len(cats_list) > 1:
            if position == "front":
                filtered = [(c, l) for c, l in cats_list if "передн" in l.lower() or "перед" in l.lower()]
            else:
                filtered = [(c, l) for c, l in cats_list if "задн" in l.lower() or "зад" in l.lower()]
            if filtered:
                cats_list = filtered

        return (cats_list, part_name_raw, position, side, explicit_side)

    def get_all_parts(self) -> Dict[str, Tuple[List[Tuple[str, str]], str, int]]:
        """
        Returns {keyword: (cats_list, part_name, group_num)} for /help command.
        """
        out: Dict[str, Tuple[List[Tuple[str, str]], str, int]] = {}
        for part_name_key, cats in _PART_NAME_TO_CATS.items():
            group_num = _PART_NAME_TO_GROUP.get(part_name_key, 0)
            # Use part_name_key as keyword (normalized)
            out[part_name_key] = (cats, part_name_key.title(), group_num)
        return out
