"""The one JARVIS settings schema (V2 D17).

Every setting JARVIS reads anywhere — the scattered environment variables of
the v2 plan §1.3, the plugin-local knobs that used to hide behind each
plugin's own ``_env()`` helper, the runtime-only Hermes config keys the
``aura-settings`` plugin lets an operator change live, and the Glass HUD's
layout state (D30) — is declared here once, as one field of :class:`Settings`,
with its type, non-secret default, description, UI group, secret/restart/live
flags and *where it lands* (a ``.env`` variable name, a Hermes ``config.yaml``
dotted key, or both). :mod:`aura_schema.generate` renders the deploy config
template, the ``jsettings`` contract and the settings reference from it, so
those cannot drift from each other or from the code that reads the values.

Three things come from here:

* :class:`Settings` — the Pydantic model, one typed attribute per setting.
  :func:`from_env` reads the process environment once (the only ``os.environ``
  read in the settings layer) and returns a validated instance; a variable whose
  value does not fit raises naming the variable. The Hermes plugins and the
  adapter read their configuration as attributes of this object.
* :data:`SETTINGS` — the registry the generators walk, derived from the model's
  fields so there is exactly one place a setting is declared.
* :func:`validate_settings` — the fail-fast validator over a settings *mapping*
  (a config fragment, a ``jsettings set`` payload): an unknown key is an error
  naming the key, and a value of the wrong type is an error naming the key —
  never a silently ignored typo.

Secrets carry their *name and shape* only; a secret's default is ``""``, never a
real value, because this file is committed and the real token lives in ``.env``.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any, Literal, Optional, Union, cast, get_args, get_origin

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)


class Kind(str, Enum):
    """The value type of a setting, in the vocabulary the surfaces render from.

    ``STRING``/``URL``/``PATH`` are all textual at runtime; the distinction is a
    widget hint (a URL field, a path field) and documentation, not a different
    Python type. ``OBJECT`` is a JSON document validated against a named
    ``shape`` (today only the HUD layouts).
    """

    STRING = "string"
    URL = "url"
    PATH = "path"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    ENUM = "enum"
    LIST = "list"
    OBJECT = "object"


class Widget(str, Enum):
    """The control a settings UI draws for a setting.

    This is the shared vocabulary the web console and the Android settings
    screen both render from (published in ``tests/contracts/hermes-settings.json``);
    keep it small and stable so both surfaces can switch on it exhaustively.
    """

    TEXT = "text"          #: a single-line string field
    URL = "url"            #: a single-line field validated as a URL
    PATH = "path"          #: a single-line filesystem-path field
    NUMBER = "number"      #: an integer/float field (honours ``min``/``max``)
    TOGGLE = "toggle"      #: an on/off switch for a bool
    SELECT = "select"      #: a one-of picker for an enum (uses ``choices``)
    MULTISELECT = "multi"  #: a list-of-strings editor
    SECRET = "secret"      #: a masked field whose value is never shown back
    MANAGED = "managed"    #: written by the app itself (the HUD's window manager), shown as a summary with a reset


class Group(str, Enum):
    """The UI section a setting is shown under. Ordered as the console lays them out."""

    MODEL = "Model"
    VOICE = "Voice"
    AGENT = "Agent"
    APPROVALS = "Approvals"
    TOOLS = "Tools"
    HOUSE = "House"
    HOMELAB = "Homelab"
    MESSAGING = "Messaging"
    NETWORK = "Network"
    CONSOLE = "Console"
    BACKUPS = "Backups"
    SYSTEM = "System"
    HUD = "HUD"


#: What each group is for — the sentence the settings screens show under the
#: section title. The HUD line is the D30 wording: JARVIS may rearrange the HUD
#: unless it is locked, and the person's own arrangement always wins.
GROUP_DESCRIPTIONS: dict[Group, str] = {
    Group.MODEL: "Which model answers, where it runs and how much context it gets.",
    Group.VOICE: "Speech in and out: the recogniser, the voice, and how a spoken turn ends.",
    Group.AGENT: "How far a goal or a single turn may run on its own.",
    Group.APPROVALS: "When JARVIS asks before acting.",
    Group.TOOLS: "Which toolsets new sessions start with.",
    Group.HOUSE: "jarvis-core, the house service behind the rooms, devices and automations.",
    Group.HOMELAB: "The services on the LAN JARVIS reaches: search, images, telemetry, workflows, "
                   "guests and GitLab.",
    Group.MESSAGING: "The chat platforms JARVIS answers on. A platform only ever answers the ids you "
                     "list; an empty list means nobody.",
    Group.NETWORK: "Addresses and origins the apps and the adapter bind to and accept.",
    Group.CONSOLE: "The web console's own login and phone pairing.",
    Group.BACKUPS: "Where the nightly backup lands and how many are kept.",
    Group.SYSTEM: "Images, timezone, updates and the Hermes back-ends the adapter proxies.",
    Group.HUD: "How the HUD is arranged; JARVIS may rearrange it unless locked, and your own "
               "arrangement always wins.",
}


# --------------------------------------------------------------------------- #
# The HUD layout document (D30): one per surface × activity.
# --------------------------------------------------------------------------- #
#: The five surfaces that carry a HUD; each keeps its own layouts because a
#: desktop tiling tree does not fit a phone's stack.
Surface = Literal["web", "desktop", "android_phone", "android_tablet", "console"]
#: The window manager's four modes.
#: The map's four presets (HUD.md §6.5). They replaced the window manager's
#: modes in v3: `tiled` and `floating` described a split tree and a z-order, and
#: ADR-0036 retired both. A saved v2 layout names a mode that no longer exists
#: and is dropped on first load, which that ADR records as the cost.
HudMode = Literal["overview", "task", "focus", "ambient"]

#: The three orbital bands, inner (what is happening now) to outer (reference).
Band = Literal["inner", "middle", "outer"]

#: Which tier of the settings UI a row appears in (ADR-0035).
#:
#: The tier is a field on the setting, not a list beside the schema: a
#: hand-maintained "these are the basic ones" is a second source of truth and is
#: wrong the first time somebody adds a setting. `advanced` is the default, so a
#: new setting lands somewhere sensible without anyone editing a second file, and
#: `test_settings.py` asserts that exactly seven fields are `basic` — a
#: drive-by promotion fails CI rather than quietly growing the first screen.
Tier = Literal["basic", "advanced", "expert"]
#: Follow the OS setting, or force it on/off.
TriState = Literal["system", "on", "off"]
CarNavigation = Literal["jarvis", "organic", "google", "here"]
CarClock = Literal["bar", "panel", "off"]
CarVoice = Literal["auto", "house", "phone"]
#: Hermes' own vocabulary (hermes_constants.parse_reasoning_effort); "none" turns thinking off.
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]

SURFACES: tuple[str, ...] = get_args(Surface)
HUD_MODES: tuple[str, ...] = get_args(HudMode)
TRI_STATES: tuple[str, ...] = get_args(TriState)

#: Activities a surface keeps a layout for — a suggestion for the surfaces, not
#: an enforced list: the agent may name a new one and the shape still validates.
HUD_ACTIVITIES: tuple[str, ...] = ("default", "chat", "goal", "approval", "image", "browser", "voice", "telemetry")


class Rect(BaseModel):
    """A panel's placement in Floating mode, as fractions of the surface (0..1).

    Ignored in Tiled/Focus/Ambient, where the tree decides. Fractions rather than
    pixels so a layout saved on one screen size still means something on another.
    """

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


class Panel(BaseModel):
    """One panel in a layout: a stable id, its content type, optional placement."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.:-]+$")
    type: str = Field(min_length=1)
    rect: Optional[Rect] = None
    #: A pinned panel survives JARVIS's rearrangements (the person's choice wins).
    pinned: bool = False
    #: Which orbital band the panel sits on, overriding its type's resting band
    #: (HUD.md §6.2). `None` means "wherever the type belongs", which is what
    #: almost every panel says; a band here is JARVIS or the person having moved
    #: it — bringing a task's panels inward, or pushing finished work outward.
    band: Optional[Band] = None


class Split(BaseModel):
    """An interior node of the Tiled tree: two children side by side or stacked."""

    model_config = ConfigDict(extra="forbid")

    split: Literal["row", "column"]
    #: Share of the space the first child gets.
    ratio: float = Field(default=0.5, gt=0, lt=1)
    children: list[LayoutNode] = Field(min_length=2, max_length=2)


#: A leaf is a panel id; an interior node is a :class:`Split`.
LayoutNode = Union[str, Split]


class LayoutDocument(BaseModel):
    """The saved arrangement of one surface for one activity.

    ``tree`` is the tiling tree (or ``None`` for an empty/ambient layout); every
    leaf must name a panel in ``panels`` and no panel id may repeat, so a surface
    never has to render a reference to a panel that does not exist.
    """

    model_config = ConfigDict(extra="forbid")

    mode: HudMode
    tree: Optional[LayoutNode] = None
    panels: list[Panel] = Field(default_factory=list)

    @model_validator(mode="after")
    def _leaves_name_panels(self) -> LayoutDocument:
        ids = [p.id for p in self.panels]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate panel id(s): {dupes}")
        known = set(ids)
        unknown = sorted(leaf for leaf in _leaves(self.tree) if leaf not in known)
        if unknown:
            raise ValueError(f"tree refers to panel(s) not in panels: {unknown}")
        return self


def _leaves(node: Optional[LayoutNode]) -> list[str]:
    if node is None:
        return []
    if isinstance(node, str):
        return [node]
    return [leaf for child in node.children for leaf in _leaves(child)]


Split.model_rebuild()

#: ``hud.layouts``: surface → activity → layout document.
HudLayouts = dict[Surface, dict[str, LayoutDocument]]

#: The named shapes an ``OBJECT`` setting may be validated against.
SHAPES: dict[str, type[BaseModel]] = {"hud_layouts": LayoutDocument}


# --------------------------------------------------------------------------- #
# The descriptor row every generator reads, and the field helper that declares one.
# --------------------------------------------------------------------------- #
class Setting(BaseModel):
    """One setting's full description.

    ``key`` is the canonical identity (unique across the schema) and ``field`` the
    attribute name on :class:`Settings`. ``env`` is the ``.env`` variable name when
    the setting is seeded from the environment, and ``hermes_key`` is the
    ``config.yaml`` dotted path when it lands in Hermes' own config; a setting may
    have one, the other, or both. ``settable`` marks the runtime-changeable
    allow-list the ``aura-settings`` plugin serves; ``advanced`` marks a
    fine-grained knob the settings screens fold away by default.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    field: str
    env: Optional[str] = None
    hermes_key: Optional[str] = None
    kind: Kind
    default: Any = None
    description: str
    group: Group
    widget: Widget
    secret: bool = False
    restart_needed: bool = False
    settable: bool = False
    advanced: bool = False
    #: Which tier of the settings UI this row appears in (ADR-0035). It is a
    #: field here rather than a list beside the schema so that a setting cannot
    #: be added without landing somewhere.
    tier: Tier = "advanced"
    choices: Optional[tuple[str, ...]] = None
    min: Optional[Union[int, float]] = None
    max: Optional[Union[int, float]] = None
    #: For ``OBJECT``: the :data:`SHAPES` name each value of the mapping validates against.
    shape: Optional[str] = None

    @model_validator(mode="after")
    def _coherent(self) -> Setting:
        # A schema row that lies about itself is a defect in this file, so fail at
        # import rather than let a generator emit a contract nothing can satisfy.
        if self.kind is Kind.ENUM and not self.choices:
            raise ValueError(f"{self.key}: an enum setting needs choices")
        if self.secret and self.default not in ("", None):
            raise ValueError(f"{self.key}: a secret must not carry a real default")
        if self.env is None and self.hermes_key is None and not self.settable:
            raise ValueError(f"{self.key}: a setting lands nowhere (no env, no hermes_key, not settable)")
        if self.choices is not None and self.default is not None and self.default not in self.choices:
            raise ValueError(f"{self.key}: default {self.default!r} is not one of {self.choices}")
        if self.kind is Kind.OBJECT and self.shape not in SHAPES:
            raise ValueError(f"{self.key}: an object setting needs a known shape, got {self.shape!r}")
        return self

    @property
    def contract_type(self) -> str:
        """The type string the ``jsettings`` contract and plugin speak.

        The plugin's allow-list uses ``string``/``int``/``float``/``bool``/``enum``/
        ``list``/``object``; ``url``/``path`` are strings there.
        """
        if self.kind in (Kind.URL, Kind.PATH):
            return "string"
        return self.kind.value


def _widget_for(kind: Kind, secret: bool) -> Widget:
    if secret:
        return Widget.SECRET
    return {
        Kind.STRING: Widget.TEXT,
        Kind.URL: Widget.URL,
        Kind.PATH: Widget.PATH,
        Kind.INT: Widget.NUMBER,
        Kind.FLOAT: Widget.NUMBER,
        Kind.BOOL: Widget.TOGGLE,
        Kind.ENUM: Widget.SELECT,
        Kind.LIST: Widget.MULTISELECT,
        Kind.OBJECT: Widget.MANAGED,
    }[kind]


def setting(
    key: str,
    *,
    group: Group,
    description: str,
    default: Any = None,
    env: Optional[str] = None,
    hermes_key: Optional[str] = None,
    kind: Optional[Kind] = None,
    secret: bool = False,
    restart_needed: bool = False,
    settable: bool = False,
    advanced: bool = False,
    min: Optional[float] = None,
    max: Optional[float] = None,
    widget: Optional[Widget] = None,
    shape: Optional[str] = None,
    tier: Tier = "advanced",
) -> Any:
    """Declare one :class:`Settings` field.

    The field's *type* is its annotation on the class (a ``Literal`` is an enum
    whose choices are the literal's values); ``kind`` only overrides it for the
    textual hints ``URL``/``PATH``. The rest is the metadata the generators read,
    carried on the ``FieldInfo`` so the class is the single declaration.
    """
    if secret and default is None:
        default = ""
    aliases = list(dict.fromkeys(a for a in (env, key, hermes_key) if a))
    meta: dict[str, Any] = {
        "key": key, "env": env, "hermes_key": hermes_key, "kind": kind, "group": group,
        "description": description, "secret": secret, "restart_needed": restart_needed,
        "settable": settable, "advanced": advanced, "min": min, "max": max, "widget": widget,
        "shape": shape, "tier": tier,
    }
    bounds: dict[str, Any] = {}
    if min is not None:
        bounds["ge"] = min
    if max is not None:
        bounds["le"] = max
    if isinstance(default, (list, dict)):
        # A fresh copy per instance, so one Settings() cannot mutate another's default.
        return Field(
            default_factory=lambda d=default: type(d)(d),
            validation_alias=AliasChoices(*aliases),
            serialization_alias=env or key,
            description=description,
            json_schema_extra=cast(Any, {"jarvis": meta}),  # enums inside: not JSON, and never emitted
            **bounds,
        )
    return Field(
        default,
        validation_alias=AliasChoices(*aliases),
        serialization_alias=env or key,
        description=description,
        json_schema_extra=cast(Any, {"jarvis": meta}),  # enums inside: not JSON, and never emitted
        **bounds,
    )


#: Cache for :func:`_list_field_aliases` — the model's fields never change at
#: runtime, so the alias walk happens once rather than on every ``from_env()``.
_LIST_ALIAS_CACHE: dict[int, tuple[str, ...]] = {}


def _list_field_aliases(model: type[BaseModel]) -> tuple[str, ...]:
    """Every name a ``list[str]`` field of *model* can be addressed by.

    A field's aliases are its ``.env`` variable, its canonical key and its Hermes
    key (whichever were declared), so a value arriving under any of them is the
    same setting. Used by the comma-splitting validator; nothing else needs it.
    """
    cached = _LIST_ALIAS_CACHE.get(id(model))
    if cached is not None:
        return cached
    names: list[str] = []
    for name, info in model.model_fields.items():
        if get_origin(_unwrap_optional(info.annotation)) is not list:
            continue
        choices = getattr(info.validation_alias, "choices", None) or ()
        names.extend(str(c) for c in choices)
        names.append(name)
    out = tuple(dict.fromkeys(names))
    _LIST_ALIAS_CACHE[id(model)] = out
    return out


# --------------------------------------------------------------------------- #
# The model — one field per setting, grouped as the UI shows them.
#
# The descriptions of the eight original ``settable=True`` rows are the exact
# strings the ``jsettings`` contract has always carried; the console's mock and
# the plugin tests compare them character for character.
# --------------------------------------------------------------------------- #
class Settings(BaseModel):
    """Every JARVIS setting, typed. Build one with :func:`from_env`.

    Unknown keys are rejected (``extra="forbid"``), so a typo in a config fragment
    or a ``jsettings`` payload is an error naming the key, not a silently ignored
    field. Each field validates from its ``.env`` name, its dotted key or its
    Hermes key, so the same model reads an environment mapping and a config
    fragment.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_default=False)

    @model_validator(mode="before")
    @classmethod
    def _split_comma_separated_lists(cls, data: Any) -> Any:
        """A list setting seeded from ``.env`` arrives as one comma-separated string.

        ``TELEGRAM_ALLOWED_USERS=111,222`` is the shape Hermes' gateway itself parses
        (``gateway/authz_mixin.py`` splits the variable on commas), so an environment
        can only ever carry the joined form. Without this the field would fail
        validation with pydantic's "input should be a valid list" and the *variable's*
        name would be lost in a message about a type — the failure this repo's fail-fast
        rule exists to avoid. Splitting here keeps the declared type a list, which is
        what the settings screens render a multi-value editor from.
        """
        if not isinstance(data, Mapping):
            return data
        aliases = _list_field_aliases(cls)
        if not any(isinstance(data.get(a), str) for a in aliases):
            return data
        out = dict(data)
        for alias in aliases:
            value = out.get(alias)
            if isinstance(value, str):
                out[alias] = [part.strip() for part in value.split(",") if part.strip()]
        return out

    # ---- Model / routing ------------------------------------------------- #
    model_default: str = setting(
        "model.default", group=Group.MODEL, env="LLM_MODEL", hermes_key="model.default", default="",
        settable=True, tier="basic", description="The model every conversation runs on")
    model_base_url: str = setting(
        "model.base_url", group=Group.MODEL, env="LLM_URL", hermes_key="model.base_url", kind=Kind.URL,
        default="", restart_needed=True,
        description="OpenAI-compatible base URL of the model server the agent talks to.")
    model_context_length: int = setting(
        "model.context_length", group=Group.MODEL, env="LLM_CONTEXT_LENGTH",
        hermes_key="model.context_length", default=262144, min=1, restart_needed=True,
        description="Token context window the provider advertises.")
    model_api_key: str = setting(
        "model.api_key", group=Group.MODEL, env="LLM_API_KEY", secret=True, restart_needed=True,
        description="The inference key of the model server: llama-swap in jarvis-models answers only "
                    "this bearer, and Hermes, the adapter and the house gateway present it. Unset, "
                    "llama-swap stays open to the host's containers.")
    llama_swap_url: str = setting(
        "llama_swap.url", group=Group.MODEL, env="LLAMA_SWAP_URL", kind=Kind.URL, default="",
        description="Base URL of the llama-swap model router (model load/unload control).")
    llama_swap_timeout: float = setting(
        "llama_swap.timeout", group=Group.MODEL, env="LLAMA_SWAP_TIMEOUT", default=900.0, min=1,
        advanced=True,
        description="Seconds a llama-swap request may take before it is abandoned (cold start is slow).")

    # ---- Model routing table (D21) --------------------------------------- #
    # One row per kind of work. ``model.default`` above is the chat/default row; each
    # row here names the model that kind of work runs on, and **empty means "the
    # default model"** — no route, no swap. That is the measured position on this
    # hardware: llama-swap serves qwen3.8-27b and qwen3.6-35b, the 27b is started with
    # --gpu-memory-utilization 0.92 across both 3090s, so nothing co-resides with it and
    # every route between models is a cold start in the agent's hot path. The benchmark
    # report's swap column is the evidence for changing one of these (V2_PLAN §6/§9.1).
    # Thinking stays on (ADR-0002) but bounded. Hermes sends NO reasoning_effort unless this is
    # set, and vLLM's default for qwen3.8-27b is then unbounded: "set up an alert if the GPU goes
    # above 80 degrees" produced 4000 tokens of reasoning and no answer (finish_reason=length),
    # and alert-gpu-002 / alert-vram-007 timed out in every benchmark run including the baseline.
    # With an explicit "medium" the same prompt reasons for ~1000 tokens and answers.
    # The entrypoint renders an unset variable as "", and "" is what Hermes reads as "send
    # nothing" — the unbounded case this exists to close — so docker-compose.yml carries the
    # default (`${LLM_REASONING_EFFORT:-medium}`) rather than trusting the host's .env to.
    model_reasoning_effort: ReasoningEffort = setting(
        "model.reasoning_effort", group=Group.MODEL, env="LLM_REASONING_EFFORT",
        hermes_key="agent.reasoning_effort", default="medium", restart_needed=True,
        description="How long JARVIS thinks before answering. \"none\" turns thinking off; the "
                    "levels above bound it. Unset, the model server decides, and on this "
                    "hardware that means it can think past every task's time budget.")
    routing_reasoning_model: str = setting(
        "routing.reasoning_model", group=Group.MODEL, env="ROUTING_REASONING_MODEL", default="",
        restart_needed=True, advanced=True,
        description="Model for reasoning-heavy work. Empty means the default model answers "
                    "(no swap); naming another model asks JARVIS to load it first.")
    routing_coding_model: str = setting(
        "routing.coding_model", group=Group.MODEL, env="ROUTING_CODING_MODEL", default="",
        restart_needed=True, advanced=True,
        description="Model for writing and fixing code. Empty means the default model answers "
                    "(no swap); naming another model asks JARVIS to load it first.")
    routing_vision_model: str = setting(
        "routing.vision_model", group=Group.MODEL, env="ROUTING_VISION_MODEL",
        hermes_key="auxiliary.vision.model", default="", restart_needed=True,
        description="Model that reads images and screenshots. Empty means the default model, "
                    "which serves vision_analyze on this deployment.")
    routing_auxiliary_model: str = setting(
        "routing.auxiliary_model", group=Group.MODEL, env="ROUTING_AUXILIARY_MODEL",
        hermes_key="auxiliary.compression.model", default="", restart_needed=True,
        description="Model for JARVIS' side calls — compression, titles, the goal judge, the "
                    "skill curator. Empty means the default model; a smaller model here only "
                    "pays off if llama-swap can keep it resident alongside the default one.")
    routing_delegation_model: str = setting(
        "routing.delegation_model", group=Group.MODEL, env="ROUTING_DELEGATION_MODEL",
        hermes_key="delegation.model", default="", restart_needed=True,
        description="Model the delegated subagents run on. Empty means they inherit the "
                    "parent's model, which is the only zero-swap answer while one model fits.")
    routing_embedding_model: str = setting(
        "routing.embedding_model", group=Group.MODEL, env="ROUTING_EMBEDDING_MODEL", default="",
        advanced=True,
        description="Embedding model for retrieval, served by ROUTING_EMBEDDING_URL. Empty is "
                    "the deployed position: the memory provider searches with SQLite FTS5 and "
                    "needs no embeddings. llama-swap's qwen3-embedding-0.6b (on the CPU) is one to point at.")
    routing_embedding_url: str = setting(
        "routing.embedding_url", group=Group.MODEL, env="ROUTING_EMBEDDING_URL", kind=Kind.URL,
        default="", advanced=True,
        description="Base URL of a local OpenAI-compatible embeddings endpoint. Must stay on "
                    "the LAN: a cloud embedding service would send memory off the house.")
    # Not a queue depth: Hermes *refuses* a batch bigger than this rather than running it in
    # waves. At 3 a four-part request answered "Too many tasks: 4 provided, but
    # max_concurrent_children is 3"; the agent recovered by splitting into three calls, but it
    # spent a tool call learning the cap, so the default sits above an ordinary multi-part
    # request. The ceiling is the shared model server, which serves one 27b for the whole house.
    # 24, not 10: Hermes has no ceiling of its own (above 10 it logs one warning per process
    # about cost, which a local server does not pay), and the delegation proof runs twenty.
    routing_delegation_max_concurrent: int = setting(
        "routing.delegation_max_concurrent", group=Group.MODEL,
        env="ROUTING_DELEGATION_MAX_CONCURRENT", hermes_key="delegation.max_concurrent_children",
        default=5, min=1, max=24,
        description="Largest batch of delegated subagents. A request for more than this many "
                    "children is refused, not queued, so keep it above the size of an ordinary "
                    "multi-part request; they all share one model server.")
    # Hermes' own default is 1 (flat: a child cannot delegate) and it has no ceiling. 2 is
    # what lets a subagent run its own workers; 3 is the most the one model server should
    # be asked to answer at once, since every level multiplies the batch above it.
    routing_delegation_max_depth: int = setting(
        "routing.delegation_max_depth", group=Group.MODEL,
        env="ROUTING_DELEGATION_MAX_DEPTH", hermes_key="delegation.max_spawn_depth",
        default=2, min=1, max=3,
        description="How deep delegation nests: 1 means a subagent cannot delegate, 2 lets a "
                    "subagent run children of its own. Every level multiplies what the one "
                    "model server answers at once.")
    # Hermes offers six memory providers; five of them post the user's facts to a service
    # (mem0, honcho, hindsight cloud, retaindb, byterover), so only the two that stay in
    # the volume are choices here. Widening this Literal would be a "fully local" breach.
    # No ``env``: the entrypoint's renderer has no default syntax, so an unset variable
    # renders empty — and empty here means "no structured store", which would be a silent
    # loss of memory on any host whose .env predates this row. The config template carries
    # the value literally and aura-schema's tests hold the two in step.
    memory_provider: Literal["", "holographic"] = setting(
        "memory.provider", group=Group.MODEL, hermes_key="memory.provider",
        default="holographic", restart_needed=True,
        description="Structured long-term memory behind the memory tools. 'holographic' is a "
                    "local SQLite fact store in the JARVIS volume; empty keeps only "
                    "MEMORY.md/USER.md.")

    # ---- Voice ----------------------------------------------------------- #
    stt_model: str = setting(
        "stt.model", group=Group.VOICE, env="WHISPER_MODEL", hermes_key="stt.local.model",
        default="small", restart_needed=True,
        description="faster-whisper model size for speech-to-text (baked into the image).")
    tts_voice: str = setting(
        "tts.voice", group=Group.VOICE, env="PIPER_VOICE", hermes_key="tts.piper.voice",
        default="en_GB-alan-medium", restart_needed=True,
        tier="basic", description="Piper voice the assistant speaks with (baked into the image).")
    # Hermes' own TTS: the shim by default (ADR-0039), Piper as the way back.
    # These four render into deploy/config.template.yaml's `tts:` block; the
    # entrypoint pins `tts.provider` from the env on every start, so the .env
    # line is the switch, not a first-start default.
    tts_provider: Literal["openai", "piper"] = setting(
        "tts.provider", group=Group.VOICE, env="TTS_PROVIDER", hermes_key="tts.provider",
        default="openai", restart_needed=True,
        description="Which engine Hermes speaks with: openai (the house voice service, "
                    "Kokoro on the card) or piper (one voice, no GPU).")
    tts_shim_url: str = setting(
        "tts.shim_url", group=Group.VOICE, env="TTS_OPENAI_BASE_URL", hermes_key="tts.openai.base_url",
        kind=Kind.URL, default="http://jarvis-cosyvoice:8890/v1", restart_needed=True, advanced=True,
        description="The voice service's OpenAI-shaped base URL, as Hermes reaches it on the compose network.")
    tts_shim_model: str = setting(
        "tts.shim_model", group=Group.VOICE, env="TTS_OPENAI_MODEL", hermes_key="tts.openai.model",
        default="kokoro", restart_needed=True, advanced=True,
        description="The model name the voice service answers to; it accepts one.")
    tts_primary_voice: str = setting(
        "tts.primary_voice", group=Group.VOICE, env="TTS_VOICE", hermes_key="tts.openai.voice",
        default="af_heart", restart_needed=True, advanced=True,
        description="The voice Hermes' own speech uses (its platforms' voice notes): the default "
                    "theme's, AURA's Kokoro stock voice; the console and the apps ask the adapter "
                    "for the speaking persona's voice instead.")
    tts_app_voice: str = setting(
        "tts.app_voice", group=Group.VOICE, env="AURA_TTS_VOICE", default="en_GB-alan-medium",
        description="Piper voice the phone/desktop apps ask the pipeline to speak with.")
    tts_engine: Literal["wyoming", "openai", "cosyvoice"] = setting(
        "tts.engine", group=Group.VOICE, env="AURA_TTS_ENGINE", default="wyoming",
        restart_needed=True,
        tier="basic", description="Which speech service speaks: wyoming (Piper, the default), openai "
                    "(a Kokoro-shaped /v1/audio/speech) or cosyvoice (the house voice service, Kokoro).")
    tts_url: str = setting(
        "tts.url", group=Group.VOICE, env="AURA_TTS_URL", kind=Kind.URL, default="",
        restart_needed=True, advanced=True,
        description="Base URL of the speech service when tts.engine is openai or cosyvoice; "
                    "empty means the engine's own default port on this host.")
    voice_vad_hangover_ms: int = setting(
        "voice.vad_hangover_ms", group=Group.VOICE, env="AURA_VAD_HANGOVER_MS", default=550,
        min=200, max=3000,
        description="Milliseconds of silence before a spoken turn is considered finished.")
    voice_push_to_talk: bool = setting(
        "voice.push_to_talk", group=Group.VOICE, env="AURA_PUSH_TO_TALK", default=False,
        tier="basic", description="Whether the apps require holding a button to talk instead of open-mic.")
    # Read by the adapter on every speak-pcm call (jarvis-adapter config.voice_speech), so a name
    # taught by voice is said right in the next sentence (T-0587).
    voice_pronunciations: str = setting(
        "voice.pronunciations", group=Group.VOICE, env="AURA_PRONUNCIATIONS", default="",
        settable=True,
        description="How JARVIS says names it would get wrong, as written=spoken pairs separated by "
                    "semicolons, e.g. \"Siobhan=shi-vawn; Nguyen=win\". Whole words, any case; the "
                    "transcript keeps the written name.")
    voice_quiet_hours: str = setting(
        "voice.quiet_hours", group=Group.VOICE, env="AURA_QUIET_HOURS", default="23:00-07:00",
        settable=True,
        description="When JARVIS speaks softer and a little slower, as HH:MM-HH:MM in the house's "
                    "time (it may wrap midnight); empty for never.")

    # The first spoken word waits for the reasoning tokens (bench/results/model-first-token.md),
    # so a spoken turn has a level of its own. No hermes_key: Hermes takes the effort per
    # session over the HUD socket and the adapter sends it ahead of each voice prompt
    # (jarvis-adapter effort.py), reading this value from the override file on every prompt,
    # so a change by voice applies on the next turn. Set it equal to model.reasoning_effort
    # to turn the difference off -- the default since T-0814. Once the level reached the
    # model at all, `low` turned out to put a line at the top of its system prompt: a spoken
    # turn at low after typed ones at medium shared no cached prefix and re-read the whole
    # conversation (22 s to the first token on a 30k-token prompt, against 0.6 s warm), and
    # it reasoned no less than medium on short questions. `none` keeps the cache (it only
    # closes the think block at the end) and is the measured lever, a quality call not yet
    # made (bench/results/reasoning-effort.md).
    voice_reasoning_effort: ReasoningEffort = setting(
        "voice.reasoning_effort", group=Group.VOICE, env="VOICE_REASONING_EFFORT", default="medium",
        settable=True,
        description="How long JARVIS thinks before answering a spoken question (car, phone, "
                    "console mic). \"none\" is faster to the first word; a level that differs from "
                    "model.reasoning_effort other than none makes a spoken turn re-read the "
                    "conversation after a typed one.")

    # ---- Agent behaviour ------------------------------------------------- #
    goals_max_turns: int = setting(
        "goals.max_turns", group=Group.AGENT, env="GOALS_MAX_TURNS", hermes_key="goals.max_turns",
        default=20, min=1, max=200, settable=True,
        description="Turns a standing goal may take before it parks")
    tool_loop_guardrails_hard_stop_enabled: bool = setting(
        "tool_loop_guardrails.hard_stop_enabled", group=Group.AGENT,
        hermes_key="tool_loop_guardrails.hard_stop_enabled", default=True, settable=True,
        description="Stop a turn whose tool calls loop without progress")
    tool_loop_guardrails_loop_caps_max_web_searches: int = setting(
        "tool_loop_guardrails.loop_caps.max_web_searches", group=Group.AGENT,
        hermes_key="tool_loop_guardrails.loop_caps.max_web_searches", default=50, min=1, max=200,
        settable=True, description="Web searches one turn may make")

    # ---- Approvals ------------------------------------------------------- #
    # `manual` by default since 2026-09-10 (the owner: "fix/find all security
    # issues"): `smart` let an auxiliary model answer Hermes' dangerous-command
    # prompts on its own, so a shell command the guard flagged could run with
    # nobody in the house asked (V8 in the overhaul audit). The JARVIS approval
    # matrix (aura-guard) gates tools either way; this is the second door.
    approvals_mode: Literal["manual", "smart", "off"] = setting(
        "approvals.mode", group=Group.APPROVALS, hermes_key="approvals.mode", default="manual",
        settable=True,
        tier="basic", description="When the approval door opens: manual = every gated tool asks, "
                    "smart = an auxiliary model decides for you, off = never")
    # Hermes keeps an `always` answer for ever; the guard's token in the rule key makes it run
    # out (hermes/plugins/_shared/_grants.py, T-0570). Read by the plugin, so no hermes_key.
    approvals_always_ttl_days: int = setting(
        "approvals.always_ttl_days", group=Group.APPROVALS, env="AURA_ALWAYS_TTL_DAYS",
        default=30, min=1, max=365, advanced=True,
        description="Days a standing \"always\" approval lasts before JARVIS asks again. "
                    "Every live one is listed under Approvals, where it can be revoked sooner.")

    # Where the model's shell runs (ADR-0045). Env-backed, like tts.provider, because the
    # entrypoint pins `terminal.backend` from it on every start: a volume rendered when
    # the answer was `local` would otherwise keep it forever, and a pinned leaf must
    # have an owner outside the volume. `advanced`: not a deploy .env line, a way back.
    terminal_backend: Literal["aura-bwrap", "local"] = setting(
        "terminal.backend", group=Group.APPROVALS, env="AURA_TERMINAL_BACKEND",
        hermes_key="terminal.backend", default="aura-bwrap", restart_needed=True, advanced=True,
        description="Where shell commands and execute_code run: aura-bwrap = a bubblewrap "
                    "sandbox in the container (no network, no environment, one scratch per "
                    "task), local = the gateway process itself, with every credential it holds.")

    approvals_work_root: str = setting(
        "approvals.work_root", group=Group.APPROVALS, env="AURA_WORK_ROOT", kind=Kind.PATH,
        default="/opt/data/work", advanced=True,
        description="The directory a coding job is allowed to write in. The approval matrix "
                    "resolves a tool's paths against it, and anything landing outside is a "
                    "write beyond the workspace, which asks before it happens.")

    # ---- The GPU watchdog's thresholds, and when JARVIS holds its tongue --- #
    gpu_temp_alert_c: float = setting(
        "gpu.temp_alert_c", group=Group.HOMELAB, env="GPU_TEMP_ALERT_C", default=80.0, min=0,
        description="GPU temperature (°C) at which the watchdog raises an alert.")
    gpu_temp_clear_c: float = setting(
        "gpu.temp_clear_c", group=Group.HOMELAB, env="GPU_TEMP_CLEAR_C", default=78.0, min=0,
        description="GPU temperature the alert clears under. Below the alert point on purpose: "
                    "without the gap a card sitting on the limit alternates alert and recovered "
                    "on every tick.")
    gpu_vram_alert_pct: float = setting(
        "gpu.vram_alert_pct", group=Group.HOMELAB, env="GPU_VRAM_ALERT_PCT", default=95.0, min=0,
        description="VRAM in use (%) at which the watchdog raises an alert.")
    gpu_vram_clear_pct: float = setting(
        "gpu.vram_clear_pct", group=Group.HOMELAB, env="GPU_VRAM_CLEAR_PCT", default=90.0, min=0,
        description="VRAM percentage the alert clears under, with the same hysteresis as the "
                    "temperature pair.")
    gpu_alert_renotice_s: float = setting(
        "gpu.alert_renotice_s", group=Group.HOMELAB, env="GPU_ALERT_RENOTICE_S", default=3600.0,
        min=0, advanced=True,
        description="How long the watchdog stays quiet between reminders while a threshold is "
                    "still crossed.")
    proactive_quiet_hours_from: int = setting(
        "proactive.quiet_hours_from", group=Group.AGENT, env="PROACTIVE_QUIET_HOURS_FROM",
        default=22, min=0, max=23,
        tier="basic", description="Hour (house local time) quiet hours begin. Scheduled digests and recovery "
                    "notices hold until they end; a threshold alert still fires.")
    proactive_deliver: str = setting(
        "proactive.deliver", group=Group.AGENT, env="PROACTIVE_DELIVER", default="",
        description="Where every shipped scheduled job delivers. Empty means the house "
                    "companion; `telegram`, `discord` or `platform:chat_id` send it to a "
                    "messaging platform instead.")
    proactive_quiet_hours_to: int = setting(
        "proactive.quiet_hours_to", group=Group.AGENT, env="PROACTIVE_QUIET_HOURS_TO", default=7,
        min=0, max=23,
        description="Hour quiet hours end. Equal to quiet_hours_from means there are none.")

    # ---- Tools ----------------------------------------------------------- #
    tools_enabled: list[str] = setting(
        "tools.enabled", group=Group.TOOLS, hermes_key="tools.enabled", default=[], settable=True,
        description="Toolsets on for new sessions")
    tools_disabled: list[str] = setting(
        "tools.disabled", group=Group.TOOLS, hermes_key="tools.disabled", default=[], settable=True,
        description="Toolsets off for new sessions")
    image_gen_provider: Optional[str] = setting(
        "image_gen.provider", group=Group.TOOLS, hermes_key="image_gen.provider", default=None,
        settable=True, description="Which image backend image_generate uses")

    # ---- House (jarvis-core, HA-shaped) ---------------------------------- #
    house_url: str = setting(
        "house.url", group=Group.HOUSE, env="HASS_URL", kind=Kind.URL, default="http://127.0.0.1:8080",
        description="Base URL of jarvis-core's Home-Assistant-shaped REST API (loopback when it shares the host).")
    house_token: str = setting(
        "house.token", group=Group.HOUSE, env="HASS_TOKEN", secret=True, restart_needed=True,
        description="Long-lived token for jarvis-core's REST API (the 'house').")
    emergency_contacts_secret: str = setting(
        "emergency.contacts_secret", group=Group.HOUSE, env="AURA_EMERGENCY_SECRET", secret=True,
        default="", restart_needed=True,
        description="Secret the emergency plugin and the house share: the house keeps a new emergency "
                    "contact list only with the plugin's proof under it (T-1213). Empty: the house "
                    "refuses every change to the list.")
    emergency_fallback_url: str = setting(
        "emergency.fallback_url", group=Group.HOUSE, env="AURA_EMERGENCY_FALLBACK_URL", secret=True,
        default="", restart_needed=True, advanced=True,
        description="Where the house itself posts an emergency alarm (what, kind, time; no contact "
                    "details) when the phone cannot send it: an ntfy topic or n8n webhook URL (T-1208). "
                    "Empty: nothing leaves the house. The flow behind it should confirm with you before "
                    "telling anyone.")
    house_console_url: str = setting(
        "house.console_url", group=Group.HOUSE, env="AURA_URL", kind=Kind.URL, default="",
        description="jarvis-core base URL the console uses for the house pages.")
    house_console_token: str = setting(
        "house.console_token", group=Group.HOUSE, env="AURA_TOKEN", secret=True, restart_needed=True,
        description="jarvis-core access token the console and HUD present.")
    privacy_quiet_house: bool = setting(
        "privacy.quiet_house", group=Group.HOUSE, env="AURA_QUIET_HOUSE", default=False, settable=True,
        restart_needed=True,
        description="One privacy switch: the house makes no call out on its own (sky downloads, "
                    "a scheduled forecast, a trip's map region); what you ask for still works.")

    # ---- Homelab plugins ------------------------------------------------- #
    searxng_url: str = setting(
        "searxng.url", group=Group.HOMELAB, env="SEARXNG_URL", kind=Kind.URL, default="",
        description="SearXNG endpoint the web toolset searches through.")
    searxng_secret: str = setting(
        "searxng.secret", group=Group.HOMELAB, env="SEARXNG_SECRET", secret=True,
        description="SearXNG instance secret (openssl rand -hex 32).")
    extract_url: str = setting(
        "extract.url", group=Group.HOMELAB, env="EXTRACT_URL", kind=Kind.URL,
        default="http://jarvis-extract:8896",
        description="Local article-extraction service the web toolset reads pages through. The "
                    "default is the compose service by name, so a stack works without an .env "
                    "edit; there is no cloud fallback, so a wrong value fails naming this "
                    "variable rather than rerouting anywhere.")
    extract_timeout: float = setting(
        "extract.timeout", group=Group.HOMELAB, env="EXTRACT_TIMEOUT", default=45.0, min=1, max=300,
        description="Seconds to wait for the extraction service before giving up on a batch of URLs.")
    extract_enabled: bool = setting(
        "extract.enabled", group=Group.HOMELAB, env="EXTRACT_ENABLED", default=True,
        description="Whether web_extract may use the local extraction service. Off, the tool has no "
                    "extract backend at all — which is the point: it must never silently reroute "
                    "to a cloud provider.")
    places_photon_url: str = setting(
        "places.photon_url", group=Group.HOMELAB, env="PHOTON_URL", kind=Kind.URL,
        default="http://127.0.0.1:2322",
        description="The house's Photon geocoder, which place_search asks first for names and "
                    "addresses near the car (jarvis-core compose `photon`, --profile geocode, with "
                    "PHOTON_REGION set). None running is the ordinary case until one is built.")
    places_photon_public: bool = setting(
        "places.photon_public", group=Group.HOMELAB, env="PHOTON_PUBLIC", default=False,
        description="Whether place_search may fall back to the public Photon (komoot) and "
                    "OpenStreetMap's Nominatim when the house's place index and Photon have no "
                    "answer. On, the search words and a point rounded to about 100 m leave the "
                    "house; the result says so. Off by default (T-0765): where the car is stays in.")
    maps_auto_fetch: bool = setting(
        "maps.auto_fetch", group=Group.HOMELAB, env="AURA_MAPS_AUTO_FETCH", default=True,
        description="Whether car_navigate, for the owner, has the house download the map region a "
                    "route ends in when no offline map covers it (T-0979), so the car's map and "
                    "turn-by-turn work there with no connection. On, Geofabrik hears that region's "
                    "name (a state, a country), never the destination. Off, a region is downloaded "
                    "only when asked for (map_region_download, or the phone's Maps settings).")
    places_overpass_url: str = setting(
        "places.overpass_url", group=Group.HOMELAB, env="OVERPASS_URL", kind=Kind.URL,
        default="",
        description="An Overpass server place_search may ask for 'the nearest' of a kind (a cafe, "
                    "fuel, a pharmacy) within 5 km of the car when the house's place index has "
                    "none. Empty by default (T-0765): the kind and a rounded point would leave the "
                    "house for a public one.")
    comfyui_url: str = setting(
        "comfyui.url", group=Group.HOMELAB, env="COMFYUI_URL", kind=Kind.URL, default="",
        description="ComfyUI endpoint the image_gen plugin submits workflows to.")
    comfyui_verify_ssl: bool = setting(
        "comfyui.verify_ssl", group=Group.HOMELAB, env="COMFYUI_VERIFY_SSL", default=False,
        description="Whether to verify TLS when calling ComfyUI (the homelab's sits behind a self-signed front).")
    comfyui_checkpoint: str = setting(
        "comfyui.checkpoint", group=Group.HOMELAB, env="COMFYUI_CHECKPOINT", default="",
        description="ComfyUI checkpoint name to load, when the workflow takes one.")
    comfyui_workflow: str = setting(
        "comfyui.workflow", group=Group.HOMELAB, env="COMFYUI_WORKFLOW", default="txt2img",
        description="Named ComfyUI workflow the image_gen plugin fills in (txt2img, qwen-image, or a path).")
    comfyui_negative: str = setting(
        "comfyui.negative", group=Group.HOMELAB, env="COMFYUI_NEGATIVE",
        default="blurry, low quality, watermark", advanced=True,
        description="Negative prompt filled into the workflow when the caller gives none.")
    comfyui_steps: Optional[int] = setting(
        "comfyui.steps", group=Group.HOMELAB, env="COMFYUI_STEPS", default=None, min=1, max=200,
        advanced=True,
        description="Sampler steps when the caller gives none; unset means the workflow's own default.")
    comfyui_cfg: Optional[float] = setting(
        "comfyui.cfg", group=Group.HOMELAB, env="COMFYUI_CFG", default=None, min=0, max=30, advanced=True,
        description="Classifier-free guidance scale; unset means the workflow's own default.")
    comfyui_shift: float = setting(
        "comfyui.shift", group=Group.HOMELAB, env="COMFYUI_SHIFT", default=3.1, min=0, advanced=True,
        description="AuraFlow sampling shift for the Qwen-Image graph (ComfyUI's template value).")
    comfyui_unet: str = setting(
        "comfyui.unet", group=Group.HOMELAB, env="COMFYUI_UNET", default="", advanced=True,
        description="Diffusion model file for the Qwen-Image graph; empty means the plugin's shipped default.")
    comfyui_clip: str = setting(
        "comfyui.clip", group=Group.HOMELAB, env="COMFYUI_CLIP", default="", advanced=True,
        description="Text encoder file for the Qwen-Image graph; empty means the plugin's shipped default.")
    comfyui_vae: str = setting(
        "comfyui.vae", group=Group.HOMELAB, env="COMFYUI_VAE", default="", advanced=True,
        description="VAE file for the Qwen-Image graph; empty means the plugin's shipped default.")
    comfyui_min_vram_gb: float = setting(
        "comfyui.min_vram_gb", group=Group.HOMELAB, env="COMFYUI_MIN_VRAM_GB", default=4.0, min=0,
        advanced=True,
        description="Free VRAM (GB) required before a generation is submitted; below it the tool refuses with the fix named.")
    comfyui_timeout: float = setting(
        "comfyui.timeout", group=Group.HOMELAB, env="COMFYUI_TIMEOUT", default=600.0, min=1, advanced=True,
        description="Seconds to wait for a ComfyUI prompt to finish.")
    grafana_url: str = setting(
        "grafana.url", group=Group.HOMELAB, env="GRAFANA_URL", kind=Kind.URL, default="",
        description="Grafana base URL for GPU/telemetry reads.")
    grafana_token: str = setting(
        "grafana.token", group=Group.HOMELAB, env="GRAFANA_TOKEN", secret=True,
        description="Grafana service-account token.")
    grafana_influx_datasource: str = setting(
        "grafana.influx_datasource", group=Group.HOMELAB, env="GRAFANA_INFLUX_DS", default="",
        description="Grafana datasource UID (or name) for the InfluxDB telemetry queries; empty picks the first.")
    grafana_verify_ssl: bool = setting(
        "grafana.verify_ssl", group=Group.HOMELAB, env="GRAFANA_VERIFY_SSL", default=True, advanced=True,
        description="Whether to verify TLS when calling Grafana.")
    influx_url: str = setting(
        "influx.url", group=Group.HOMELAB, env="INFLUX_URL", kind=Kind.URL, default="",
        description="InfluxDB base URL for telemetry reads; set, it is preferred over the Grafana proxy.")
    influx_token: str = setting(
        "influx.token", group=Group.HOMELAB, env="INFLUX_TOKEN", secret=True,
        description="InfluxDB API token.")
    influx_org: str = setting(
        "influx.org", group=Group.HOMELAB, env="INFLUX_ORG", default="",
        description="InfluxDB organisation the telemetry bucket lives in.")
    influx_bucket: str = setting(
        "influx.bucket", group=Group.HOMELAB, env="INFLUX_BUCKET", default="proxmox",
        description="InfluxDB bucket Telegraf writes nvidia_smi into.")
    gpu_measurement: str = setting(
        "gpu.measurement", group=Group.HOMELAB, env="GPU_MEASUREMENT", default="nvidia_smi", advanced=True,
        description="Measurement name the GPU exporter writes (nvidia_smi, dcgm, …).")
    gpu_tag: str = setting(
        "gpu.tag", group=Group.HOMELAB, env="GPU_TAG", default="index", advanced=True,
        description="Tag that identifies a GPU in the telemetry rows.")
    gpu_field_util: str = setting(
        "gpu.field_util", group=Group.HOMELAB, env="GPU_FIELD_UTIL", default="utilization_gpu",
        advanced=True, description="Field name for GPU utilisation.")
    gpu_field_vram_used: str = setting(
        "gpu.field_vram_used", group=Group.HOMELAB, env="GPU_FIELD_VRAM_USED", default="memory_used",
        advanced=True, description="Field name for VRAM in use.")
    gpu_field_vram_total: str = setting(
        "gpu.field_vram_total", group=Group.HOMELAB, env="GPU_FIELD_VRAM_TOTAL", default="memory_total",
        advanced=True, description="Field name for total VRAM.")
    gpu_field_temp: str = setting(
        "gpu.field_temp", group=Group.HOMELAB, env="GPU_FIELD_TEMP", default="temperature_gpu",
        advanced=True, description="Field name for GPU temperature.")
    gpu_field_power: str = setting(
        "gpu.field_power", group=Group.HOMELAB, env="GPU_FIELD_POWER", default="power_draw",
        advanced=True, description="Field name for power draw.")
    n8n_url: str = setting(
        "n8n.url", group=Group.HOMELAB, env="N8N_URL", kind=Kind.URL, default="",
        description="n8n base URL the trigger plugin posts webhooks to.")
    n8n_api_key: str = setting(
        "n8n.api_key", group=Group.HOMELAB, env="N8N_API_KEY", secret=True,
        description="n8n API key, when the instance requires one.")
    n8n_verify_ssl: bool = setting(
        "n8n.verify_ssl", group=Group.HOMELAB, env="N8N_VERIFY_SSL", default=True, advanced=True,
        description="Whether to verify TLS when calling n8n.")
    n8n_webhook_auth: str = setting(
        "n8n.webhook_auth", group=Group.HOMELAB, env="N8N_WEBHOOK_AUTH", secret=True, advanced=True,
        description="user:pass for webhooks protected by basic auth.")
    n8n_webhook_header: str = setting(
        "n8n.webhook_header", group=Group.HOMELAB, env="N8N_WEBHOOK_HEADER", secret=True, advanced=True,
        description="'Name: value' header for webhooks protected by a header token.")
    proxmox_url: str = setting(
        "proxmox.url", group=Group.HOMELAB, env="PROXMOX_URL", kind=Kind.URL, default="",
        description="Proxmox VE API base URL (guest control, owner-waived by default).")
    proxmox_token_id: str = setting(
        "proxmox.token_id", group=Group.HOMELAB, env="PROXMOX_TOKEN_ID", secret=True,
        description="Proxmox API token id (user@realm!tokenname).")
    proxmox_token_secret: str = setting(
        "proxmox.token_secret", group=Group.HOMELAB, env="PROXMOX_TOKEN_SECRET", secret=True,
        description="Proxmox API token secret.")
    proxmox_verify_ssl: bool = setting(
        "proxmox.verify_ssl", group=Group.HOMELAB, env="PROXMOX_VERIFY_SSL", default=False,
        description="Whether to verify TLS when calling Proxmox (self-signed certificates are common on PVE).")
    house_domain: str = setting(
        "house.domain", group=Group.HOMELAB, env="HOUSE_DOMAIN", default="", restart_needed=True,
        description="The house's DNS zone (home.example): every name under it is a homelab service "
                    "the tools may reach and that is never the public internet. Empty: the zone "
                    "GitLab is in (GITLAB_API_URL's host less its first label).")
    gitlab_api_url: str = setting(
        "gitlab.api_url", group=Group.HOMELAB, env="GITLAB_API_URL", kind=Kind.URL, default="",
        description="GitLab API base URL for the gitlab MCP server.")
    gitlab_token: str = setting(
        "gitlab.token", group=Group.HOMELAB, env="GITLAB_TOKEN", secret=True, restart_needed=True,
        description="GitLab personal access token for the gitlab MCP server.")
    mcp_gitlab_enabled: bool = setting(
        "mcp.gitlab_enabled", group=Group.HOMELAB, env="MCP_GITLAB_ENABLED",
        hermes_key="mcp_servers.gitlab.enabled", default=False, restart_needed=True,
        description="Whether the gitlab MCP server is started.")
    integrations_read_timeout: float = setting(
        "integrations.read_timeout", group=Group.HOMELAB, env="INTEGRATIONS_READ_TIMEOUT", default=90.0,
        min=1, advanced=True, description="Seconds a read-only integration call may take.")
    integrations_write_timeout: float = setting(
        "integrations.write_timeout", group=Group.HOMELAB, env="INTEGRATIONS_WRITE_TIMEOUT",
        default=300.0, min=1, advanced=True, description="Seconds a writing integration call may take.")
    gitlab_project_id: str = setting(
        "gitlab.project_id", group=Group.HOMELAB, env="GITLAB_PROJECT_ID", default="",
        description="Numeric id (or url-encoded path) of the JARVIS project on GitLab, so a merge "
                    "request the self-extension loop opens can be looked up without guessing.")

    # ---- Self-extension (D24) -------------------------------------------- #
    selfext_repo_path: str = setting(
        "selfext.repo_path", group=Group.HOMELAB, env="SELFEXT_REPO_PATH", kind=Kind.PATH,
        default="/opt/data/work/jarvis",
        description="Working checkout of this repository the self-extension loop scaffolds into "
                    "and pushes from. Every selfext tool is unavailable until it is a git work tree.")
    selfext_remote: str = setting(
        "selfext.remote", group=Group.HOMELAB, env="SELFEXT_REMOTE", default="origin",
        advanced=True, description="Git remote the self-extension branch is pushed to.")
    selfext_target_branch: str = setting(
        "selfext.target_branch", group=Group.HOMELAB, env="SELFEXT_TARGET_BRANCH", default="main",
        description="Branch a self-extension merge request targets. Never pushed to directly.")
    selfext_branch_prefix: str = setting(
        "selfext.branch_prefix", group=Group.HOMELAB, env="SELFEXT_BRANCH_PREFIX",
        default="jarvis/capability-", advanced=True,
        description="Prefix every branch the self-extension loop creates must carry, so a push can "
                    "be told apart from a human's branch and refused if it is neither.")
    selfext_timeout: float = setting(
        "selfext.timeout", group=Group.HOMELAB, env="SELFEXT_TIMEOUT", default=300.0, min=1,
        advanced=True,
        description="Seconds a git or test subprocess in the self-extension loop may take.")

    # ---- Mail (IMAP in, SMTP out) ---------------------------------------- #
    mail_imap_host: str = setting(
        "mail.imap_host", group=Group.HOMELAB, env="MAIL_IMAP_HOST", default="",
        description="IMAP server hostname JARVIS reads mail from.")
    mail_imap_port: int = setting(
        "mail.imap_port", group=Group.HOMELAB, env="MAIL_IMAP_PORT", default=993, min=1, max=65535,
        advanced=True, description="IMAP port (993 implicit TLS, 143 plain/STARTTLS).")
    mail_imap_ssl: bool = setting(
        "mail.imap_ssl", group=Group.HOMELAB, env="MAIL_IMAP_SSL", default=True, advanced=True,
        description="Whether the IMAP connection is TLS from the first byte (port 993).")
    mail_smtp_host: str = setting(
        "mail.smtp_host", group=Group.HOMELAB, env="MAIL_SMTP_HOST", default="",
        description="SMTP server hostname JARVIS sends mail through.")
    mail_smtp_port: int = setting(
        "mail.smtp_port", group=Group.HOMELAB, env="MAIL_SMTP_PORT", default=587, min=1, max=65535,
        advanced=True, description="SMTP port (587 STARTTLS, 465 implicit TLS, 25 plain).")
    mail_smtp_starttls: bool = setting(
        "mail.smtp_starttls", group=Group.HOMELAB, env="MAIL_SMTP_STARTTLS", default=True,
        advanced=True, description="Whether to upgrade the SMTP connection with STARTTLS after greeting.")
    mail_username: str = setting(
        "mail.username", group=Group.HOMELAB, env="MAIL_USERNAME", default="",
        description="Mailbox login used for both IMAP and SMTP.")
    mail_password: str = setting(
        "mail.password", group=Group.HOMELAB, env="MAIL_PASSWORD", secret=True,
        description="Mailbox password or app password. Least privilege: one mailbox, not an admin account.")
    mail_from: str = setting(
        "mail.from_address", group=Group.HOMELAB, env="MAIL_FROM", default="",
        description="Address outgoing mail is sent from; empty falls back to the mailbox login.")

    # ---- DAV (contacts, files) ------------------------------------------- #
    # Calendars are not here: the house's `calendar:` block (jarvis-core) is the one CalDAV
    # client, the morning brief and the calendar tools both read it, so configuring a
    # calendar twice is not possible.
    dav_url: str = setting(
        "dav.url", group=Group.HOMELAB, env="DAV_URL", kind=Kind.URL, default="",
        description="Base URL of the DAV server that carries contacts and files "
                    "(e.g. https://cloud.lan/remote.php/dav). Calendars are set in the house "
                    "(`calendar:` in jarvis-core's configuration.yaml), not here.")
    dav_username: str = setting(
        "dav.username", group=Group.HOMELAB, env="DAV_USERNAME", default="",
        description="DAV login shared by the contacts and files plugins.")
    dav_password: str = setting(
        "dav.password", group=Group.HOMELAB, env="DAV_PASSWORD", secret=True,
        description="DAV password or app password.")
    dav_verify_ssl: bool = setting(
        "dav.verify_ssl", group=Group.HOMELAB, env="DAV_VERIFY_SSL", default=True, advanced=True,
        description="Whether to verify TLS when calling the DAV server.")
    carddav_path: str = setting(
        "carddav.path", group=Group.HOMELAB, env="CARDDAV_PATH", default="addressbooks",
        advanced=True,
        description="Path under the DAV base URL where address books live.")
    webdav_path: str = setting(
        "webdav.path", group=Group.HOMELAB, env="WEBDAV_PATH", default="files", advanced=True,
        description="Path under the DAV base URL where the file share is rooted.")

    # ---- Jellyfin -------------------------------------------------------- #
    jellyfin_url: str = setting(
        "jellyfin.url", group=Group.HOMELAB, env="JELLYFIN_URL", kind=Kind.URL, default="",
        description="Jellyfin base URL for library browsing and playback control.")
    jellyfin_token: str = setting(
        "jellyfin.token", group=Group.HOMELAB, env="JELLYFIN_TOKEN", secret=True,
        description="Jellyfin API key (Dashboard → API Keys). Read scope is enough for browsing.")
    jellyfin_user_id: str = setting(
        "jellyfin.user_id", group=Group.HOMELAB, env="JELLYFIN_USER_ID", default="", advanced=True,
        description="Jellyfin user id whose libraries and resume list are read; empty picks the first user.")
    jellyfin_verify_ssl: bool = setting(
        "jellyfin.verify_ssl", group=Group.HOMELAB, env="JELLYFIN_VERIFY_SSL", default=True,
        advanced=True, description="Whether to verify TLS when calling Jellyfin.")

    # ---- Plex ------------------------------------------------------------ #
    plex_url: str = setting(
        "plex.url", group=Group.HOMELAB, env="PLEX_URL", kind=Kind.URL, default="",
        description="Plex server address for read-only browsing: https, or plain http only to a "
                    "private address such as http://192.168.1.20:32400. No login, query or "
                    "fragment in it.")
    plex_token: str = setting(
        "plex.token", group=Group.HOMELAB, env="PLEX_TOKEN", secret=True,
        description="Plex token, sent only to the server above in a header. Nothing is played "
                    "or changed with it.")

    # ---- Messaging platforms (D20) --------------------------------------- #
    telegram_enabled: bool = setting(
        "gateway.telegram.enabled", group=Group.MESSAGING, env="TELEGRAM_ENABLED",
        hermes_key="gateway.platforms.telegram.enabled", default=False, restart_needed=True,
        description="Whether the Telegram gateway platform runs. Off, a bot token alone cannot "
                    "switch it on.")
    telegram_bot_token: str = setting(
        "gateway.telegram.bot_token", group=Group.MESSAGING, env="TELEGRAM_BOT_TOKEN", secret=True,
        restart_needed=True,
        description="Telegram bot token from @BotFather. Read from the environment by Hermes' own "
                    "adapter; never written into config.yaml.")
    telegram_allowed_users: list[str] = setting(
        "gateway.telegram.allowed_users", group=Group.MESSAGING, env="TELEGRAM_ALLOWED_USERS",
        default=[], restart_needed=True,
        description="Telegram user ids allowed to talk to JARVIS. Empty means nobody: the gateway "
                    "default-denies when no allowlist is configured.")
    telegram_group_allowed_chats: list[str] = setting(
        "gateway.telegram.group_allowed_chats", group=Group.MESSAGING,
        env="TELEGRAM_GROUP_ALLOWED_CHATS", default=[], restart_needed=True, advanced=True,
        description="Telegram group/forum chat ids whose members may talk to JARVIS.")
    telegram_home_channel: str = setting(
        "gateway.telegram.home_channel", group=Group.MESSAGING, env="TELEGRAM_HOME_CHANNEL",
        default="", description="Telegram chat id proactive messages and cron output are delivered to.")
    discord_enabled: bool = setting(
        "gateway.discord.enabled", group=Group.MESSAGING, env="DISCORD_ENABLED",
        hermes_key="gateway.platforms.discord.enabled", default=False, restart_needed=True,
        description="Whether the Discord gateway platform runs. Off, a bot token alone cannot "
                    "switch it on.")
    discord_bot_token: str = setting(
        "gateway.discord.bot_token", group=Group.MESSAGING, env="DISCORD_BOT_TOKEN", secret=True,
        restart_needed=True,
        description="Discord bot token. Read from the environment by Hermes' own adapter; never "
                    "written into config.yaml.")
    discord_allowed_users: list[str] = setting(
        "gateway.discord.allowed_users", group=Group.MESSAGING, env="DISCORD_ALLOWED_USERS",
        default=[], restart_needed=True,
        description="Discord user ids allowed to talk to JARVIS. Empty means nobody: the gateway "
                    "default-denies when no allowlist is configured.")
    discord_home_channel: str = setting(
        "gateway.discord.home_channel", group=Group.MESSAGING, env="DISCORD_HOME_CHANNEL",
        default="", description="Discord channel id proactive messages and cron output are delivered to.")
    gateway_unauthorized_dm_behavior: Literal["pair", "ignore"] = setting(
        "gateway.unauthorized_dm_behavior", group=Group.MESSAGING,
        env="GATEWAY_UNAUTHORIZED_DM_BEHAVIOR",
        hermes_key="gateway.unauthorized_dm_behavior", default="pair",
        description="What an unlisted person's direct message gets while NO allowlist is set: "
                    "'pair' offers a code the owner must approve, 'ignore' drops it silently. "
                    "Once a platform has an allowlist, a stranger is dropped either way — Hermes "
                    "will not answer unknown senders with pairing codes. Neither reaches the model.")
    webhook_enabled: bool = setting(
        "gateway.webhook.enabled", group=Group.MESSAGING, env="WEBHOOK_ENABLED",
        hermes_key="gateway.platforms.webhook.enabled", default=False, restart_needed=True,
        description="Whether the inbound webhook platform runs, so n8n and CI can turn an event "
                    "into a JARVIS turn.")
    webhook_port: int = setting(
        "gateway.webhook.port", group=Group.MESSAGING, env="WEBHOOK_PORT", default=8650, min=1,
        max=65535, restart_needed=True,
        description="Port the inbound webhook listener binds inside the container.")
    webhook_secret: str = setting(
        "gateway.webhook.secret", group=Group.MESSAGING, env="WEBHOOK_SECRET", secret=True,
        restart_needed=True,
        description="HMAC secret every inbound webhook must sign its body with. Without it the route "
                    "refuses the request.")

    # ---- Network / apps -------------------------------------------------- #
    network_tailscale_ip: str = setting(
        "network.tailscale_ip", group=Group.NETWORK, env="TAILSCALE_IP", default="",
        description="Address the stack's published ports bind to: the host's LAN address, reached from "
                    "outside over WireGuard (the name predates the 2026-10-27 move to the LAN "
                    "and is kept for compatibility).")
    network_cors_origins: str = setting(
        "network.cors_origins", group=Group.NETWORK, env="AURA_CORS_ORIGINS",
        hermes_key="gateway.api_server.cors_origins", default="",
        description="Comma-separated browser origins allowed to call the adapter/API directly.")
    network_allowed_origins: str = setting(
        "network.allowed_origins", group=Group.NETWORK, env="AURA_ALLOWED_ORIGINS", default="",
        description="Comma-separated origins jarvis-core admits (its own CORS allow-list).")
    network_hud_web_origins: str = setting(
        "network.hud_web_origins", group=Group.NETWORK, env="AURA_HUD_WEB_ORIGINS", default="",
        description="Comma-separated origins allowed to open the HUD websocket.")
    network_public_url: str = setting(
        "network.public_url", group=Group.NETWORK, env="AURA_PUBLIC_URL", kind=Kind.URL, default="",
        description="URL the phone's pairing QR carries (how a phone reaches the adapter).")
    adapter_url: str = setting(
        "adapter.url", group=Group.NETWORK, env="AURA_ADAPTER_URL", kind=Kind.URL, default="",
        description="Adapter base URL the apps talk to.")
    adapter_key: str = setting(
        "adapter.key", group=Group.NETWORK, env="AURA_ADAPTER_KEY", secret=True,
        description="Bearer key the apps present to the adapter.")
    adapter_host: str = setting(
        "adapter.host", group=Group.NETWORK, env="ADAPTER_HOST", default="0.0.0.0", restart_needed=True,
        description="Interface the adapter's HTTP server binds to.")
    adapter_port: int = setting(
        "adapter.port", group=Group.NETWORK, env="ADAPTER_PORT", default=8643, min=1, max=65535,
        restart_needed=True, description="Port the adapter's HTTP server listens on.")
    adapter_cors_origins: str = setting(
        "adapter.cors_origins", group=Group.NETWORK, env="ADAPTER_CORS_ORIGINS", default="",
        description="Comma-separated browser origins the adapter admits directly.")
    adapter_upstream_timeout: float = setting(
        "adapter.upstream_timeout", group=Group.NETWORK, env="ADAPTER_UPSTREAM_TIMEOUT", default=900.0,
        min=1, advanced=True,
        description="Seconds an adapter->Hermes request may take (a cold model start is slow).")
    backend: str = setting(
        "backend", group=Group.NETWORK, env="AURA_BACKEND", default="",
        description="Which backend an app targets (used by the phone to pick a host).")

    # ---- Console --------------------------------------------------------- #
    console_pairing_secret: str = setting(
        "console.pairing_secret", group=Group.CONSOLE, env="AURA_PAIRING_SECRET", secret=True,
        description="Secret that authorises minting a permanent phone-pairing token.")
    console_password: str = setting(
        "console.password", group=Group.CONSOLE, env="AURA_CONSOLE_PASSWORD", secret=True,
        description="Console password in plaintext (hashed in memory, never written).")
    console_password_hash: str = setting(
        "console.password_hash", group=Group.CONSOLE, env="AURA_CONSOLE_PASSWORD_HASH", secret=True,
        description="Console password as an scrypt$ hash line (nothing recoverable).")
    console_password_file: str = setting(
        "console.password_file", group=Group.CONSOLE, env="AURA_CONSOLE_PASSWORD_FILE", kind=Kind.PATH,
        default="", description="Override path where a browser-chosen console password is stored.")
    console_origin: str = setting(
        "console.origin", group=Group.CONSOLE, env="AURA_CONSOLE_ORIGIN", kind=Kind.URL, default="",
        description="The console's https address; passkeys are bound to it. Empty: no passkeys, password only.")
    console_idle_minutes: int = setting(
        "console.idle_minutes", group=Group.CONSOLE, env="AURA_CONSOLE_IDLE_MINUTES", default=10080, min=5,
        description="Minutes a browser's console session may sit unused before it must sign in again.")
    console_pipeline: str = setting(
        "console.pipeline", group=Group.CONSOLE, env="AURA_PIPELINE", default="Jarvis",
        description="Voice pipeline name the HUD asks jarvis-core for.")

    # ---- Backups --------------------------------------------------------- #
    backup_dir: str = setting(
        "backup.dir", group=Group.BACKUPS, env="BACKUP_DIR", kind=Kind.PATH, default="/opt/jarvis/backups",
        description="Host path the nightly backup tarball lands in.")
    backup_keep: int = setting(
        "backup.keep", group=Group.BACKUPS, env="BACKUP_KEEP", default=14, min=1,
        description="How many nightly backup tarballs to keep.")
    backup_age_recipient: str = setting(
        "backup.age_recipient", group=Group.BACKUPS, env="BACKUP_AGE_RECIPIENT", default="",
        description="age public key (age1…) the nightly backup is encrypted to; unset, no backup is written.")
    backup_age_identity: str = setting(
        "backup.age_identity", group=Group.BACKUPS, env="BACKUP_AGE_IDENTITY", kind=Kind.PATH, default="",
        description="Host path of the age private key file, mounted read-only for the weekly restore drill.")
    backup_mirror_dir: str = setting(
        "backup.mirror_dir", group=Group.BACKUPS, env="BACKUP_MIRROR_DIR", kind=Kind.PATH, default="",
        description="Host path of the second location each encrypted backup is copied to (another disk or a NAS).")

    # ---- System ---------------------------------------------------------- #
    system_registry: str = setting(
        "system.registry", group=Group.SYSTEM, env="AURA_REGISTRY", default="",
        description="Container registry the images are pulled from.")
    system_image_tag: str = setting(
        "system.image_tag", group=Group.SYSTEM, env="AURA_TAG", default="main",
        description="Image tag the compose stack pulls (the pipeline writes the short SHA here).")
    # The voice's image and its two host directories (ADR-0039): read by the
    # compose file, not by the container, and here so .env.example and the
    # compose file are held to one list of names.
    system_voice_tag: str = setting(
        "system.voice_tag", group=Group.SYSTEM, env="COSYVOICE_TAG", default="latest", restart_needed=True,
        advanced=True,
        description="Image tag of the CosyVoice shim the compose stack pulls (built on its own tree, "
                    "deployed by name so an app rollback leaves the voice alone).")
    system_voice_models_dir: str = setting(
        "system.voice_models_dir", group=Group.SYSTEM, env="COSYVOICE_MODELS_DIR", kind=Kind.PATH,
        default="/opt/jarvis-host/voice/models", restart_needed=True, advanced=True,
        description="Host directory holding the CosyVoice 3 weights, outside the deploy directory "
                    "the pipeline rsyncs with --delete.")
    system_timezone: str = setting(
        "system.timezone", group=Group.SYSTEM, env="TZ", default="UTC", restart_needed=True,
        description="Container timezone (stamps logs and drives cron times).")
    system_auto_update: bool = setting(
        "system.auto_update", group=Group.SYSTEM, env="AURA_AUTO_UPDATE", default=True,
        description="Whether an app checks for and installs updates on its own. On: the desktop "
                    "shell downloads in the background and installs on quit, never with a "
                    "surprise restart.")
    desktop_push_to_talk_key: str = setting(
        "desktop.push_to_talk_key", group=Group.SYSTEM, env="AURA_PUSH_TO_TALK_KEY",
        default="Super+Space",
        description="Accelerator the desktop shell registers to start a turn from any window. "
                    "Separate from voice.push_to_talk, which is the on/off switch: read as one "
                    "setting, writing 'false' to switch push-to-talk off registered a hotkey "
                    "named 'false'.")
    desktop_palette_key: str = setting(
        "desktop.palette_key", group=Group.SYSTEM, env="AURA_PALETTE_KEY",
        default="Ctrl+Shift+K",
        description="Accelerator the desktop shell registers to show the island with its typing "
                    "line focused, from any window. Not a bare Ctrl+K: registered globally that "
                    "would take the chord from every browser and editor. A key another app owns "
                    "is logged and the tray says it is not available.")
    desktop_dictation_key: str = setting(
        "desktop.dictation_key", group=Group.SYSTEM, env="AURA_DICTATION_KEY",
        default="",
        description="Accelerator the desktop shell registers to dictate into the focused window: "
                    "hold it and speak, let go and what the adapter heard is typed where the "
                    "cursor is (once, on release). Empty is off. It needs a modifier, and a chord "
                    "the push-to-talk or typing key already is is not registered. Typing needs "
                    "input_automation on for that desktop.")
    hermes_api_url: str = setting(
        "hermes.api_url", group=Group.SYSTEM, env="HERMES_API_URL", kind=Kind.URL,
        default="http://127.0.0.1:8642", advanced=True,
        description="Loopback URL of the Hermes API server the adapter proxies (port 8642).")
    hermes_api_key: str = setting(
        "hermes.api_key", group=Group.SYSTEM, env="HERMES_API_KEY", secret=True, restart_needed=True,
        description="The Hermes API server key the adapter presents (port 8642).")
    models_api_key: str = setting(
        "models.api_key", group=Group.SYSTEM, env="MODELS_API_KEY", secret=True, restart_needed=True,
        description="The key the adapter presents to the model manager in jarvis-models (port 8001); "
                    "unset, the manager listens on its own loopback only and the Models panel is off.")
    hermes_serve_url: str = setting(
        "hermes.serve_url", group=Group.SYSTEM, env="HERMES_SERVE_URL", kind=Kind.URL,
        default="http://127.0.0.1:9119", advanced=True,
        description="Loopback URL of the hermes-serve backend the adapter relays (port 9119).")
    hermes_serve_token: str = setting(
        "hermes.serve_token", group=Group.SYSTEM, env="HERMES_SERVE_TOKEN", secret=True, restart_needed=True,
        description="The hermes-serve dashboard session token (port 9119).")
    adapter_model: str = setting(
        "adapter.model", group=Group.SYSTEM, env="AURA_MODEL", default="qwen3.8-27b", advanced=True,
        description="Model name the adapter reports for the apps' /v1/models listing.")
    adapter_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = setting(
        "adapter.log_level", group=Group.SYSTEM, env="ADAPTER_LOG_LEVEL", default="INFO", advanced=True,
        description="Log level the adapter's process logs at.")
    apps_api_key: str = setting(
        "apps.api_key", group=Group.SYSTEM, env="AURA_API_KEY", secret=True, restart_needed=True,
        description="Bearer key the apps present to the adapter's front door.")

    # ---- HUD (D30) ------------------------------------------------------- #
    hud_mode: HudMode = setting(
        "hud.mode", group=Group.HUD, hermes_key="hud.mode", default="task", settable=True,
        description="Which preset the map is in: overview (everything as glyphs), task (the "
                    "current work's panels open), focus (one panel open, the rest glyphs) or "
                    "ambient (the reactor and a minimal strip).")
    hud_lock_layout: bool = setting(
        "hud.lock_layout", group=Group.HUD, hermes_key="hud.lock_layout", default=False, settable=True,
        description="Lock layout: while on, JARVIS may not move, resize, open or close panels.")
    hud_reduced_transparency: TriState = setting(
        "hud.reduced_transparency", group=Group.HUD, hermes_key="hud.reduced_transparency",
        default="system", settable=True,
        description="Replace glass blur with solid panels: follow the system setting, or force on/off.")
    hud_reduced_motion: TriState = setting(
        "hud.reduced_motion", group=Group.HUD, hermes_key="hud.reduced_motion", default="system",
        settable=True,
        tier="basic", description="Shorten or drop panel and reactor animation: follow the system setting, or force on/off.")
    hud_sync_layouts: bool = setting(
        "hud.sync_layouts", group=Group.HUD, hermes_key="hud.sync_layouts", default=False, settable=True,
        description="Apply a layout chosen on one device to the other devices of the same surface kind.")
    hud_layouts: HudLayouts = setting(
        "hud.layouts", group=Group.HUD, hermes_key="hud.layouts", default={}, settable=True,
        shape="hud_layouts", widget=Widget.MANAGED,
        description="Saved arrangements per surface and activity; written by the window manager, "
                    "not by hand.")
    hud_car_navigation: CarNavigation = setting(
        "hud.car_navigation", group=Group.HUD, hermes_key="hud.car_navigation", default="jarvis",
        settable=True,
        description="Where a route Jarvis finds is driven in the car: Jarvis's own map on the car "
                    "screen, with its turns said aloud, or a navigation app (Organic Maps, Google "
                    "Maps, HERE WeGo) in the car's app pane. Ask Jarvis to change it.")
    hud_car_speak_directions: bool = setting(
        "hud.car_speak_directions", group=Group.HUD, hermes_key="hud.car_speak_directions", default=True,
        settable=True,
        description="Say each turn of a route aloud on the car's speakers, timed from the car's "
                    "speed so the words arrive before the turn. Ask Jarvis to change it.")
    hud_car_clock: CarClock = setting(
        "hud.car_clock", group=Group.HUD, hermes_key="hud.car_clock", default="bar", settable=True,
        description="Where the car screen shows the time: in the top bar, in a small Clock panel, "
                    "or not at all.")
    hud_car_voice: CarVoice = setting(
        "hud.car_voice", group=Group.HUD, hermes_key="hud.car_voice", default="auto", settable=True,
        description="Where a reply in the car is spoken from: the phone itself when it can keep "
                    "up (auto), always the house, or always the phone. Spoken from the phone it "
                    "needs no connection, so it does not break up on mobile data.")
    hud_car_speed_warnings: bool = setting(
        "hud.car_speed_warnings", group=Group.HUD, hermes_key="hud.car_speed_warnings", default=True,
        settable=True,
        description="Jarvis says to slow down, in the persona's words, when the car's average speed "
                    "is well over the road's limit; the Speed panel shows it either way.")
    hud_car_speed_warn_over: int = setting(
        "hud.car_speed_warn_over", group=Group.HUD, hermes_key="hud.car_speed_warn_over", default=15,
        min=1, max=50, settable=True,
        description="How many mph over the limit the car's average speed must be before Jarvis "
                    "says anything.")
    hud_car_speed_full_over: int = setting(
        "hud.car_speed_full_over", group=Group.HUD, hermes_key="hud.car_speed_full_over", default=20,
        min=5, max=50, settable=True,
        description="How many mph over the limit fills the Speed panel's bar; the words (safe, "
                    "speedy, risky, dangerous, deadly) scale with it and the bar runs past full.")
    hud_car_speed_average_s: int = setting(
        "hud.car_speed_average_s", group=Group.HUD, hermes_key="hud.car_speed_average_s", default=8,
        min=2, max=60, settable=True,
        description="Seconds of driving the Speed panel averages, so a moment's overtake is not "
                    "a warning.")
    hud_car_speed_repeat_s: int = setting(
        "hud.car_speed_repeat_s", group=Group.HUD, hermes_key="hud.car_speed_repeat_s", default=90,
        min=10, max=900, settable=True,
        description="Seconds before Jarvis repeats a speed warning, unless the speeding gets "
                    "worse.")
    activity_enabled: bool = setting(
        "activity.enabled", group=Group.HUD, env="ACTIVITY_ENABLED", default=True,
        description="The activity view: while the browser or the desktop is being driven, a live "
                    "tile shows the page, the action and a reduced screenshot when a tool's result "
                    "carries one, redacted before it leaves the container. Off, the aura-activity "
                    "plugin emits nothing and no frame is captured at all.")


# --------------------------------------------------------------------------- #
# The registry, derived from the model's fields.
# --------------------------------------------------------------------------- #
def _unwrap_optional(annotation: Any) -> Any:
    if get_origin(annotation) is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _kind_and_choices(annotation: Any, override: Optional[Kind]) -> tuple[Kind, Optional[tuple[str, ...]]]:
    ann = _unwrap_optional(annotation)
    if get_origin(ann) is Literal:
        return Kind.ENUM, tuple(str(a) for a in get_args(ann))
    if ann is bool:
        return Kind.BOOL, None
    if ann is int:
        return Kind.INT, None
    if ann is float:
        return Kind.FLOAT, None
    if ann is str:
        return override or Kind.STRING, None
    if get_origin(ann) is list:
        return Kind.LIST, None
    if get_origin(ann) is dict:
        return Kind.OBJECT, None
    raise TypeError(f"no setting kind for annotation {annotation!r}")


def _build_registry() -> tuple[Setting, ...]:
    rows: list[Setting] = []
    for name, info in Settings.model_fields.items():
        extra = info.json_schema_extra
        if not isinstance(extra, dict) or "jarvis" not in extra:
            raise TypeError(f"Settings.{name} was not declared with setting()")
        meta = dict(cast("dict[str, Any]", extra["jarvis"]))
        kind, choices = _kind_and_choices(info.annotation, meta.pop("kind"))
        # pydantic types default_factory as a union of zero- and one-argument callables
        # across its releases; the cast keeps this a plain call on every version mypy sees
        # (an `ignore` was needed on one and flagged unused on the other).
        factory = cast("Callable[[], Any] | None", info.default_factory)
        default = factory() if factory is not None else info.default
        widget = meta.pop("widget") or _widget_for(kind, meta["secret"])
        if kind is Kind.INT:
            # Bounds of an int setting render as ints in the contract (1, not 1.0): the
            # console's number widget and the plugin compare them to JSON integers.
            meta["min"] = int(meta["min"]) if meta["min"] is not None else None
            meta["max"] = int(meta["max"]) if meta["max"] is not None else None
        rows.append(Setting(field=name, kind=kind, choices=choices, default=default, widget=widget, **meta))
    return tuple(rows)


#: One row per setting, in declaration order — what every generator walks.
SETTINGS: tuple[Setting, ...] = _build_registry()
SETTINGS_BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}
SETTINGS_BY_ENV: dict[str, Setting] = {s.env: s for s in SETTINGS if s.env}
SETTINGS_BY_HERMES_KEY: dict[str, Setting] = {s.hermes_key: s for s in SETTINGS if s.hermes_key}


def _assert_unique() -> None:
    # A duplicate key or env name would make the indexes silently drop a row and a
    # generator emit a short file; catch it at import.
    for label, names in (("setting key", [s.key for s in SETTINGS]),
                         ("env name", [s.env for s in SETTINGS if s.env]),
                         ("hermes key", [s.hermes_key for s in SETTINGS if s.hermes_key])):
        if len(names) != len(set(names)):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"duplicate {label}(s): {dupes}")


_assert_unique()


# --------------------------------------------------------------------------- #
# The jsettings plugin's runtime constants and its allow-list order.
# --------------------------------------------------------------------------- #
COMMAND_NAME: str = "jsettings"
TOOLS: tuple[str, ...] = ("settings_list", "settings_get", "settings_set")
AUDIT_LOG: str = "logs/settings.jsonl"

#: The order the ``jsettings`` allow-list is served in — the eight original keys
#: in the order the console and the plugin already read, then the HUD keys.
CONTRACT_KEY_ORDER: tuple[str, ...] = (
    "goals.max_turns",
    "approvals.mode",
    "tools.enabled",
    "tools.disabled",
    "model.default",
    "image_gen.provider",
    "tool_loop_guardrails.hard_stop_enabled",
    "tool_loop_guardrails.loop_caps.max_web_searches",
    "hud.mode",
    "hud.lock_layout",
    "hud.reduced_transparency",
    "hud.reduced_motion",
    "hud.sync_layouts",
    "hud.layouts",
    "hud.car_navigation",
    "hud.car_speak_directions",
    "hud.car_clock",
    "hud.car_voice",
    "hud.car_speed_warnings",
    "hud.car_speed_warn_over",
    "hud.car_speed_full_over",
    "hud.car_speed_average_s",
    "hud.car_speed_repeat_s",
    "voice.reasoning_effort",
    "voice.pronunciations",
    "voice.quiet_hours",
    "privacy.quiet_house",
)


def settable_settings() -> list[Setting]:
    """The runtime allow-list settings, in :data:`CONTRACT_KEY_ORDER`.

    Raises if the registry and the order fall out of step, so a new ``settable``
    row cannot land in the contract in an unspecified position.
    """
    settable = {s.key for s in SETTINGS if s.settable}
    if settable != set(CONTRACT_KEY_ORDER):
        missing = settable - set(CONTRACT_KEY_ORDER)
        extra = set(CONTRACT_KEY_ORDER) - settable
        raise ValueError(f"settable settings and CONTRACT_KEY_ORDER disagree: "
                         f"missing from order {sorted(missing)}, not settable {sorted(extra)}")
    return [SETTINGS_BY_KEY[k] for k in CONTRACT_KEY_ORDER]


# --------------------------------------------------------------------------- #
# Reading the environment.
# --------------------------------------------------------------------------- #
def from_env(environ: Optional[Mapping[str, str]] = None, *,
             overrides: Optional[Mapping[str, str]] = None) -> Settings:
    """Read the environment once and return a validated :class:`Settings`.

    This is the only place the settings layer touches ``os.environ``. Unrelated
    environment variables are ignored; an *empty* variable counts as unset (the
    compose files pass every ``.env`` key through, blank or not, and a blank
    ``GOALS_MAX_TURNS=`` must mean the default, not a parse error); a variable
    whose value does not fit its declared type raises a
    :class:`pydantic.ValidationError` naming the variable.

    ``overrides`` (the settings plugin's :func:`load_overrides`) win over the
    environment name for name, the way the entrypoint exports them over the
    compose-provided value in the gateway container; a process that reads the
    file itself (the adapter) gets the same answer without an export.
    """
    env = adopt_legacy(os.environ if environ is None else environ)
    merged: Mapping[str, str] = {**env, **adopt_legacy(overrides)} if overrides else env
    present = {s.env: merged[s.env].strip() for s in SETTINGS
               if s.env and merged.get(s.env, "").strip() != ""}
    return Settings.model_validate(present)


# --------------------------------------------------------------------------- #
# Operator overrides of env-backed settings (ADR-0044).
# --------------------------------------------------------------------------- #
#: Where the ``aura-settings`` plugin keeps the values an operator set for
#: env-backed settings, relative to ``HERMES_HOME``: ``{"<ENV_NAME>": "<value>"}``,
#: mode 0600. The entrypoint exports it over the container environment on every
#: start and the adapter reads it at load, so the three agree on one path.
OVERRIDES_REL: str = ".storage/environment.json"

#: Env names whose value is fixed OUTSIDE the container and which an override
#: file inside it therefore cannot change: the compose file resolves them on the
#: host (an image tag, a port bind, a value renamed on its way into a service,
#: or one handed to a container that does not read the file), the phone and
#: the desktop read them from their own settings, or the entrypoint acts on
#: them as root before any override could be trusted (``SELFEXT_*``; ``HOUSE_DOMAIN`` and
#: ``GITLAB_API_URL``, its fallback, which widen the egress allowlist to the house's zone and
#: so must not be settable from a file the gateway's uid can write). A ``set``
#: on one of these is refused and the row says ``applies: "compose"``. Pinned by
#: ``tests/test_overrides.py`` against ``deploy/docker-compose.yml`` and
#: ``deploy/.env.example``, so a compose change that starts or stops owning a
#: name fails there rather than silently making an override a no-op.
HOST_OWNED_ENV: frozenset[str] = frozenset({
    "ADAPTER_CORS_ORIGINS", "ADAPTER_HOST", "ADAPTER_PORT",
    "BACKUP_AGE_IDENTITY", "BACKUP_AGE_RECIPIENT", "BACKUP_DIR", "BACKUP_KEEP", "BACKUP_MIRROR_DIR",
    "COSYVOICE_MODELS_DIR", "COSYVOICE_TAG", "GITLAB_API_URL",
    "HERMES_API_KEY", "HERMES_SERVE_TOKEN", "HOUSE_DOMAIN",
    "AURA_ADAPTER_KEY", "AURA_ADAPTER_URL", "AURA_ALLOWED_ORIGINS", "AURA_API_KEY",
    "AURA_AUTO_UPDATE", "AURA_BACKEND",
    "AURA_CONSOLE_IDLE_MINUTES", "AURA_CONSOLE_ORIGIN",
    "AURA_CONSOLE_PASSWORD", "AURA_CONSOLE_PASSWORD_FILE", "AURA_CONSOLE_PASSWORD_HASH",
    "AURA_CORS_ORIGINS", "AURA_HUD_WEB_ORIGINS", "AURA_PAIRING_SECRET", "AURA_PIPELINE",
    "AURA_DICTATION_KEY", "AURA_PALETTE_KEY", "AURA_PUSH_TO_TALK", "AURA_PUSH_TO_TALK_KEY", "AURA_REGISTRY", "AURA_TAG",
    "AURA_TOKEN", "AURA_TTS_VOICE", "AURA_URL", "AURA_VAD_HANGOVER_MS",
    "LLM_API_KEY", "LLM_MODEL", "MODELS_API_KEY", "SEARXNG_SECRET", "TAILSCALE_IP", "TZ", "WEBHOOK_PORT",
    "SELFEXT_REPO_PATH", "SELFEXT_TARGET_BRANCH",
})

Applies = Literal["now", "restart", "compose"]


def applies(setting: Setting) -> Applies:
    """When a value written through the settings plugin takes effect.

    ``now``: a Hermes config key the gateway reads live. ``restart``: the value
    is read at start (an env leaf via the override file, or a config key marked
    ``restart_needed``). ``compose``: fixed outside the container
    (:data:`HOST_OWNED_ENV`) — the plugin will not write it. A leaf with both an
    env name and a Hermes key answers for the Hermes key, which is the one the
    plugin writes first.
    """
    if setting.hermes_key:
        return "restart" if setting.restart_needed else "now"
    if setting.env in HOST_OWNED_ENV:
        return "compose"
    return "restart"


def overrides_path(home: "str | os.PathLike[str]") -> str:
    """The override file for a ``HERMES_HOME``."""
    return os.path.join(os.fspath(home), OVERRIDES_REL)


def load_overrides(path: "str | os.PathLike[str]") -> dict[str, str]:
    """The overrides a process may apply: ``{env_name: value}`` from the file.

    Only names the schema declares and does not list in :data:`HOST_OWNED_ENV`
    come back, and only string values — the file is written by the gateway's
    uid, and the entrypoint exports what this returns as root, so an entry that
    is not a JARVIS setting (``LD_PRELOAD``, a path the entrypoint chowns) must
    never reach the environment whatever wrote it. A missing file is no
    overrides; an unreadable or malformed one raises (``OSError`` /
    ``ValueError``) so the caller decides whether that is a warning or a stop.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{os.fspath(path)}: expected a JSON object of env name -> value")
    return {name: value for name, value in adopt_legacy(data).items()
            if isinstance(name, str) and isinstance(value, str)
            and name in SETTINGS_BY_ENV and name not in HOST_OWNED_ENV}


#: The prefix env names had before T-0959. Production's .env, GitLab's project
#: variables, a desktop's environment and an overrides file already in a volume were
#: written with it, and none of them is the repo's to rename.
LEGACY_PREFIX = "JARVIS_"


def adopt_legacy(env: Mapping[str, Any]) -> dict[str, Any]:
    """``env`` with each ``JARVIS_X`` also present as ``AURA_X``, unless ``AURA_X`` is set.

    The new name always wins, so an operator who renames a key in place is obeyed.
    Does not remove the legacy key, and does not decide whether a name is a setting:
    the caller's own filter still applies to the adopted name.
    """
    out = dict(env)
    for name, value in env.items():
        if isinstance(name, str) and name.startswith(LEGACY_PREFIX):
            out.setdefault("AURA_" + name[len(LEGACY_PREFIX):], value)
    return out


def env_string(setting: Setting, value: Any) -> str:
    """The ``.env`` spelling of a typed value — the inverse of what :func:`from_env` parses.

    A list joins on commas (the shape the gateway itself splits), a bool is
    ``true``/``false``, ``None`` is the empty string (unset). Anything else is
    ``str()``, which is what an int, a float or a string already round-trip as.
    """
    if value is None:
        return ""
    if setting.kind is Kind.LIST:
        return ",".join(str(x) for x in value)
    if setting.kind is Kind.BOOL:
        return "true" if value else "false"
    return str(value)


# --------------------------------------------------------------------------- #
# The fail-fast validator over a settings mapping.
# --------------------------------------------------------------------------- #
class SettingsError(ValueError):
    """An unknown settings key, or a value that does not fit the key's type."""


def resolve(key: str) -> Optional[Setting]:
    """A setting addressed by its canonical key, its env name, or its Hermes key."""
    return SETTINGS_BY_KEY.get(key) or SETTINGS_BY_ENV.get(key) or SETTINGS_BY_HERMES_KEY.get(key)


def coerce(setting: Setting, value: Any, *, strict: bool = False) -> Any:
    """Coerce/validate one value against a setting, raising :class:`SettingsError`.

    Strings from the environment are coerced (``"20"`` -> ``20``) unless
    ``strict`` — a JSON payload (``jsettings set``) is already typed, and a string
    ``"true"`` accepted there would land a YAML string where Hermes reads a bool.
    An in-Python value of the wrong type (``True`` for an int) is always rejected,
    because ``bool`` is an ``int`` subclass and would otherwise slip through.
    Returned values are plain JSON-shaped Python (an ``OBJECT`` comes back as
    dicts), so the caller can write them to a YAML config or send them over a
    socket as they are.
    """
    k = setting.kind
    if k in (Kind.INT, Kind.FLOAT):
        if isinstance(value, bool) or (strict and not isinstance(value, (int, float))):
            raise SettingsError(f"{setting.key} takes {'an integer' if k is Kind.INT else 'a number'}, "
                                f"not {type(value).__name__}")
        try:
            num: Any = int(value) if k is Kind.INT else float(value)
        except (TypeError, ValueError):
            raise SettingsError(f"{setting.key} takes {'an integer' if k is Kind.INT else 'a number'}, "
                                f"got {value!r}") from None
        if setting.min is not None and num < setting.min or setting.max is not None and num > setting.max:
            raise SettingsError(f"{setting.key} must be between {setting.min} and {setting.max}, got {num}")
        return num
    if k is Kind.BOOL:
        if isinstance(value, bool):
            return value
        if not strict and isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise SettingsError(f"{setting.key} takes true or false, not {type(value).__name__}")
    if k is Kind.ENUM:
        if value not in (setting.choices or ()):
            raise SettingsError(f"{setting.key} must be one of {', '.join(setting.choices or ())}, got {value!r}")
        return value
    if k is Kind.LIST:
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise SettingsError(f"{setting.key} takes a list of strings")
        return value
    if k is Kind.OBJECT:
        return _coerce_object(setting, value)
    # string / url / path
    if value is None and setting.default is None:
        return None  # an optional string (image_gen.provider) may be cleared
    if not isinstance(value, str):
        raise SettingsError(f"{setting.key} takes a string, got {type(value).__name__}")
    return value


def _coerce_object(setting: Setting, value: Any) -> Any:
    """``hud.layouts``: ``{surface: {activity: LayoutDocument}}``, every document validated."""
    if not isinstance(value, dict):
        raise SettingsError(f"{setting.key} takes an object, got {type(value).__name__}")
    shape = SHAPES[setting.shape or ""]
    out: dict[str, dict[str, Any]] = {}
    for surface, per_activity in value.items():
        if surface not in SURFACES:
            raise SettingsError(f"{setting.key}: unknown surface {surface!r}; one of {', '.join(SURFACES)}")
        if not isinstance(per_activity, dict):
            raise SettingsError(f"{setting.key}.{surface} takes an object of activity -> layout")
        out[surface] = {}
        for activity, doc in per_activity.items():
            if not isinstance(activity, str) or not activity:
                raise SettingsError(f"{setting.key}.{surface}: activity names must be non-empty strings")
            try:
                out[surface][activity] = shape.model_validate(doc).model_dump(exclude_none=True)
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(p) for p in first.get("loc", ()))
                raise SettingsError(
                    f"{setting.key}.{surface}.{activity}: {first.get('msg')}"
                    + (f" (at {where})" if where else "")
                ) from None
    return out


def validate_settings(data: Mapping[str, Any], *, strict: bool = False) -> dict[str, Any]:
    """Fail-fast validation of a settings mapping.

    An unknown key is an error naming the key; a value of the wrong type is an
    error naming the key. Returns the mapping with each value coerced to its
    declared type (keys unchanged, so a caller can address by env name or key).
    ``strict`` disables string coercion (see :func:`coerce`).
    """
    out: dict[str, Any] = {}
    for key, value in data.items():
        setting_ = resolve(key)
        if setting_ is None:
            raise SettingsError(f"unknown settings key: {key!r}")
        out[key] = coerce(setting_, value, strict=strict)
    return out
