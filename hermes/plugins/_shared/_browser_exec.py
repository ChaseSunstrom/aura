"""What a ``browser_exec`` call does, read as the per-action browser calls it stands for.

Hermes v0.23 removed the ten per-action browser tools (navigate, click, type, snapshot…) for
one ``browser_exec(code)`` that runs Python with browser helpers pre-imported (T-1519). The
matrix had those per-action tools free, each behind its own checks (an API answer in the
browser, a metadata endpoint, a geocoder, text the owner did not say); ``browser_exec`` as a
whole runs in the gateway container, outside the aura-bwrap sandbox, so arbitrary code in it
must ask. :func:`steps` reads the code against an allow-list and, when every statement is a
helper call or plain data handling, returns the per-action calls it makes, so the matrix can
judge each one the way it judged the old tools. Anything else returns ``None``: the call is
raw automation and asks.

The allow-list is of syntax, not a deny-list of words: no import, no attribute but a small
set of string, list and dict methods, no name the code did not bind itself or that is not a
helper or a plain builtin, and every address and typed text a literal, so the checks see what
the call will really send. A screenshot is taken only to the harness's own path.

Does not cover:
- a helper redefined by the workspace's ``agent_helpers.py``, which the harness imports into
  every call; the file name is sealed (``_matrix.SEALED``), so no tool may write it;
- what the page's own scripts do once loaded, or a click landing on a submit button;
- JavaScript passed to ``js()`` beyond the read-shaped test in :func:`_reads_only`: an
  expression that passes it can still read anything the page shows, as the snapshot did.
"""
from __future__ import annotations

import ast
import re
from urllib.parse import urlsplit
from collections.abc import Callable
from typing import Any

#: Helpers that only look at the page (the old snapshot) or move nothing outside it.
_LOOKS = frozenset({"page_info", "wait_for_load", "ensure_real_tab"})
#: Builtins that compute on values already in hand.
_BUILTINS = frozenset({
    "print", "len", "str", "int", "float", "bool", "round", "abs", "min", "max", "sum",
    "sorted", "reversed", "range", "enumerate", "zip", "list", "dict", "set", "tuple", "any", "all",
    "repr", "isinstance",
})
#: Methods of a str, list or dict the code may call on a value it holds.
_METHODS = frozenset({
    "get", "items", "keys", "values", "append", "extend", "split", "rsplit", "splitlines",
    "strip", "lstrip", "rstrip", "lower", "upper", "title", "startswith", "endswith", "join",
    "replace", "find", "count", "index",
})
_SCHEMES = ("http://", "https://")
#: A JavaScript expression that sends, stores, navigates or changes the page is not a read: an
#: assignment or an increment, a call of a method that changes the page, a network or storage
#: API, a navigation. Written as the acts, not as words, so `location.href` or a `.submit`
#: selector still reads.
_JS_ACTS = re.compile(
    r"(?<![=!<>])=(?![=>])|\+\+|--|\bdelete\s"
    r"|\blocation\s*\.\s*replace\s*\("  # a string's .replace() reads; location's navigates
    r"|\.\s*(click|submit|requestSubmit|focus|blur|remove|append|prepend|appendChild|removeChild|"
    r"insertBefore|replaceWith|replaceChildren|setAttribute|removeAttribute|toggleAttribute|"
    r"insertAdjacent\w*|dispatchEvent|postMessage|assign|reload|pushState|replaceState|back|"
    r"forward|go|write|writeln|showModal|close|play|pause|setRangeText|execCommand)\s*\("
    r"|\b(fetch|eval|Function|setTimeout|setInterval|open|import|Worker|SharedWorker)\s*\("
    r"|\b(XMLHttpRequest|sendBeacon|WebSocket|EventSource|RTCPeerConnection|cookie|localStorage|"
    r"sessionStorage|indexedDB|caches|clipboard|serviceWorker|credentials|geolocation)\b")

Step = tuple[str, dict[str, Any]]


class _Refused(Exception):
    """The code steps outside the allow-list."""


#: A local declaration (`const h = …`) binds a name inside the expression and changes nothing.
_JS_DECLARES = re.compile(r"\b(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=(?![=>])")


#: Names a read may use. A regex of the acts alone could not hold: JavaScript reaches a function
#: by a computed name (`window['fet'+'ch']`, `Reflect.get`, `fetch.call`, a value picked out of
#: `Object.values(window)`), and the security review of T-1519 sent page data out through each.
#: With only these names, no computed member access and no escape, the functions an expression
#: reaches are the reads below and nothing it builds from a string can become one.
#:
#: Bare names: the language and the globals a page cannot make a network call of its own
#: through by name (a page can patch JSON or Array, and then sees what is passed in: the
#: strings, which the typed-text gate holds, see `_js_strings`).
_JS_GLOBALS = frozenset({
    "const", "let", "var", "return", "typeof", "instanceof", "in", "of", "new", "null",
    "undefined", "true", "false", "NaN", "Infinity", "if", "else",
    "document", "window", "location", "JSON", "Array", "Math", "Number", "String", "Boolean",
    "parseInt", "parseFloat", "isNaN", "Set", "Map", "Date", "URL",
})
#: Names after a dot.
_JS_PROPERTIES = frozenset({
    "stringify", "parse", "from", "isArray", "round", "floor", "ceil", "max", "min", "abs",
    "now", "toISOString", "length", "map", "filter", "slice", "join", "trim", "trimStart",
    "trimEnd", "split", "includes", "indexOf", "startsWith", "endsWith", "toLowerCase",
    "toUpperCase", "find", "findIndex", "some", "every", "reduce", "concat", "flat", "flatMap",
    "forEach", "sort", "reverse", "at", "toString", "toFixed", "substring", "padStart", "padEnd",
    "size", "has", "get", "keys", "entries",
    "document", "location", "title", "body", "head", "documentElement", "readyState", "href",
    "host", "hostname", "pathname", "search", "hash", "origin", "protocol", "referrer",
    # Not `value`: a field's live value is what the vault filled (a password), never shown on
    # the page, and the vault exists so the model never holds it (T-1528); such a read asks.
    "textContent", "innerText", "innerHTML", "outerHTML", "checked", "selected",
    "selectedIndex", "options", "disabled", "placeholder", "label", "tagName", "nodeName", "id",
    "className", "classList", "contains", "children", "childNodes", "firstElementChild",
    "lastElementChild", "parentElement", "nextElementSibling", "previousElementSibling",
    "attributes", "name", "src", "alt", "rel", "type", "content", "dataset", "getAttribute",
    "hasAttribute", "querySelector", "querySelectorAll", "getElementById",
    "getElementsByTagName", "getElementsByClassName", "closest", "matches", "item", "images",
    "links", "forms", "rows", "cells", "getBoundingClientRect", "top", "left", "bottom", "right",
    "width", "height", "x", "y", "scrollY", "scrollX", "innerWidth", "innerHeight",
    "scrollHeight", "offsetTop", "offsetHeight", "offsetWidth", "naturalWidth", "naturalHeight",
})
_JS_BARE = re.compile(r"(?<![\w$.])[A-Za-z_$][\w$]*")
_JS_PROPERTY = re.compile(r"(?<=\.)[A-Za-z_$][\w$]*")
#: `x[…]` (a member by computed name) unless the index is a number: an array literal follows
#: `(`, `,`, `[`, `=`, `:` or the start, never a name, a bracket or a string.
_JS_COMPUTED = re.compile(r"""[\w$)\]"'`]\s*(?:\?\.)?\s*\[(?!\s*-?\d+\s*\])""")
_JS_ARROW_ONE = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*=>")
_JS_ARROW_MANY = re.compile(r"\(([^()]*)\)\s*=>")
_JS_DECLARED = re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)")
_JS_NAME = re.compile(r"[A-Za-z_$][\w$]*")
_JS_PARAMS = re.compile(r"[\w$\s,]*")
#: A number literal (decimal, hex, exponent): six characters or more carry text as well as a
#: string does (a phone number), so they meet the typed-text gate too; `slice(0, 3000)` does not.
#: A name never starts with a digit, so `.5` after anything but a name character is a number.
_JS_NUMBER = re.compile(r"(?<![\w$.])\d[\w.]*|(?<![\w$)\]])\.\d[\w.]*")
#: A string in a read that is no more than a selector or a separator: short, no run of digits,
#: no address sign. Anything else could carry what the turn knows into a page function the page
#: has patched (`js()` runs in the page's own world), so it meets the typed-text gate.
_JS_PLAIN = re.compile(r"[\w\s#.,:;>~+*()\[\]='\"|^$/-]{0,40}")
_DIGIT_RUN = re.compile(r"\d{3}")


def _scan_strings(expression: str) -> tuple[str, list[str]] | None:
    """(the expression with each string's content emptied, the strings' contents); None for an
    unterminated string or a template with a `${…}` hole (code inside a string)."""
    out, strings, i, n = [], [], 0, len(expression)
    while i < n:
        c = expression[i]
        if c in "'\"`":
            j = i + 1
            while j < n and expression[j] != c:
                if expression[j] == "\\":
                    j += 1
                elif c == "`" and expression.startswith("${", j):
                    return None
                j += 1
            if j >= n:
                return None
            out.append(c + c)
            strings.append(expression[i + 1:j])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out), strings


def _scope_end(code: str, start: int, *, statements: bool) -> int:
    """Where the group that begins at ``start`` ends: its closing bracket, or (an arrow's body)
    a comma or semicolon at its own depth."""
    depth = 0
    for j in range(start, len(code)):
        c = code[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return j
            depth -= 1
        elif c in ",;" and depth == 0 and not statements:
            return j
    return len(code)


def _own_names(code: str) -> list[tuple[str, int, int]]:
    """(name, from, to) for each name the expression binds and where it is that binding: an
    arrow's parameters within its body, a declaration to the end of its block."""
    scopes = []
    for match in _JS_ARROW_ONE.finditer(code):
        scopes.append((match.group(1), match.start(), _scope_end(code, match.end(), statements=False)))
    for match in _JS_ARROW_MANY.finditer(code):
        end = _scope_end(code, match.end(), statements=False)
        for name in _JS_NAME.findall(match.group(1)):
            scopes.append((name, match.start(), end))
    for match in _JS_DECLARED.finditer(code):
        scopes.append((match.group(1), match.start(), _scope_end(code, match.end(), statements=True)))
    return scopes


def _reads_only(expression: str) -> bool:
    """Does this JavaScript only read the page? Does not cover what a read returns: anything the
    page shows (its text, its HTML, a field's value) is read, as the snapshot read it."""
    if _JS_ACTS.search(_JS_DECLARES.sub(" ", expression)):
        return False
    scanned = _scan_strings(expression)
    if scanned is None:
        return False
    code = re.sub(r"\s*\.\s*", ".", scanned[0].replace("...", " "))
    # `/` opens a comment or a regex literal, inside which the string scan above sees quotes
    # that are not strings and code that it then never checks (the second review's `/'/`); a
    # read gives up division for it.
    if "\\" in code or "/" in code or _JS_COMPUTED.search(code):
        return False
    # A destructured parameter (`({fetch: f}) =>`, `([f]) =>`) makes a global the arrow's own.
    if any(not _JS_PARAMS.fullmatch(m.group(1)) for m in _JS_ARROW_MANY.finditer(code)):
        return False
    if not all(m.group() in _JS_PROPERTIES for m in _JS_PROPERTY.finditer(code)):
        return False
    scopes = _own_names(code)
    return all(
        m.group() in _JS_GLOBALS
        or any(name == m.group() and start <= m.start() <= end for name, start, end in scopes)
        for m in _JS_BARE.finditer(code))


def _js_strings(expression: str) -> list[str]:
    """The strings and long numbers in a read that could carry text (see `_JS_PLAIN`,
    `_JS_NUMBER`). Does not cover a short one, nor the read's own names (`_JS_PLAIN`'s limit)."""
    scanned = _scan_strings(expression)
    if scanned is None:
        return []
    strings = [s for s in scanned[1] if not _JS_PLAIN.fullmatch(s) or _DIGIT_RUN.search(s)]
    return strings + [n for n in _JS_NUMBER.findall(scanned[0]) if len(n) >= 6]


def _literal(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    raise _Refused


#: The modules code may import, and the functions of each it may call: text and data handling
#: with no I/O. On the T-1519 throwaway gateway the model opened a page's code with an `import
#: re` it never used, and the call asked. Anything else imported still asks.
_MODULES: dict[str, frozenset[str]] = {
    "re": frozenset({"search", "match", "fullmatch", "findall", "finditer", "sub", "subn",
                     "split", "escape"}),
    "json": frozenset({"loads", "dumps"}),
    # A wait for a page to settle (the proof turn's next call); it runs in the harness's own
    # process, under the harness's timeout, so a long one only ends that call.
    "time": frozenset({"sleep", "time", "monotonic"}),
}


#: Statements after which a loop body's later lines may not run.
_BRANCHES = (ast.If, ast.For, ast.Break, ast.Continue)


def _runs_at_least_once(node: ast.AST) -> bool:
    """Is ``node`` `range(n)` or `range(a, b)` over int literals that is not empty?"""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "range" and not node.keywords and 1 <= len(node.args) <= 2):
        return False
    if not all(isinstance(a, ast.Constant) and type(a.value) is int for a in node.args):
        return False
    bounds = [a.value for a in node.args]  # type: ignore[attr-defined]
    return (bounds[0] > 0) if len(bounds) == 1 else bounds[0] < bounds[1]


class _Reader:
    def __init__(self) -> None:
        self.bound: set[str] = set()
        self.modules: set[str] = set()
        self.fixed: dict[str, str] = {}
        self.ready: set[str] = set()
        self.steps: list[Step] = []

    def bind(self, target: ast.AST) -> None:
        # A leading underscore can reach the harness's own globals; `_` alone is the throwaway
        # loop name the model writes in a wait loop (the T-1519 proof turn's retry asked on it).
        if isinstance(target, ast.Name) and (target.id == "_" or not target.id.startswith("_")):
            if target.id in _BUILTINS or target.id in _HELPERS or target.id in self.modules:
                raise _Refused  # shadowing a helper would hide what a later call does
            self.bound.add(target.id)
            # Rebound to a value, the name is no longer the module: `re = 'x'` then `re.sub`.
            self.modules.discard(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self.bind(element)
        else:
            raise _Refused

    def _scope(self) -> tuple[set[str], set[str]]:
        return set(self.bound), set(self.modules)

    def _restore(self, scope: tuple[set[str], set[str]]) -> None:
        self.bound, self.modules = set(scope[0]), set(scope[1])

    def statement(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Expr):
            self.expr(node.value)
        elif isinstance(node, ast.Assign):
            self.expr(node.value)
            for target in node.targets:
                self.bind(target)
        elif isinstance(node, ast.AugAssign):
            self.expr(node.value)
            if not (isinstance(node.target, ast.Name) and node.target.id in self.bound):
                raise _Refused
        # A name read after an `if` or a loop is bound only if every path bound it: one bound
        # where it may not run (a branch not taken, a loop over nothing) is read as whatever
        # the harness's own globals hold (the second review: `if False: os = 'x'`, then `os`).
        elif isinstance(node, ast.For):
            self.expr(node.iter)
            before = self._scope()
            self.bind(node.target)
            # A wait loop over `range(15)` runs its first statements at least once, up to the
            # first that can leave it early: the T-1519 proof turn read `h` after such a loop.
            lead = 0
            while lead < len(node.body) and not isinstance(node.body[lead], _BRANCHES):
                self.statement(node.body[lead])
                lead += 1
            runs = self._scope() if _runs_at_least_once(node.iter) else before
            for inner in node.body[lead:] + node.orelse:
                self.statement(inner)
            self._restore(runs)
        elif isinstance(node, ast.If):
            self.expr(node.test)
            before = self._scope()
            for inner in node.body:
                self.statement(inner)
            taken = self._scope()
            self._restore(before)
            for inner in node.orelse:
                self.statement(inner)
            self.bound &= taken[0]
            self.modules &= taken[1]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in _MODULES or alias.asname is not None or alias.name in self.bound:
                    raise _Refused
                self.modules.add(alias.name)
        elif not isinstance(node, (ast.Pass, ast.Break, ast.Continue)):
            raise _Refused

    def expr(self, node: ast.AST) -> None:
        if isinstance(node, ast.Call):
            self.call(node)
        elif isinstance(node, ast.Constant):
            pass
        elif isinstance(node, ast.Name):
            if node.id not in self.bound and node.id not in _BUILTINS:
                raise _Refused
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for element in node.elts:
                self.expr(element)
        elif isinstance(node, ast.Dict):
            for part in [*node.keys, *node.values]:
                if part is None:
                    raise _Refused
                self.expr(part)
        elif isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    self.expr(part.value)
                    # `f"{x:{y}}"`: the spec is an f-string of its own, and its holes run.
                    if part.format_spec is not None:
                        self.expr(part.format_spec)
                else:
                    self.expr(part)
        elif isinstance(node, ast.Subscript):
            self.expr(node.value)
            self.expr(node.slice)
        elif isinstance(node, ast.Slice):
            for part in (node.lower, node.upper, node.step):
                if part is not None:
                    self.expr(part)
        elif isinstance(node, ast.BinOp):
            self.expr(node.left)
            self.expr(node.right)
        elif isinstance(node, ast.UnaryOp):
            self.expr(node.operand)
        elif isinstance(node, ast.BoolOp):
            for value in node.values:
                self.expr(value)
        elif isinstance(node, ast.Compare):
            self.expr(node.left)
            for value in node.comparators:
                self.expr(value)
        elif isinstance(node, ast.IfExp):
            for part in (node.test, node.body, node.orelse):
                self.expr(part)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            # A comprehension's names are its own: after it, they are the harness's.
            before = self._scope()
            for generator in node.generators:
                self.expr(generator.iter)
                self.bind(generator.target)
                for condition in generator.ifs:
                    self.expr(condition)
            if isinstance(node, ast.DictComp):
                self.expr(node.key)
                self.expr(node.value)
            else:
                self.expr(node.elt)
            self._restore(before)
        else:
            raise _Refused

    def call(self, node: ast.Call) -> None:
        func = node.func
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id in self.modules):
            if func.attr not in _MODULES[func.value.id]:
                raise _Refused
            for value in [*node.args, *(k.value for k in node.keywords)]:
                self.expr(value)
            return
        if isinstance(func, ast.Attribute):
            if func.attr not in _METHODS:
                raise _Refused
            self.expr(func.value)
            for value in [*node.args, *(k.value for k in node.keywords)]:
                self.expr(value)
            return
        if not isinstance(func, ast.Name):
            raise _Refused
        if func.id in _BUILTINS and func.id not in self.bound:
            for value in [*node.args, *(k.value for k in node.keywords)]:
                self.expr(value)
            return
        helper = _HELPERS.get(func.id)
        if helper is None:
            raise _Refused
        helper(self, node)


def _no_args(reader: _Reader, node: ast.Call) -> None:
    if node.args or node.keywords:
        raise _Refused
    reader.steps.append(("browser_snapshot", {}))


def _navigate(reader: _Reader, node: ast.Call) -> None:
    if node.keywords or len(node.args) > 1:
        raise _Refused
    if not node.args:  # new_tab() opens a blank tab
        reader.steps.append(("browser_snapshot", {}))
        return
    url = _literal(node.args[0])
    if not url.lower().startswith(_SCHEMES):
        raise _Refused  # file:, chrome:, data:, javascript: and a bare host are not a page fetch
    reader.steps.append(("browser_navigate", {"url": url}))


def _type(reader: _Reader, node: ast.Call) -> None:
    if node.keywords or len(node.args) != 2:
        raise _Refused
    reader.steps.append(("browser_type", {"ref": _literal(node.args[0]), "text": _literal(node.args[1])}))


def _click(reader: _Reader, node: ast.Call) -> None:
    if node.keywords or len(node.args) != 2:
        raise _Refused
    for value in node.args:
        if not (isinstance(value, ast.Constant) and type(value.value) in (int, float)):
            raise _Refused
    reader.steps.append(("browser_click", {}))


_CONVERSIONS: dict[int, Callable[[object], str]] = {
    -1: str, ord("s"): str, ord("r"): repr, ord("a"): ascii}


def _fixed_text(reader: _Reader, node: ast.AST) -> str:
    """A literal, or an f-string whose every hole is a name in ``reader.fixed`` (bound once in
    the whole code, to a string literal) with no format spec: the text it always is."""
    if not isinstance(node, ast.JoinedStr):
        return _literal(node)
    text = []
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            text.append(part.value)
        elif (isinstance(part, ast.FormattedValue) and part.format_spec is None
              and isinstance(part.value, ast.Name) and part.value.id in reader.fixed
              # Assigned already, at the top level: read before that line ran, or after one
              # inside an `if` that did not, the name is whatever the harness's own globals
              # hold (`os`, a helper's state), not the literal.
              and part.value.id in reader.ready
              and part.conversion in _CONVERSIONS):
            text.append(_CONVERSIONS[part.conversion](reader.fixed[part.value.id]))
        else:
            raise _Refused
    return "".join(text)


def _fixed_names(tree: ast.AST) -> dict[str, str]:
    """Names bound exactly once in ``tree``, by ``name = "literal"``: whatever runs first, such
    a name can only ever hold that literal. Every other binding (a loop, a comprehension, an
    augmented or tuple assignment, a second assignment) leaves the name out."""
    bindings: dict[str, int] = {}
    literal: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bindings[node.id] = bindings.get(node.id, 0) + 1
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                bindings[name] = bindings.get(name, 0) + 1
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            literal[node.targets[0].id] = node.value.value
    return {name: value for name, value in literal.items() if bindings.get(name) == 1}


def _js(reader: _Reader, node: ast.Call) -> None:
    expression = _fixed_text(reader, node.args[0]) if len(node.args) == 1 else ""
    if node.keywords or len(node.args) != 1 or not _reads_only(expression):
        raise _Refused
    reader.steps.append(("browser_snapshot", {}))
    # Text that could carry what the turn knows reaches the page's own functions: held as
    # typed text (the typed-text gate asks unless the owner said it).
    reader.steps.extend(("browser_type", {"ref": "js()", "text": text})
                        for text in _js_strings(expression))


def _screenshot(reader: _Reader, node: ast.Call) -> None:
    # A path would let the PNG land anywhere the gateway can write; only the size options pass.
    if node.args:
        raise _Refused
    for keyword in node.keywords:
        if keyword.arg not in ("full", "max_dim") or not isinstance(keyword.value, ast.Constant):
            raise _Refused
    reader.steps.append(("browser_vision", {}))


_HELPERS: dict[str, Any] = {
    "new_tab": _navigate, "goto_url": _navigate, "fill_input": _type, "click_at_xy": _click,
    "js": _js, "capture_screenshot": _screenshot,
    **{name: _no_args for name in _LOOKS},
}


def unparsable(code: object) -> bool:
    """Is ``code`` a string Python cannot compile? The harness ``exec``s it in the gateway's own
    interpreter (``browser_harness/run.py``), so such code raises before any of it runs. Does
    not cover a harness that ran on another Python, whose grammar could differ."""
    if not isinstance(code, str):
        return False
    try:
        ast.parse(code)
    except (SyntaxError, ValueError):
        return True
    except RecursionError:
        return False   # too deep to read is not "cannot run": it asks
    return False


def steps(code: object) -> list[Step] | None:
    """The per-action calls ``code`` makes, in order; ``None`` when it is not helper-only code."""
    if not isinstance(code, str):
        return None
    try:
        tree = ast.parse(code)
        reader = _Reader()
        reader.fixed = _fixed_names(tree)
        for node in tree.body:
            reader.statement(node)
            if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in reader.fixed):
                reader.ready.add(node.targets[0].id)
    except (SyntaxError, ValueError, RecursionError, _Refused):
        return None
    return reader.steps


#: The harness helpers raw code may call and still be one site's browsing (headless-browser
#: SKILL.md lists the vocabulary). Not ``cdp``: raw CDP can navigate, download or open a target
#: anywhere, which no host in the code would show.
_SITE_HELPERS = frozenset({"new_tab", "goto_url", "js", "fill_input", "click_at_xy",
                           "capture_screenshot"}) | _LOOKS
#: A host as the gate and Chrome read it alike: no userinfo, escape, percent or other byte where
#: the two parsers differ (Chrome reads `\` as `/`, so `a.test\@b.test` is a.test to it, b.test to urlsplit).
_NETLOC = re.compile(r"[A-Za-z0-9.-]+(:[0-9]{1,5})?")


def _host(node: ast.AST) -> str:
    url = _literal(node)
    if not url.lower().startswith(_SCHEMES):
        raise _Refused
    parts = urlsplit(url)
    if not _NETLOC.fullmatch(parts.netloc) or "\\" in url:
        raise _Refused
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise _Refused
    return host[4:] if host.startswith("www.") else host


#: The names js() may use when the code is keyed to a site: what reads the page, presses or
#: scrolls it, and computes on what it read. An allowlist, because a denylist of ways off the
#: page always misses one (``Object.assign`` with ``atob`` built an address from words it never
#: named, T-1547). Nothing here navigates, sends, stores, writes markup or builds code.
_JS_NAMES = frozenset({
    "true", "false", "null", "undefined",
    "document", "window", "Array", "JSON", "Math", "String", "Number", "Boolean",
    "querySelector", "querySelectorAll", "getElementById", "getElementsByTagName",
    "getElementsByClassName", "closest", "matches", "parentElement", "children",
    "firstElementChild", "lastElementChild", "nextElementSibling", "previousElementSibling",
    "innerText", "textContent", "value", "checked", "selected", "disabled", "selectedIndex",
    "options", "title", "body", "head", "tagName", "id", "className", "classList", "contains",
    "getAttribute", "hasAttribute", "rows", "cells", "length", "readyState", "scrollHeight",
    "scrollWidth", "scrollTop", "scrollY", "scrollX", "innerHeight", "innerWidth",
    "offsetHeight", "offsetWidth", "getBoundingClientRect", "width", "height",
    "click", "focus", "blur", "scrollTo", "scrollBy", "scrollIntoView",
    "map", "filter", "join", "trim", "slice", "split", "includes", "startsWith", "endsWith",
    "toLowerCase", "toUpperCase", "replace", "indexOf", "from", "isArray", "concat", "reverse",
    "sort", "find", "some", "every", "forEach", "flat", "at",
    "stringify", "parse", "round", "floor", "ceil", "min", "max", "abs", "toFixed", "toString",
})
_JS_STRING = re.compile(r"'[^'\\\n]*'|\"[^\"\\\n]*\"")
_SITE_JS_NAME = re.compile(r"(?<![\w$])[A-Za-z_$][\w$]*")
#: An arrow's parameters: ``e =>`` or ``(e, i) =>``, one lower-case letter each.
_SITE_JS_PARAMS = re.compile(r"\(\s*([a-z](?:\s*,\s*[a-z])*)\s*\)\s*=>|(?<![\w$])([a-z])\s*=>")
#: A computed member (``window['op'+'en']``), plain or optional: it names a property no
#: allowlist can read. A call of what a call returned (``.at(5)('x')``, ``?.('x')``): it may be any function
#: the page holds. ``{``, ``;``, ``/`` and a backtick keep it one expression with no block,
#: comment, regular expression or template. No ``Object``: its ``values(window)`` hands out the
#: page's functions without naming them. No ``:``: a conditional's ``:`` ends an arrow body where
#: _arrow_scopes would not, so a later letter would read as the parameter (T-1551).
_JS_SHAPE = re.compile(r"[\w$)\]]\s*\[|\?\.\s*[\[(]|\)\s*\(|[{};:/`\\'\"]")


#: Printable ASCII only: JavaScript ends a line at \r, U+2028 and U+2029 too, and reads `<!--`
#: and `-->` as comments (Annex B), each a place where the string regex and the engine would
#: part ways about what is code (T-1550).
_JS_PRINTABLE = re.compile(r"[\x20-\x7e]*")
#: An assignment (`=`, `+=` …) or an update (`++`, `--`): a change to the page, not a read.
#: `==`, `===`, `!=`, `<=`, `>=` and `=>` are not. A shift is refused outright: `<<=`, `>>=` and
#: `>>>=` put their `=` after `<` or `>`, where it reads as a comparison (T-1551).
_JS_ASSIGNS = re.compile(r"(?<![=!<>])=(?![=>])|\+\+|--|<<|>>")


def _arrow_scopes(code: str) -> list[tuple[int, int, int, set[str]]]:
    """Each arrow in ``code`` (strings already blanked): where its parameters are declared, and
    the span of its expression body, which runs to the first `,`, `)` or `]` at its own depth."""
    scopes = []
    for m in _SITE_JS_PARAMS.finditer(code):
        letters = set(re.findall(r"[a-z]", m.group(1))) if m.group(1) else {m.group(2)}
        depth, end = 0, len(code)
        for i in range(m.end(), len(code)):
            ch = code[i]
            if ch in "([":
                depth += 1
            elif ch in ")]" or (ch == "," and depth == 0):
                if depth == 0:
                    end = i
                    break
                depth -= 1
        scopes.append((m.start(), m.end(), end, letters))
    return scopes


def _js_stays(source: str) -> bool:
    """Does this js() source only read, press or scroll the page it runs on?

    Printable ASCII with no HTML-like comment; no assignment or update; every name on
    ``_JS_NAMES`` or a one-letter parameter read inside the body of the arrow that declares it,
    and only as the object of a member (``e.innerText``): outside that body, or called or
    passed, it is the page's own one-letter global. Strings are inert data here.
    Does not cover: what a click on the page's own link or button does next, or a page
    script that gave an allowlisted name to a function of its own.
    """
    if not _JS_PRINTABLE.fullmatch(source) or "<!--" in source or "-->" in source:
        return False
    code = _JS_STRING.sub("0", source)
    if _JS_SHAPE.search(code) or _JS_ASSIGNS.search(code):
        return False
    scopes = _arrow_scopes(code)
    for match in _SITE_JS_NAME.finditer(code):
        name, at = match.group(), match.start()
        if name in _JS_NAMES:
            continue
        if code[:at].rstrip().endswith("."):
            return False  # a member name off the list, even a parameter's letter (`window.e`)
        if any(start <= at < decl for start, decl, _, _ in scopes):
            continue
        if not code[match.end():].lstrip().startswith("."):
            return False
        if not any(decl <= at < end and name in letters for _, decl, end, letters in scopes):
            return False
    return True


def _target(node: ast.AST, bound: set[str]) -> None:
    if isinstance(node, ast.Name):
        bound.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            _target(elt, bound)
    elif isinstance(node, ast.Starred):
        _target(node.value, bound)
    elif isinstance(node, ast.Attribute):
        _loads(node.value, bound)
    elif isinstance(node, ast.Subscript):
        _loads(node.value, bound)
        _loads(node.slice, bound)
    else:
        raise _Refused


def _loads(node: ast.AST | None, bound: set[str]) -> None:
    """Every name ``node`` reads is bound by the time it runs, or a builtin or a site helper."""
    if node is None:
        return
    if isinstance(node, (ast.Lambda, ast.NamedExpr, ast.Await, ast.Yield, ast.YieldFrom)):
        raise _Refused
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        inner = set(bound)
        for i, gen in enumerate(node.generators):
            if gen.is_async:
                raise _Refused
            _loads(gen.iter, bound if i == 0 else inner)
            _target(gen.target, inner)
            for cond in gen.ifs:
                _loads(cond, inner)
        if isinstance(node, ast.DictComp):
            _loads(node.key, inner)
            _loads(node.value, inner)
        else:
            _loads(node.elt, inner)
        return
    if isinstance(node, ast.Name):
        if node.id not in bound and node.id not in _BUILTINS and node.id not in _SITE_HELPERS:
            raise _Refused
        return
    for child in ast.iter_child_nodes(node):
        _loads(child, bound)


def _runs(body: list[ast.stmt], bound: set[str]) -> None:
    """Walk ``body`` in the order it runs. A name counts as bound only after a statement that
    binds it on every path: a branch, a loop or a try binds nothing for what comes after it.
    A name the code reads before that is the harness's (os, requests, the environment), so no
    def, class, lambda, parameter, except name, walrus, global or del is read at all."""
    for stmt in body:
        if isinstance(stmt, ast.Expr):
            _loads(stmt.value, bound)
        elif isinstance(stmt, ast.Assign):
            _loads(stmt.value, bound)
            for target in stmt.targets:
                _target(target, bound)
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            _loads(stmt.value, bound)
            _loads(stmt.annotation, bound)
            _target(stmt.target, bound)
        elif isinstance(stmt, ast.AugAssign):
            if isinstance(stmt.target, ast.Name):
                if stmt.target.id not in bound:  # `x += 1` reads x first
                    raise _Refused
            else:
                _target(stmt.target, bound)
            _loads(stmt.value, bound)
        elif isinstance(stmt, ast.For):
            _loads(stmt.iter, bound)
            inner = set(bound)
            _target(stmt.target, inner)
            _runs(stmt.body, inner)
            _runs(stmt.orelse, set(bound))
        elif isinstance(stmt, (ast.While, ast.If)):
            _loads(stmt.test, bound)
            _runs(stmt.body, set(bound))
            _runs(stmt.orelse, set(bound))
        elif isinstance(stmt, ast.With):
            for item in stmt.items:
                _loads(item.context_expr, bound)
                if item.optional_vars is not None:
                    _target(item.optional_vars, bound)
            _runs(stmt.body, bound)
        elif isinstance(stmt, ast.Try):
            if any(h.name for h in stmt.handlers):
                raise _Refused
            _runs(stmt.body, set(bound))
            for handler in stmt.handlers:
                _loads(handler.type, bound)
                _runs(handler.body, set(bound))
            _runs(stmt.orelse, set(bound))
            _runs(stmt.finalbody, set(bound))
        elif isinstance(stmt, ast.Import):
            bound.update(a.name for a in stmt.names)
        elif isinstance(stmt, (ast.Assert, ast.Raise)):
            for child in ast.iter_child_nodes(stmt):
                _loads(child, bound)
        elif not isinstance(stmt, (ast.Pass, ast.Break, ast.Continue)):
            raise _Refused


_NAVIGATE = ("goto_url", "new_tab")


def _goes_first(tree: ast.Module) -> None:
    """The first statement that touches the browser is ``new_tab`` to a literal address, and
    every navigation is a statement of its own at the top level, so it runs, and runs before
    what follows it. Before it, only ``ensure_real_tab()``, which reads nothing; after it, no
    ``ensure_real_tab()``, which may move to another tab the owner has open.

    ``new_tab``, not ``goto_url``: an answer of 204 or a download leaves a tab on the page it
    was on, which for ``goto_url`` is whatever site the tab last showed and for a new tab is a
    blank page. Does not cover a named session's ``new_tab``, which reuses that session's tab.
    """
    top = {id(s.value) for s in tree.body if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)
           and isinstance(s.value.func, ast.Name) and s.value.func.id in _NAVIGATE and s.value.args}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _NAVIGATE and id(node) not in top):
            raise _Refused
    navigated = False
    for stmt in tree.body:
        if isinstance(stmt, ast.Expr) and id(stmt.value) in top:
            if not navigated and stmt.value.func.id != "new_tab":  # type: ignore[attr-defined]
                raise _Refused
            navigated = True
            continue
        helpers = [n.func.id for n in ast.walk(stmt) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Name) and n.func.id in _SITE_HELPERS]
        if navigated:
            # It may move to another tab the owner has open, on another site.
            if "ensure_real_tab" in helpers:
                raise _Refused
        elif helpers and not (helpers == ["ensure_real_tab"] and isinstance(stmt, ast.Expr)):
            raise _Refused
    if not navigated:
        raise _Refused


def site(code: object) -> str | None:
    """The one host raw, browser-shaped ``code`` drives; ``None`` for anything else.

    Browser-shaped: no import beyond ``_MODULES``, no name or attribute that starts with an
    underscore, every call a site helper, a plain builtin, an allowed method or an allowed
    module function; every name read already bound on every path that reaches it (``_runs``);
    the first browser statement a navigation to a literal http(s) address, and every navigation
    a top-level statement (``_goes_first``); every ``js()`` a literal that only reads, presses
    or scrolls the page (``_js_stays``); a helper only ever called, never passed around. All
    navigations on one host (``www.`` and the port ignored).

    Does not cover: where a click or a form's own action lands, or what the page's scripts do
    once loaded; a remembered yes for the site covers those as the owner saw them on the card.
    """
    if not isinstance(code, str):
        return None
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return None
    hosts: set[str] = set()
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    try:
        # A name the code has not bound yet is the harness's (its namespace may hold os,
        # requests or the environment): read, it reaches an allowed method name on something
        # that does I/O. Bound later, in a branch, or as a parameter is not bound (T-1547).
        _runs(tree.body, set())
        # Acting on the page already open, then going to the keyed site, acts on another (T-1547).
        _goes_first(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                raise _Refused
            if isinstance(node, ast.Import) and any(
                    a.name not in _MODULES or a.asname for a in node.names):
                raise _Refused
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                raise _Refused
            if isinstance(node, ast.Name):
                if node.id.startswith("_") or (node.id in _SITE_HELPERS and id(node) not in called):
                    raise _Refused
            if isinstance(node, ast.Call):
                _site_call(node, hosts)
    except (_Refused, RecursionError, ValueError):  # ValueError: urlsplit on a bad IPv6 literal
        return None
    return hosts.pop() if len(hosts) == 1 else None


def _site_call(node: ast.Call, hosts: set[str]) -> None:
    func = node.func
    if isinstance(func, ast.Attribute):
        owner = func.value.id if isinstance(func.value, ast.Name) else ""
        if func.attr not in _MODULES.get(owner, _METHODS):
            raise _Refused
        return
    if not isinstance(func, ast.Name) or func.id not in _SITE_HELPERS | _BUILTINS:
        raise _Refused
    if func.id in ("goto_url", "new_tab"):
        if node.keywords or len(node.args) > 1 or (func.id == "goto_url" and not node.args):
            raise _Refused
        if node.args:
            hosts.add(_host(node.args[0]))
    elif func.id == "js":
        if node.keywords or len(node.args) != 1 or not _js_stays(_literal(node.args[0])):
            raise _Refused
    elif func.id == "capture_screenshot":
        if node.args or any(k.arg not in ("full", "max_dim") for k in node.keywords):
            raise _Refused
