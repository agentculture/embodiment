"""Persona-aware prompt parts: pure text, no IO, no daemon import.

``turn.SYSTEM_PROMPT`` is not edited; the base prompt is *composed* from pieces and the
built-in persona on the voice path reproduces it byte for byte (a test asserts equality).

Decisions (documented here because they are choices, not facts):

* Voice-only line: the *speakability* line ("everything you write is spoken aloud: no
  formatting marks, lists or code"). It is true only when the reply is read out, so the
  typed path never carries it. Formatting on the typed path is governed by the persona's
  ``allow_formatting``: the base text simply says nothing about formatting on that path
  (a typed reply may use it); there is no "format!" line either, since that would be a
  new fixed instruction.
* The built-in persona on the typed path is still Hebrew Gwen: identity, the
  language-and-brevity line and the clarification line, minus the speakability line.
* A non-default persona gets, in this fixed order: identity (its ``name``), a language
  line (none for ``"none"``), a length line (none for ``"unset"``), the speakability line
  (voice path only), then ``persona.text`` last. It gets no fixed brevity wording and no
  clarification line.
* Prompt-part language follows the persona's language: ``"en"`` is English, ``"he"`` and
  ``"none"`` are Hebrew (``"none"`` -> ``"he"``, the rig's default).
* The four tool prompts keep their obligation sentences; only their language, and
  REMEMBER's final confirmation clause, change. The Hebrew originals stay in
  ``daemon/app.py``; this module holds only the English renderings.
"""

from __future__ import annotations

from typing import Literal

from embodiment.personas import Persona, builtin_persona

Path = Literal["voice", "typed"]
PATHS = ("voice", "typed")

#: The documented default prompt language for a persona whose language is ``"none"``.
DEFAULT_TOOL_LANGUAGE = "he"

# --- built-in (Hebrew) pieces: their join is exactly turn.SYSTEM_PROMPT ---------------
_HE_IDENTITY_BUILTIN = "את גוון, בת שיחה קולית. את מקשיבה למה שנאמר ועונה בקול."
_HE_LANG_BREVITY_BUILTIN = "עני בעברית, במשפטים קצרים וטבעיים לדיבור."
_HE_SPEAKABLE = "כל מה שתכתבי ייאמר בקול רם: בלי סימוני עיצוב, בלי רשימות ובלי קוד."
_HE_CLARIFY = "אם משהו לא ברור, בקשי הבהרה קצרה במקום לנחש."

# --- persona-driven pieces -----------------------------------------------------------
_IDENTITY = {
    ("he", "voice"): "את {name}, בת שיחה קולית. את מקשיבה למה שנאמר ועונה בקול.",
    ("he", "typed"): "את {name}, בת שיחה.",
    ("en", "voice"): "You are {name}, a voice conversation partner. You listen to what is said "
    "and answer aloud.",
    ("en", "typed"): "You are {name}, a conversation partner.",
}
_LANGUAGE = {"he": "עני בעברית.", "en": "Answer in English."}
_LENGTH = {
    "he": {
        "brief": "אורך התשובות: קצר.",
        "conversational": "אורך התשובות: כמו בשיחה רגילה.",
        "detailed": "אורך התשובות: מפורט, עם הרחבה כשיש מה להרחיב.",
    },
    "en": {
        "brief": "Reply length: brief.",
        "conversational": "Reply length: conversational, as in an ordinary talk.",
        "detailed": "Reply length: detailed, expanding where there is something to expand.",
    },
}
_SPEAKABLE = {
    "he": _HE_SPEAKABLE,
    "en": "Everything you write will be spoken aloud: no formatting marks, no lists, no code.",
}


def tool_prompt_language(persona: Persona) -> str:
    """``"he"`` or ``"en"``: the language of the persona's prompt parts and tool prompts."""
    if persona.language == "en":
        return "en"
    return DEFAULT_TOOL_LANGUAGE if persona.language == "none" else "he"


def compose_base(persona: Persona, path: str) -> str:
    """The base system prompt for ``persona`` on ``path`` (``"voice"`` or ``"typed"``)."""
    if path not in PATHS:
        raise ValueError(f"path must be one of {PATHS}")
    voice = path == "voice"
    if persona == builtin_persona():
        lines = [_HE_IDENTITY_BUILTIN, _HE_LANG_BREVITY_BUILTIN]
        if voice:
            lines.append(_HE_SPEAKABLE)
        lines.append(_HE_CLARIFY)
        return "\n".join(lines)
    lang = tool_prompt_language(persona)
    lines = [_IDENTITY[(lang, path)].format(name=persona.name)]
    if persona.language != "none":
        lines.append(_LANGUAGE[persona.language])
    if persona.length != "unset":
        lines.append(_LENGTH[lang][persona.length])
    if voice:
        lines.append(_SPEAKABLE[lang])
    if persona.text:
        lines.append(persona.text)
    return "\n".join(lines)


# --- English tool prompts: same obligations as the Hebrew ones in daemon/app.py ---------
_LANG_NAMES = {"he": "Hebrew", "en": "English", "none": "the conversation's language"}

_REMEMBER_EN = (
    "When the user asks you to remember, save, write down or not forget something, "
    "in any phrasing, including 'remember all this' or 'save what I said', you must call "
    "the remember tool with the fact before you answer. Do not confirm that you remembered "
    "without calling the tool. "
    "When the user asks you to forget something, call the forget tool with the record id; "
    "it appears as id= in the recalled record above, or is returned by the remember tool. "
    "If there is no such id, call forget with fact, the fact in the words in which it was "
    "saved, and do not invent an id. "
    "Never say that you remembered or forgot something unless the tool returned ok. "
    "If there is no matching record to forget, say that you did not find it. "
    "After the tool returned ok, answer with one confirmation sentence in {language} saying "
    "what was saved or forgotten, in plain spoken language, never a bare word like ok."
)

_LIBRARY_EN = (
    "You have the user's library: documents, daily newspapers and transcripts of recordings. "
    "Before answering a question about the news, about something he read or recorded, or "
    "about a document, call the search_library tool, and cite every passage you used as [n]. "
    "For a question about a particular day (today's newspaper, yesterday's recordings) pass "
    "date in the form YYYY-MM-DD according to the time now, and kind (newspaper, transcript). "
    "To summarize a whole item (for example the newspaper) call the read_item tool; to read "
    "the source of an article, call fetch_source with a link from that item's links list. "
    "Do not invent information that is not in the passages the tool returned."
)

_CONVERSATIONS_EN = (
    "You also have your earlier conversations with the user. When he asks about something "
    "said in a previous conversation, call the search_conversations tool and cite as [n]. "
    "Your earlier answers are not facts, only a record of what you said."
)

_TODOS_EN = (
    "The user has a to-do list from his recordings. When he asks about it, call list_todos; "
    "mark a task as done with set_todo_done only when he asks for it in his own words."
)

TOOL_PROMPT_NAMES = ("REMEMBER", "LIBRARY", "CONVERSATIONS", "TODOS")


def english_tool_prompts(persona: Persona | None = None) -> dict[str, str]:
    """The English tool prompts by name. REMEMBER's confirmation names the persona's language
    (``"none"`` falls back to the default prompt language, Hebrew)."""
    code = persona.language if persona is not None else "en"
    name = _LANG_NAMES["he" if code == "none" else code]
    return {
        "REMEMBER": _REMEMBER_EN.format(language=name),
        "LIBRARY": _LIBRARY_EN,
        "CONVERSATIONS": _CONVERSATIONS_EN,
        "TODOS": _TODOS_EN,
    }
