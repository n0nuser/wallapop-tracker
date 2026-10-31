"""Config-driven listing extractor. All target knowledge lives in targets.toml under
[targets.<name>.extract]; this module is a generic engine (no per-target code)."""
import ast, operator, re, unicodedata

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}

# Generic Spanish condition words; a target can override with extract.condition = [[label, regex], ...]
CONDITION = [
    ["broken", r"averiad|(?<!o )no funciona|para piezas|despiece|\broto\b"],
    ["like_new", r"como nuevo|casi nuevo|practicamente (nuevo|a estrenar)|poco uso|muy poco"],
    ["new", r"\b(nuevo|precintad[oa]s?|sin estrenar|a estrenar|sin usar)\b"],
    ["used", r"\busado|segunda mano|marcas de uso|signos de uso|reacondicionad"],
]
# `wallapop item show` reports condition as one of these; map onto the labels above so bargain groups match text-derived ones
DETAIL_CONDITION = {"new": "new", "as_good_as_new": "like_new", "good": "used", "fair": "used", "has_given_it_all": "used"}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _eval(node, env):
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        a, b = _eval(node.left, env), _eval(node.right, env)
        if a is None or b is None or (isinstance(node.op, ast.Div) and b == 0):
            return None
        return OPS[type(node.op)](a, b)
    raise ValueError(f"unsupported expression: {ast.dump(node)}")


def _first(rules, text):
    return next((label for label, rx in rules if re.search(rx, text)), None)


def _number(spec, text):
    for rx, mul in spec["patterns"]:
        m = re.search(rx, text)
        if m:
            return float(m.group(1).replace(",", ".")) * mul
    return spec.get("default")


def extract(target_cfg, title, description, detail=None, price=None):
    """Return fields, numbers, derived values, relevant, compatible, condition for one listing.
    `price` is only needed by derived expressions that use it (e.g. eur_per_cm)."""
    cfg = target_cfg.get("extract", {})
    full = norm(f"{title}\n{description}")
    for rx in cfg.get("strip", []):
        full = re.sub(rx, " ", full, flags=re.S)
    out = {k: _first(rules, full) for k, rules in cfg.get("fields", {}).items()}
    out.update({k: _number(spec, full) for k, spec in cfg.get("numbers", {}).items()})
    env = {**out, "price": price}
    for k, expr in cfg.get("derived", {}).items():
        out[k] = env[k] = _eval(ast.parse(expr, mode="eval"), env)

    # relevance looks at the title only: descriptions carry keyword blobs and cross-sell text
    rel, t = cfg.get("relevant"), norm(title)
    out["relevant"] = True if not rel else bool(
        any(re.search(r, t) for r in rel.get("include", [""])) and not any(re.search(r, t) for r in rel.get("exclude", [])))

    out["compatible"] = None
    for verdict, ref in cfg.get("compatible", []):
        field, label = ref.split("=")
        if out.get(field) == label:
            out["compatible"] = verdict
            break

    c = (detail or {}).get("condition")
    out["condition"] = DETAIL_CONDITION.get(c, c) or _first(cfg.get("condition", CONDITION), full)
    return out
