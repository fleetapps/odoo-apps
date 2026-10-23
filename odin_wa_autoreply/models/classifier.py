"""What a customer's first WhatsApp messages are about, from keywords.

Plain rules, in English and in the Swahili and Sheng customers write in, so
anyone can read why a message got the reply it got. Order matters: a payment
or a claim is service before it is anything else; "personal accident" is a
product the page cannot price before "accident" is a claim.
"""

import re

SERVICE = "service"          # payments, claims, documents: a person, now
OTHER = "advisor"            # something the quote pages cannot price
MEDICAL = "medical"
MOTOR = "motor"
BOTH = "both"                # nothing clear: both links


def _words(*patterns):
    return re.compile("|".join(r"(?:%s)" % p for p in patterns), re.IGNORECASE)


SERVICE_WORDS = _words(
    r"confirmed\.?\s*ksh", r"\bm-?pesa\b", r"\bpaid\b", r"\bnimelipa\b", r"\bnimetuma\b",
    r"\b(made|sent|did) (a |the )?payment\b", r"\bpayment (made|done|sent|confirmation)\b",
    r"\breceipt\b", r"\bkra\b", r"\bclaims?\b", r"(?<!personal )\baccident\b",
    r"\bpolicy (no|number)\b", r"\bcertificate\b", r"\bsticker\b", r"\bcover note\b",
    r"\bdebit note\b", r"\brefund\b", r"\bcomplain", r"\bnishughulikie\b",
)

OTHER_PRODUCTS = _words(
    r"\bpersonal accident\b", r"\btravel\b", r"\blife (cover|insurance|assurance|policy)\b",
    r"\beducation (policy|plan|cover)\b", r"\bpension\b", r"\blast expense\b", r"\bfuneral\b",
    r"\bdomestic\b", r"\b(home|house|property) (insurance|cover)\b", r"\bfire\b", r"\bburglary\b",
    r"\bwiba\b", r"\bwork ?injury\b", r"\bgroup (cover|medical|life|insurance)\b",
    r"\bstaff\b", r"\bemployees?\b", r"\bfleet\b", r"\bmarine\b", r"\bgoods in transit\b",
    r"\bcontractors?\b", r"\bprofessional indemnity\b", r"\bbonds?\b", r"\bpublic liability\b",
    r"\bcrops?\b", r"\blivestock\b",
)

# Medical cover the page cannot price: it quotes you, a partner and children.
OTHER_PEOPLE = _words(
    r"\b(mum|mom|mother|dad|father|parents?|siblings?|brothers?|sisters?|in-?laws?)\b",
    r"\bgrand(ma|pa|mother|father|parents?)\b", r"\bmama\b", r"\bbaba\b",
)

MEDICAL_WORDS = _words(
    r"\bmedical\b", r"\bhealth\b", r"\bhospital", r"\binpatient\b", r"\boutpatient\b",
    r"\bmaternity\b", r"\bnhif\b", r"\bsha\b", r"\bafya\b", r"\bmatibabu\b", r"\bdoctor\b",
    r"\bdental\b", r"\boptical\b", r"\bsenior citizens?\b", r"\bfamily\b", r"\bcouple\b",
    r"\b(wife|husband|spouse|kids|children|child|baby)\b",
)

BRANDS = (
    r"\b(toyota|nissan|mazda|subaru|honda|mitsubishi|isuzu|mercedes|benz|bmw|volkswagen|vw|"
    r"suzuki|hyundai|kia|peugeot|ford|audi|lexus|probox|succeed|fielder|vitz|ist|axio|premio|"
    r"allion|noah|voxy|harrier|prado|land ?cruiser|hilux|demio|tiida|x-?trail|forester|"
    r"impreza|outback|wingroad|ractis|passo|belta|mark ?x|crown|rav ?4|cx-?5)\b"
)
PLATE = r"\bk[a-z]{2}\s?\d{3}\s?[a-z]?\b"

MOTOR_WORDS = _words(
    r"\bcars?\b", r"\bvehicles?\b", r"\bmotor\b", r"\bthird[- ]?party\b", r"\b3rd party\b",
    r"\btpo\b", r"\bpick[- ]?ups?\b", r"\blorry\b", r"\btrucks?\b", r"\bmatatu\b", r"\bpsv\b",
    r"\bboda\b", r"\bmotor ?(bike|cycle)s?\b", r"\bpikipiki\b", r"\btuk[- ]?tuk\b", r"\buber\b",
    r"\btaxi\b", r"\bgari\b", r"\btonn?e?s?\b", r"\b\d+\s?tonn?e?s?\b",
    BRANDS, PLATE,
)

# Questions a person should answer, alongside the link.
QUESTIONS = _words(
    r"\bwhich hospitals?\b", r"\b(list|lists) of (the )?hospitals?\b", r"\bhospital lists?\b",
    r"\bpanel\b", r"\bnetwork\b", r"\bbenefits?\b", r"\bwhat do i get\b", r"\bexplain\b",
    r"\bdetails?\b",
)

STOP_WORDS = re.compile(r"^\s*(stop|unsubscribe|opt[- ]?out)\s*[.!]*\s*$", re.IGNORECASE)

# Prefill: what the message already says, so the page skips a question.
WHO = (
    ("family", _words(r"\bfamily\b", r"\bfamily of [3-9]\b",
                      r"\b(wife|husband|spouse|couple)\b.*\b(kids|children|child)\b",
                      r"\b(kids|children|child)\b.*\b(wife|husband|spouse|couple)\b")),
    ("couple", _words(r"\bcouple\b", r"\b(me|myself) and my (wife|husband|spouse|partner)\b")),
    ("me_kids", _words(r"\b(me|myself) and my (kids|children|child)\b")),
    ("me", _words(r"\bone person\b", r"\bmyself\b", r"\bsingle\b", r"\bindividual\b",
                  r"\bfor me\b", r"\bjust me\b")),
)
VEHICLE = (
    ("odin_motor_quote.class_pickup", _words(r"\bpick[- ]?ups?\b")),
    ("odin_motor_quote.class_commercial", _words(r"\blorry\b", r"\btrucks?\b", r"\btonn?e?s?\b",
                                                  r"\b\d+\s?tonn?e?s?\b")),
    ("odin_motor_quote.class_motorcycle_psv", _words(r"\bboda\b")),
    ("odin_motor_quote.class_tuktuk_psv", _words(r"\btuk[- ]?tuk\b")),
    ("odin_motor_quote.class_motorcycle_private", _words(r"\bmotor ?(bike|cycle)s?\b",
                                                          r"\bpikipiki\b")),
    ("odin_motor_quote.class_psv", _words(r"\bmatatu\b", r"\bpsv\b", r"\btaxi\b", r"\buber\b")),
    ("odin_motor_quote.class_private", _words(r"\bcars?\b", r"\bgari\b", BRANDS, PLATE)),
)
THIRD_PARTY = _words(r"\bthird[- ]?party\b", r"\b3rd party\b", r"\btpo\b")
COMPREHENSIVE = _words(r"\bcomprehensive\b")
ONE_MONTH = _words(r"\b(one|1) ?months?\b", r"\bmwezi (mmoja|1)\b")
SIX_MONTHS = _words(r"\b(six|6) ?months\b", r"\bmiezi (sita|6)\b")


def is_stop(text):
    return bool(STOP_WORDS.match(text or ""))


def classify(text):
    """``(kind, hints)`` for the customer's words.

    ``hints`` are what the quote page can be opened with: ``who`` for
    medical; ``vehicle`` (an xmlid), ``cover`` and ``period`` for motor;
    ``question`` when there is also something for a person to answer.
    """
    text = " ".join((text or "").split())
    if not text:
        return BOTH, {}
    if SERVICE_WORDS.search(text) and not OTHER_PRODUCTS.search(text):
        return SERVICE, {}
    medical = bool(MEDICAL_WORDS.search(text))
    motor = bool(MOTOR_WORDS.search(text))
    question = {"question": True} if QUESTIONS.search(text) else {}
    if OTHER_PRODUCTS.search(text) and not (medical or motor):
        return OTHER, {}
    if medical and OTHER_PEOPLE.search(text):
        return OTHER, {}
    if medical and not motor:
        who = next((key for key, words in WHO if words.search(text)), None)
        return MEDICAL, dict(question, **({"who": who} if who else {}))
    if motor and not medical:
        hints = dict(question)
        vehicle = next((xmlid for xmlid, words in VEHICLE if words.search(text)), None)
        if vehicle:
            hints["vehicle"] = vehicle
        if THIRD_PARTY.search(text):
            hints["cover"] = "tpo"
            if ONE_MONTH.search(text):
                hints["period"] = 1
            elif SIX_MONTHS.search(text):
                hints["period"] = 6
        elif COMPREHENSIVE.search(text):
            hints["cover"] = "comprehensive"
        return MOTOR, hints
    if OTHER_PRODUCTS.search(text):
        return OTHER, {}
    return BOTH, {}
