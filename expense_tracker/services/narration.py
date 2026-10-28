"""Turn raw bank descriptions into readable rows, driven by regex rules.

The rules live in a TOML file (see narration_rules.toml for the format):
the bundled default, or the user's own copy in the config dir when present.
The raw description stays in the DB untouched (duplicate detection relies on
it); this module only interprets it for display, merchant grouping and the
default type on import.
"""

from __future__ import annotations

import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..config import CONFIG_DIR

DEFAULT_RULES = Path(__file__).with_name("narration_rules.toml")
USER_RULES = CONFIG_DIR / "narration_rules.toml"

_TEMPLATE = re.compile(r"\{(\w+)((?:\|\w+)*)\}")


@dataclass
class Narration:
    text: str                       # cleaned description (shown as is when nothing matched)
    title: str = ""                 # who / what, when a rule matched
    method: str = ""                # UPI, NEFT, Net banking, Card, Cash ...
    details: list[str] = field(default_factory=list)
    ref: str = ""
    kind: str | None = None         # default transaction type from a rule
    rule: str = ""                  # name of the rule that produced the title

    @property
    def parsed(self) -> bool:
        return bool(self.title)

    @property
    def display(self) -> str:
        return self.title or self.text


@dataclass
class _Rule:
    name: str
    regex: re.Pattern
    match_on: str = "clean"
    title: list[str] = field(default_factory=list)
    method: str = ""
    details: list[str] = field(default_factory=list)
    ref: str = ""
    kind: str | None = None


@dataclass
class RuleSet:
    path: Path
    cleanup: list[tuple[re.Pattern, str]]
    rules: list[_Rule]
    banks: dict[str, str]
    ignore_notes: set[str]
    note_min_length: int
    keep_upper: set[str]

    # ---- filters for templates ------------------------------------------
    def _filter(self, value: str, name: str) -> str:
        value = value.strip()
        match name:
            case "title":
                value = re.sub(r"\s+", " ", value).strip(" -*/")
                return " ".join(w.upper() if w.upper() in self.keep_upper else w.capitalize()
                                for w in value.split())
            case "upper":
                return value.upper()
            case "lower":
                return value.lower()
            case "bank":
                return self.banks.get(value.upper()[:4], value.upper())
            case "note":
                return "" if value.upper() in self.ignore_notes or len(value) < self.note_min_length else f"“{value}”"
            case "company":  # drop legal suffixes: 'BLINK COMMERCE PVT LTD' -> 'BLINK COMMERCE'
                return re.sub(r"\s+(PVT\.?|PRIVATE)?\s*(LTD\.?|LIMITED|LLP)\s*$", "", value, flags=re.I).strip()
            case "autopay":  # any captured text -> 'Auto-pay' (e.g. SBI Card's 'SI' marker)
                return "Auto-pay" if value else ""
        raise ValueError(f"Unknown filter '{name}'")

    def _render(self, template: str, groups: dict[str, str | None]) -> str:
        def sub(m: re.Match) -> str:
            value = groups.get(m[1]) or ""
            for f in filter(None, m[2].split("|")):
                value = self._filter(value, f)
            return value
        return _TEMPLATE.sub(sub, template).strip()

    # ---- parsing ---------------------------------------------------------
    def parse(self, raw: str) -> Narration:
        text = raw
        for regex, replace in self.cleanup:
            text = regex.sub(replace, text)
        n = Narration(text=re.sub(r"\s+", " ", text).strip() or raw.strip())

        for rule in self.rules:
            if n.title and n.kind:
                break
            m = rule.regex.search(raw if rule.match_on == "raw" else text)
            if not m:
                continue
            groups = m.groupdict()
            if rule.kind and not n.kind:
                n.kind = rule.kind
            if rule.title and not n.title:
                n.title = next((t for t in (self._render(t, groups) for t in rule.title) if t), "")
                if n.title:
                    n.rule = rule.name
                    n.method = self._render(rule.method, groups)
                    n.details = [d for d in (self._render(d, groups) for d in rule.details) if d]
                    n.ref = self._render(rule.ref, groups)
        return n


def load_rules(path: Path) -> RuleSet:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    rules = []
    for r in data.get("rule", []):
        title = r.get("title", [])
        rules.append(_Rule(
            name=r.get("name", r["pattern"]), regex=re.compile(r["pattern"], re.I),
            match_on=r.get("match_on", "clean"), title=[title] if isinstance(title, str) else title,
            method=r.get("method", ""), details=r.get("details", []), ref=r.get("ref", ""),
            kind=r.get("kind"),
        ))
    notes = data.get("notes", {})
    return RuleSet(
        path=path,
        cleanup=[(re.compile(c["pattern"], re.I | re.M), c.get("replace", "")) for c in data.get("cleanup", [])],
        rules=rules,
        banks={k.upper(): v for k, v in data.get("banks", {}).items()},
        ignore_notes={s.upper() for s in notes.get("ignore", [])} | {""},
        note_min_length=notes.get("min_length", 5),
        keep_upper={s.upper() for s in data.get("title", {}).get("keep_upper", [])},
    )


_cache: dict = {"key": None, "rules": None, "error": None}


def rules_path() -> Path:
    return USER_RULES if USER_RULES.exists() else DEFAULT_RULES


def current_rules() -> RuleSet:
    """Rules from the active file, reloaded whenever it changes. A broken user
    file falls back to the defaults (the error is kept for Settings to show)."""
    path = rules_path()
    key = (path, path.stat().st_mtime)
    if _cache["key"] != key:
        try:
            _cache["rules"], _cache["error"] = load_rules(path), None
        except (tomllib.TOMLDecodeError, re.error, KeyError, ValueError) as exc:
            _cache["rules"], _cache["error"] = load_rules(DEFAULT_RULES), f"{path.name}: {exc}"
        _cache["key"] = key
    return _cache["rules"]


def rules_error() -> str | None:
    current_rules()
    return _cache["error"]


def create_user_copy() -> Path:
    """Copy the bundled rules to the config dir for the user to edit."""
    if not USER_RULES.exists():
        USER_RULES.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_RULES, USER_RULES)
    return USER_RULES


def parse(raw: str) -> Narration:
    return current_rules().parse(raw)
