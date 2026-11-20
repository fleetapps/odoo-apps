"""Which item of the catalogue a line on an invoice photo is, and which
customer the invoice is for: worked out here, in code, from what the model
read off the photo. The model reads; it does not choose an item unless the
rules below leave several close ones, and then only among the ones they
allow.

What tells near-misses apart, in the order it counts:

* the item's **code**, when the invoice prints it;
* its **kind**: shampoo, conditioner, lotion, handwash... Two items of the
  same range are the same words but one: "Avocado & Grapeseed Shampoo" and
  "... Conditioner". An item of another kind is never matched;
* its **size**: 500 ml, 1000 ml (1 l), 5 litre. Another size is never matched;
* **bulk or packaged**: an item kept in bulk (sold by the litre, under a
  Bulk category) is matched only when the invoice says bulk;
* the other **words**, weighed by how rare they are in the catalogue
  ("grapeseed" says more than "hair"), allowing abbreviations (REG, AVO)
  and words run together (grapeseed, grape seed);
* the customer's own **history** and **pricelist price**, to break a tie.

Nothing here touches the database: the Odoo side gives the catalogue as
plain dicts, so the rules are tested on their own.
"""

import math
import re
from difflib import SequenceMatcher

# Kinds of product: two items whose kinds have nothing in common are never
# the same item. Each kind lists the ways invoices write it (whole words,
# after the text is lowered and split on anything not a letter or digit).
KINDS = {
    "shampoo": ("shampoo", "shampoos", "shampo", "shamp", "shmp"),
    "conditioner": ("conditioner", "conditioners", "conditioning", "cond", "condtnr", "condr", "cdtnr"),
    "lotion": ("lotion", "lotions", "lotn"),
    "handwash": ("handwash", "hand wash", "h wash", "hwash"),
    "dishwash": ("dishwash", "dish wash", "dishwashing", "dish washing", "d wash", "washing up"),
    "multipurpose": ("multipurpose", "multi purpose", "m purpose", "all purpose", "allpurpose"),
    "bleach": ("bleach",),
    "detergent": ("detergent", "washing powder", "laundry"),
    "softener": ("softener", "fabric conditioner"),
    "sanitizer": ("sanitizer", "sanitiser", "sanitizing", "sanitising"),
    "toilet cleaner": ("toilet",),
    "glass cleaner": ("glass",),
    "floor cleaner": ("floor",),
    "jelly": ("jelly",),
    "oil": ("oil",),
    "cream": ("cream",),
    "soap": ("soap", "soaps"),
    "gel": ("gel",),
    "body wash": ("body wash", "shower"),
    "air freshener": ("air freshener", "freshener"),
    "degreaser": ("degreaser",),
    "polish": ("polish",),
}
# "fabric conditioner" is a softener, not a hair conditioner.
_KIND_SEQUENCES = sorted(
    ((tuple(alias.split()), kind) for kind, aliases in KINDS.items() for alias in aliases),
    key=lambda item: -len(item[0]))

# Words that say nothing about which item it is.
STOPWORDS = {
    "and", "the", "of", "with", "for", "in", "on", "a", "an", "x", "by",
    "pcs", "pc", "pce", "pces", "piece", "pieces", "no", "nos", "each", "ea", "per", "unit", "units",
    "pack", "pkt", "pkts", "ctn", "ctns", "carton", "cartons", "box", "case", "dozen", "dz",
    "btl", "btls", "bottle", "bottles", "new", "size", "only", "item",
}

# A size: 500 ml, 1000ml, 1 l, 5 Litre, 1.5L, 500Mlx12 (12 of 500 ml), 1,000 ML.
_SIZE = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+(?:[.,]\d+)?)\s*"
    r"(ml|mls|millilitres?|milliliters?|cl|l|lt|lts|ltr|ltrs|litres?|liters?|kg|kgs|kilos?|kilograms?|"
    r"g|gm|gms|grams?|grammes?)(?![a-wyz])")
_VOLUME = {"ml": 1, "mls": 1, "cl": 10, "l": 1000, "lt": 1000, "lts": 1000, "ltr": 1000, "ltrs": 1000}
_MASS = {"kg": 1000, "kgs": 1000, "g": 1, "gm": 1, "gms": 1}

# How an invoice or a unit of measure counts: pieces, litres, kilos, cartons.
UNIT_FAMILIES = {
    "piece": {"pcs", "pc", "pce", "pces", "piece", "pieces", "unit", "units", "each", "ea", "no", "nos", "btl",
              "btls", "bottle", "bottles", "tin", "tins", "jar", "jars", "sachet", "sachets"},
    "volume": {"l", "lt", "lts", "ltr", "ltrs", "litre", "litres", "liter", "liters", "ml"},
    "mass": {"kg", "kgs", "kilo", "kilos", "g", "gm", "gms", "gram", "grams"},
    "pack": {"ctn", "ctns", "carton", "cartons", "box", "boxes", "case", "cases", "dozen", "dozens", "dz", "pack",
             "packs", "pkt", "pkts", "bale", "bales"},
}

SURE, CHECK, NONE = "sure", "check", "none"


# ----------------------------------------------------------------------------- text
def _plain(text):
    text = (text or "").lower().replace("&", " and ").replace("+", " and ")
    return text


def tokens(text):
    """Lowercase words and numbers, split on anything else."""
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", _plain(text))


def sizes(text):
    """The sizes written in the text, as ("volume", ml) or ("mass", g)."""
    found = set()
    for number, unit in _SIZE.findall(_plain(text)):
        number = number.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+", number) else number.replace(",", ".")
        value = float(number)
        unit = unit.rstrip("s") if unit not in _VOLUME and unit not in _MASS else unit
        if unit in _VOLUME or unit.startswith(("millilit", "lit")):
            factor = _VOLUME.get(unit, 1 if unit.startswith("millilit") else 1000)
            found.add(("volume", round(value * factor, 3)))
        elif unit in _MASS or unit.startswith(("kilo", "gram", "gramme")):
            factor = _MASS.get(unit, 1000 if unit.startswith("kilo") else 1)
            found.add(("mass", round(value * factor, 3)))
    return found


def _without_sizes(text):
    return _SIZE.sub(" ", _plain(text))


def kinds(words):
    """The kinds named in a list of words, and the positions of those words."""
    found, used = set(), set()
    for start in range(len(words)):
        for sequence, kind in _KIND_SEQUENCES:
            end = start + len(sequence)
            if tuple(words[start:end]) == sequence and not used.intersection(range(start, end)):
                found.add(kind)
                used.update(range(start, end))
    return found, used


def says_bulk(text):
    return "bulk" in tokens(text)


def unit_family(text):
    for word in tokens(text):
        for family, words in UNIT_FAMILIES.items():
            if word in words:
                return family
    return False


def describe_size(size):
    dimension, value = size
    if dimension == "volume":
        return f"{value / 1000:g} l" if value >= 1000 and value % 1000 == 0 and value > 1000 else f"{value:g} ml"
    return f"{value / 1000:g} kg" if value >= 1000 and value % 1000 == 0 else f"{value:g} g"


class Text:
    """What one name or invoice line says: its kinds, sizes and other words."""

    def __init__(self, text, extra=""):
        self.raw = " ".join(f"{text or ''} {extra or ''}".split())
        words = tokens(_without_sizes(self.raw))
        self.kinds, used = kinds(words)
        self.sizes = sizes(self.raw)
        self.bulk = "bulk" in words
        self.words = [w for i, w in enumerate(words)
                      if i not in used and w not in STOPWORDS and len(w) > 1 and w != "bulk"
                      and not re.fullmatch(r"\d+(?:\.\d+)?", w)]


# ----------------------------------------------------------------------------- the catalogue
def _similar(a, b):
    if a == b:
        return True
    if min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)):
        return True
    return min(len(a), len(b)) >= 5 and SequenceMatcher(None, a, b).ratio() >= 0.85


class Catalogue:
    """The items an invoice line can be: dicts with ``id``, ``code``,
    ``name``, ``category``, ``uom``, and optionally ``uom_family``."""

    def __init__(self, products):
        self.products = []
        for product in products:
            text = Text(product["name"])
            bulk = text.bulk or "bulk" in tokens(product.get("category")) or "bulk" in tokens(product.get("code"))
            self.products.append({**product, "text": text, "bulk": bulk,
                                  "uom_family": product.get("uom_family") or unit_family(product.get("uom"))})
        count = len(self.products) or 1
        seen = {}
        for product in self.products:
            for word in set(product["text"].words):
                seen[word] = seen.get(word, 0) + 1
        self.idf = {word: 1.0 + math.log(count / df) for word, df in seen.items()}
        self.rare = 1.0 + math.log(count + 1)
        self.codes = {(p.get("code") or "").strip().lower(): p for p in self.products if (p.get("code") or "").strip()}

    def weight(self, word):
        if word in self.idf:
            return self.idf[word]
        near = [w for w in self.idf if _similar(word, w)]
        return max((self.idf[w] for w in near), default=self.rare)

    def _covered(self, line_words, product_words):
        """Which line words and which product words match each other: equal,
        an abbreviation of the other, a near spelling, or two words of one
        side written as one on the other (grape seed, grapeseed)."""
        joined = {product_words[i] + product_words[i + 1]: (i, i + 1) for i in range(len(product_words) - 1)}
        line_hit, product_hit = set(), set()
        for i, word in enumerate(line_words):
            for j, other in enumerate(product_words):
                if _similar(word, other):
                    line_hit.add(i)
                    product_hit.add(j)
            for pair, (a, b) in joined.items():
                if word == pair:
                    line_hit.add(i)
                    product_hit.update((a, b))
        for i in range(len(line_words) - 1):
            pair = line_words[i] + line_words[i + 1]
            for j, other in enumerate(product_words):
                if pair == other:
                    line_hit.update((i, i + 1))
                    product_hit.add(j)
        return line_hit, product_hit

    def rank(self, description, *, code="", unit="", rate=None, prices=None, history=()):
        """Every item the line could be, best first, and those ruled out.

        :param prices: {product id: the customer's price, VAT as on the
            invoice}, for the items it is known for.
        :param history: ids of the items the customer bought before.
        :return: (candidates, excluded), each a list of dicts with the
            product, its ``score`` and the ``notes`` that explain it.
        """
        line = Text(description)
        code = (code or "").strip().lower()
        coded = self.codes.get(code) if code else None
        if not coded:
            # A code written into the description: "FIN-PAK-040 Avocado...".
            for word in re.findall(r"[a-z0-9][a-z0-9\-/.]{3,}", _plain(description)):
                if word in self.codes:
                    coded = self.codes[word]
                    break
        family = unit_family(unit)
        line_weights = [self.weight(w) for w in line.words]
        line_total = sum(line_weights) or 1.0
        candidates, excluded = [], []
        for product in self.products:
            text = product["text"]
            line_hit, product_hit = self._covered(line.words, text.words)
            product_weights = [self.weight(w) for w in text.words]
            cover_line = sum(line_weights[i] for i in line_hit) / line_total if line.words else 1.0
            cover_item = (sum(product_weights[j] for j in product_hit) / sum(product_weights)) if text.words else 1.0
            words_score = 0.65 * cover_line + 0.35 * cover_item
            score, notes, ruled_out = words_score, [], []
            if product is coded:
                score += 1.0
                notes.append(("info", "code", "its code is on the invoice"))
            # The kind.
            if line.kinds and text.kinds:
                if not line.kinds & text.kinds:
                    ruled_out.append(f"a {'/'.join(sorted(text.kinds))}; the invoice says {'/'.join(sorted(line.kinds))}")
                elif line.kinds == text.kinds:
                    score += 0.15
                else:
                    score -= 0.1
                    notes.append(("warn", "kind", f"the item is {'/'.join(sorted(text.kinds))}, the invoice says "
                                                  f"{'/'.join(sorted(line.kinds))}"))
            elif line.kinds:
                score -= 0.05
                notes.append(("warn", "kind", f"the invoice says {'/'.join(sorted(line.kinds))}, the item's name "
                                              "does not say what it is"))
            elif text.kinds:
                # A unique match still: the margin to the next one decides.
                score -= 0.05
                notes.append(("info", "kind", f"the item is a {'/'.join(sorted(text.kinds))}, the invoice does not "
                                              "say what it is"))
            # The size.
            if line.sizes and text.sizes:
                if not line.sizes & text.sizes:
                    ruled_out.append(f"{', '.join(describe_size(s) for s in sorted(text.sizes))}; the invoice says "
                                     f"{', '.join(describe_size(s) for s in sorted(line.sizes))}")
                else:
                    score += 0.15
            elif line.sizes and not product["bulk"]:
                score -= 0.15
                notes.append(("warn", "size", f"the invoice says {describe_size(min(line.sizes))}, the item's name "
                                              "gives no size"))
            elif text.sizes and not line.bulk:
                score -= 0.1
                notes.append(("info", "size", f"the item is {describe_size(min(text.sizes))}, the invoice gives no "
                                              "size"))
            # Bulk or packaged.
            if product["bulk"] and not line.bulk:
                ruled_out.append("a bulk item; the invoice does not say bulk")
            elif line.bulk and not product["bulk"]:
                score -= 0.2
                notes.append(("warn", "bulk", "the invoice says bulk, the item is packaged"))
            elif line.bulk:
                score += 0.1
            # How it is counted.
            if family and product["uom_family"] and family != product["uom_family"]:
                score -= 0.15
                notes.append(("warn", "unit", f"the invoice counts in {unit.strip()}, the item in {product.get('uom')}"))
            if product["id"] in history:
                score += 0.05
                notes.append(("info", "history", "the customer bought it before"))
            price = (prices or {}).get(product["id"])
            if rate and price and abs(rate - price) <= max(0.01, 0.01 * price):
                score += 0.1
                notes.append(("info", "price", "the rate is its pricelist price"))
            entry = {"product": product, "score": round(score, 4), "words": round(words_score, 4), "notes": notes}
            if ruled_out and product is not coded:
                excluded.append({**entry, "why": ruled_out})
            else:
                if ruled_out:
                    notes.extend(("warn", "code", f"its code is on the invoice, but it is {why}") for why in ruled_out)
                candidates.append(entry)
        candidates.sort(key=lambda c: (-c["score"], c["product"].get("code") or "", c["product"]["id"]))
        excluded.sort(key=lambda c: (-c["words"], c["product"]["id"]))
        return candidates, excluded

    def match(self, description, **kwargs):
        """The item for one invoice line: the best candidate, how sure, the
        close ones and the near-misses ruled out (with why)."""
        candidates, excluded = self.rank(description, **kwargs)
        best = candidates[0] if candidates else None
        second = candidates[1] if len(candidates) > 1 else None
        coded = best and any(note[1] == "code" and note[0] == "info" for note in best["notes"])
        if not best or (best["words"] < 0.35 and not coded):
            confidence = NONE
        elif coded and not any(note[0] == "warn" for note in best["notes"]):
            confidence = SURE
        elif (best["score"] >= 0.8 and not any(note[0] == "warn" for note in best["notes"])
              and (not second or best["score"] - second["score"] >= 0.15)):
            confidence = SURE
        else:
            confidence = CHECK
        close = [c for c in candidates[1:6] if best and best["score"] - c["score"] < 0.35 and c["words"] >= 0.35]
        near_misses = [c for c in excluded[:3] if c["words"] >= 0.5]
        return {"best": best if confidence != NONE else None, "confidence": confidence, "close": close,
                "candidates": candidates[:6], "excluded": near_misses}


# ----------------------------------------------------------------------------- customers
LEGAL_WORDS = {"ltd", "limited", "plc", "co", "company", "inc", "llc", "the", "and", "of", "kenya", "k", "epz"}


def _name_words(text):
    return [w for w in tokens(text) if w not in LEGAL_WORDS and len(w) > 1]


def _overlap(words, other):
    """Share of ``words`` found in ``other``, allowing abbreviations."""
    if not words:
        return 0.0
    return sum(1 for w in words if any(_similar(w, o) for o in other)) / len(words)


def match_customer(printed_name, printed_branch, printed_pin, partners):
    """The customer the invoice is for, among ``partners``: dicts with
    ``id``, ``name``, ``parent_id`` (False for a company), ``vat`` (of the
    company, for a branch too) and ``children`` (how many contacts it has
    under it).

    A branch is a contact under the customer: "Chandarana Supermarket,
    Hookwood Square - Brookside Grove". The invoice names the customer and,
    often, the branch: on a line of its own, or run into the name.

    Each partner is scored on how much of the company's name is printed
    (``company_score``), how much of what is printed it explains, and
    whether its branch is the one printed (a company without a branch only
    when none is).
    """
    name_words = _name_words(printed_name)
    branch_words = _name_words(printed_branch)
    pin = "".join((printed_pin or "").split()).upper()
    by_id = {p["id"]: p for p in partners}
    ranked = []
    for partner in partners:
        company = by_id.get(partner["parent_id"]) if partner["parent_id"] else partner
        if company is None:
            continue
        company_words = _name_words(company["name"])
        own = _name_words(partner["name"]) if partner["parent_id"] else []
        # What the printed name has besides the branch's own words, and besides the company's.
        name_rest = [w for w in name_words if not any(_similar(w, o) for o in own)]
        beyond = [w for w in name_words
                  if not any(_similar(w, c) or (len(w) >= 3 and w in c) for c in company_words)]
        company_score = 0.6 * _overlap(company_words, name_words) + 0.4 * _overlap(name_rest, company_words)
        if pin and "".join((company.get("vat") or "").split()).upper() == pin:
            company_score = max(company_score, 1.0)
        explained = _overlap(name_words + branch_words, company_words + own)
        if partner["parent_id"]:
            printed = branch_words or beyond
            branch_score = max(_overlap(own, name_words + branch_words), _overlap(printed, own) if printed else 0.0)
            fits = branch_score
        else:
            branch_score = None
            fits = 0.0 if (branch_words or beyond) else 1.0
        score = 0.5 * company_score + 0.3 * explained + 0.2 * fits
        ranked.append({"partner": partner, "company": company, "score": round(score, 4),
                       "company_score": company_score, "branch_score": branch_score,
                       "branch_printed": " ".join(branch_words or beyond) if not partner["parent_id"] else ""})
    ranked.sort(key=lambda r: (-r["score"], r["partner"]["id"]))
    best = ranked[0] if ranked else None
    notes = []
    if not best or best["company_score"] < 0.6:
        return {"best": None, "confidence": NONE, "alternatives": ranked[:4], "notes": notes}
    second = next((r for r in ranked[1:] if r["partner"]["id"] != best["partner"]["id"]), None)
    clear = not second or best["score"] - second["score"] >= 0.15
    sure = clear and best["company_score"] >= 0.8
    if best["branch_score"] is None and best["branch_printed"]:
        # A branch on the invoice that is not a contact under the customer.
        has_branches = best["company"].get("children", 0) > 0
        notes.append(("warn", "branch", has_branches, " ".join(printed_branch.split()) or best["branch_printed"]))
        confidence = CHECK if has_branches or not sure else SURE
    elif best["branch_score"] is not None:
        confidence = SURE if sure and best["branch_score"] >= 0.7 else CHECK
    else:
        confidence = SURE if sure else CHECK
    alternatives = [r for r in ranked[1:4] if r["company_score"] >= 0.6]
    return {"best": best, "confidence": confidence, "alternatives": alternatives, "notes": notes}
