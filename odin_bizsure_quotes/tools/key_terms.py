"""Key terms: what a client must know before buying, in about half a page.

The full terms ran to one and a half pages per insurer, so a quotation from
three insurers carried four pages that nobody read. These keep, for every
product and in the same order, what the client needs and what protects
Bizsure: how the premium is worked out (the levies and stamp duty are in the
quotation's table and its footnote), who can join, waiting periods,
co-payments, what to do before treatment, the main exclusions, how to pay,
and that this is a quotation under the insurer's underwriting, on full
disclosure, with the insurer's policy document prevailing.

Read from each insurer's own terms, brochure or rate card (see
tools/rate_cards.py); the validity date and who prepared the quote are
printed by the quotation itself, so they are not repeated here.

Each is under SHORT_TERMS (2,000 characters, odin_medical_quote), so the
quotation keeps it whole on one page with its heading; tests/test_key_terms.py
checks every product.
"""

from xml.sax.saxutils import escape

def please_note(insurer, extra="", for_life=False):
    return ("This is a quotation, not cover: cover starts once %(i)s accepts the application "
            "and has the full premium. Declare all health conditions, or claims can be refused "
            "and cover cancelled%(life)s.%(extra)s %(i)s's policy document prevails over this "
            "summary."
            % {"i": insurer, "extra": (" " + extra) if extra else "",
               "life": "; an undeclared one stays excluded for life" if for_life else ""})


#: The sections every product's key terms has, in this order. "Co-payments"
#: and "Before treatment" are left out where the insurer has none to state.
SECTIONS = ("Your premium", "Who can join", "Waiting periods", "Co-payments",
            "Before treatment", "Not covered", "How to pay", "Please note")

APA_COPAY = (
    "KES 1,000 per outpatient visit at Aga Khan and Nairobi Hospital; KES 500 at Mater, MP "
    "Shah, AAR, Gertrude's, Premier, Mombasa Hospital, Diani Beach, Pandya, Jocham, Palm Beach, "
    "Eldoret Hospital, Oasis and Mediheal; none elsewhere.")
APA_COMMON = {
    "Waiting periods": (
        "Accidents: none. Illness: 30 days. New chronic conditions: 3 months. Other surgery: "
        "90 days. Pre-existing, chronic, congenital, palliative care, fibroids, hernia, "
        "tonsils and similar: 12 months. Maternity: 10 months. Transplants: 2 years. A gap "
        "over 30 days restarts them."),
    "Co-payments": APA_COPAY,
    "Before treatment": (
        "Use APA's panel; elsewhere APA refunds up to 85% of its rates, claimed within 90 "
        "days. Tell APA 48 hours before a planned admission."),
    "Not covered": (
        "Costs that SHIF, WIBA or GPA pay, cosmetic surgery unless after an accident, "
        "infertility, self-injury, drunkenness, war, riots, weight management, glasses and "
        "dental unless bought, and APA's other exclusions."),
    "How to pay": (
        "Pay APA directly: NCBA Bank account 6447560039 (Westlands) or M-Pesa Paybill "
        "511600, account: the principal's name (renewals: policy number). Keep APA's "
        "receipt."),
    "Please note": please_note(
        "APA", "Either side may cancel on 14 days' notice; no refund after 6 months or once "
        "a claim is made.", for_life=True),
}

JUBILEE_COMMON = {
    "Waiting periods": (
        "Accidents: none. Illness: 30 days. Pre-existing, chronic, psychiatric, congenital, "
        "HIV/AIDS, transplants, maternity, gynaecological, non-accident eye or dental "
        "surgery, hernia, "
        "tonsils, thyroid and similar: 1 year. Cancer: 2 years."),
    "Co-payments": (
        "KES 2,000 per outpatient visit at Nairobi Hospital (main), MP Shah (Parklands), "
        "Karen, Aga Khan (Parklands), Gertrude's (Muthaiga) and AAR (Kiambu Rd); KES 1,000 at "
        "Mater (main), Avenue (Parklands), Mediheal, Eldoret Hospital, St Luke's (Eldoret), "
        "Mombasa Hospital and Premier (Nyali); none elsewhere."),
    "Before treatment": (
        "Planned admissions: pre-authorised 48 hours ahead. Abroad: accident and emergency "
        "care for 6 weeks a trip, refunded."),
    "How to pay": (
        "Pay Jubilee directly: M-Pesa Paybill 7195247, a cheque to Jubilee Health Insurance "
        "Ltd, or a deposit to its bank account. Keep Jubilee's receipt."),
    "Please note": please_note("Jubilee"),
}
JUBILEE_NOT_COVERED = (
    "Cosmetic surgery unless after an accident, alternative treatment, "
    "self-injury, alcohol and drugs, war and riots, hazardous sports, weight and hormone "
    "treatment, %spandemics beyond the Covid-19 limits, glasses and dental unless bought, and "
    "Jubilee's other exclusions.")

GA_HOW_TO_PAY = (
    "No cash. Pay GA Insurance Limited: I&M Bank account 00200208651202 (2nd Ngong Avenue), "
    "M-Pesa Paybill 870250 (account number from your Bizsure advisor), a cheque or a card.")

#: product -> (heading, {section: text})
KEY_TERMS = {
    "apa_family": ("APA Jamii Plus – key terms", dict(APA_COMMON, **{
        "Your premium": (
            "Annual, at APA's May 2026 rates, by each member's age; one inpatient limit for "
            "the family."),
        "Who can join": (
            "From day 1 (full-term baby). Principal and spouse join up to 80 (KES 5M plan: 75; "
            "7.5M and 10M: 70); medical exam from 60, at their cost. A spouse, or a child over "
            "3 months, added later goes on a separate policy."),
    })),
    "apa_child_only": ("APA Jamii Plus Child-Only – key terms", dict(APA_COMMON, **{
        "Your premium": (
            "Annual, per child, at APA's child-only rates (May 2026)."),
        "Who can join": (
            "Children only, from day 1 (full-term baby). A child over 3 months added later "
            "goes on a separate policy."),
    })),
    "birdview": ("Birdview Bizsure Afya – key terms", {
        "Your premium": (
            "Annual, by the number of people covered; separate rates where a member is 70 "
            "or over."),
        "Who can join": (
            "Adults 18 to 90 at entry. Children from birth (37-week term baby), and up to 25 "
            "if living with their parents and in full-time post-secondary study. No medical "
            "exam."),
        "Waiting periods": (
            "Accidents: none. Illness: 30 days. Pre-existing, chronic and related conditions, "
            "prematurity and congenital conditions, and non-accident inpatient dental and "
            "optical: 6 months. Maternity and pregnancy complications: 10 months. Waived "
            "like-for-like when moving from another insurer."),
        "Co-payments": "None on outpatient.",
        "Before treatment": (
            "MRI, CT and PET scans and planned admissions (48 hours ahead) need "
            "pre-authorisation. Abroad: accident and emergency treatment for 6 weeks a trip, "
            "refunded."),
        "Not covered": (
            "Cosmetic surgery unless after an accident, alternative treatment, beauty "
            "treatment, old-age homes, genetic conditions, supplements, war, military action, "
            "riots and terrorism, alcohol and drug addiction, weight loss, epidemics, "
            "pandemics and unknown diseases, and Birdview's other exclusions."),
        "How to pay": (
            "In full before cover starts; your Bizsure advisor gives you Birdview's payment "
            "details."),
        "Please note": please_note("Birdview"),
    }),
    "ga_community": ("GA Community Health – key terms", {
        "Your premium": (
            "Annual, by the number of people covered (up to 7), for hospital cover only; "
            "pandemic cover only where bought."),
        "Who can join": (
            "Adults 19 to 65, joining up to 60. Children from birth (38-week term baby) to 18. "
            "Over 60: GA's Hadhi plan."),
        "Waiting periods": (
            "Accidents: none. Illness: 30 days, and Covid-19 30 days where bought. Non-accident "
            "surgery and admissions, newborn, premature and congenital conditions, chronic and "
            "declared pre-existing conditions, organ transplant, HIV/AIDS and cataract: 12 "
            "months. Maternity: 10 months."),
        "Before treatment": (
            "Every admission needs GA's pre-authorisation, 48 hours ahead when planned. "
            "Outside GA's panel: refunds only, claimed within 60 days. Cover: Kenya, and 8 "
            "weeks of travel (not UK, USA or Canada)."),
        "Not covered": (
            "Undeclared pre-existing conditions, outpatient care (including outpatient "
            "maternity, dental and optical), pandemics unless bought, cosmetic treatment, "
            "self-injury, alcohol and "
            "drugs, war and riots, hazardous sports, fertility treatment, weight treatment, "
            "and GA's other exclusions."),
        "How to pay": GA_HOW_TO_PAY,
        "Please note": please_note("GA"),
    }),
    "ga_hadhi": ("GA Hadhi Health – key terms", {
        "Your premium": (
            "Annual, for one member or a couple, by the older member's age group; the "
            "standard rates exclude pandemics."),
        "Who can join": (
            "Retirees and seniors from 50. Joining age: Plan A up to 65, Plan B up to 70, "
            "Plans C and D up to 75 (75 on any plan for groups of 5 or more)."),
        "Waiting periods": (
            "Accidents: none. Illness: 30 days. Surgery: 2 months (pre-existing conditions "
            "excepted). Declared pre-existing conditions: 9 months. Organ transplant: 2 years."),
        "Before treatment": (
            "Show your Hadhi card; GA issues an undertaking after pre-authorisation. Show "
            "your NHIF card before discharge. Refund claims within 60 days. Overseas: India, "
            "South Africa or Pakistan only, pre-authorised."),
        "Not covered": (
            "Cosmetic treatment unless after an accident, alternative medicine, homes, spas, "
            "hospices and long-term care, self-injury, alcohol and drug abuse, laser eye "
            "surgery, military operations, extreme sports, unregistered practitioners, "
            "pandemics, and GA's other exclusions."),
        "How to pay": GA_HOW_TO_PAY,
        "Please note": please_note("GA", "Either side may cancel with 30 days' notice."),
    }),
    "jubilee_j_care": ("Jubilee J Care – key terms", dict(JUBILEE_COMMON, **{
        "Your premium": (
            "Annual, on the oldest applicant's age group; children have their own inpatient "
            "rate."),
        "Who can join": (
            "From birth (term baby) to 64; members move to J Senior at 65. Children over 18 "
            "up to 25 if in school. Medical exam for new applicants from 50, at their cost."),
        "Not covered": JUBILEE_NOT_COVERED % "fertility treatment, ",
    })),
    "jubilee_j_care_junior": ("Jubilee J Care Junior – key terms", dict(JUBILEE_COMMON, **{
        "Your premium": (
            "Annual, per child."),
        "Who can join": "Children under 18 as main members, from birth (38-week term baby).",
        "Not covered": JUBILEE_NOT_COVERED % "",
    })),
    "jubilee_j_senior": ("Jubilee J Senior – key terms", dict(JUBILEE_COMMON, **{
        "Your premium": (
            "Annual, on the oldest applicant's age group; children have their own rates."),
        "Who can join": (
            "From 65, joining up to 79; cover for life, renewed yearly. New applicants have "
            "a medical exam, at their own cost."),
        "Not covered": JUBILEE_NOT_COVERED % "",
    })),
    "heritage_heriafya": ("Heritage HeriAfya – key terms", {
        "Your premium": (
            "Annual; one hospital limit for the family: adults pay by age, the first child "
            "the child rate, other children less."),
        "Who can join": (
            "Principal and spouse 18 to 65 (cover continues to 70). Children from 1 day to "
            "18, extended to 23 and over with proof of full-time study. Medical tests for new "
            "applicants aged 55 and over."),
        "Waiting periods": (
            "Accidents: none. Illness: 30 days. Non-accident dental and optical surgery: 6 "
            "months. "
            "Maternity: 10 months. Gynaecological surgery, pre-existing and chronic "
            "conditions: 12 months. Cancer, dialysis, transplants: 24 months. Reconstructive "
            "surgery: 36 months."),
        "Co-payments": (
            "KES 1,000 per outpatient visit at Aga Khan (Nairobi, Kisumu, Mombasa), Nairobi "
            "Hospital, Gertrude's, MP Shah, Mater and Karen, all branches; KES 500 elsewhere."),
        "Before treatment": (
            "Use the HeriAfya panel; if you pay cash, Heritage refunds up to 80%. Report "
            "admissions at once, emergencies within 48 hours. Cover: Kenya and East Africa, "
            "with referral to India."),
        "Not covered": (
            "Cosmetic surgery, alternative medicine, long-term care, infertility, self-injury, "
            "alcohol and drugs, experimental treatment, pandemics, dentures and implants, "
            "laser eye treatment, hearing aids, dangerous sports, and Heritage's other "
            "exclusions."),
        "How to pay": (
            "Pay Heritage only: Stanbic Bank (Chiromo) account 0100012567965, or M-Pesa "
            "Paybill 8779000, account: policy number or name."),
        "Please note": please_note("Heritage", "Heritage may decline any application."),
    }),
    "apa_afya_nafuu": ("APA Afya Nafuu – key terms", {
        "Your premium": (
            "Annual, per person, by age next birthday; paid in full before cover starts."),
        "Who can join": (
            "From 1 month to 75 (last joining age 75). A medical report for everyone joining "
            "at 55 and over. Renewal depends on claims and APA's underwriting."),
        "Waiting periods": (
            "Accidents: none. Illness: 30 days. Surgery: 90 days. Pre-existing, chronic and "
            "congenital conditions, HIV/AIDS, fibroids, hernia, adenoids and haemorrhoids: 12 "
            "months. Maternity: 10 months. Cataract: 1 year (APA's exclusions say 24 months). "
            "Transplants: 3rd year. A break in cover restarts them."),
        "Before treatment": (
            "APA's panel only: no refund for treatment elsewhere. Show your card. Planned "
            "admissions: tell APA 48 hours ahead and wait for approval; emergencies within 48 "
            "hours. Bills are paid net of SHIF. Cover: Kenya, and up to 6 weeks abroad on a "
            "refund."),
        "Not covered": (
            "Check-ups, cosmetic surgery unless after an accident, family planning and "
            "infertility, alternative medicine, self-injury, drink and drugs, war, glasses and "
            "dental unless in the cover, treatment outside Kenya or the panel, and APA's other "
            "exclusions."),
        "How to pay": APA_COMMON["How to pay"],
        "Please note": please_note(
            "APA", "Either side may cancel on 14 days' notice; no refund after 6 months or once "
            "a claim is made."),
    }),
}


def html(key):
    """The key terms of ``key`` as the quotation prints them."""
    heading, sections = KEY_TERMS[key]
    parts = ["<h3>%s</h3>" % escape(heading)]
    for section in SECTIONS:
        if section in sections:
            parts.append("<p><strong>%s.</strong> %s</p>" % (escape(section),
                                                           escape(sections[section])))
    return "".join(parts)


def plain(key):
    """As the reader sees it, for measuring."""
    heading, sections = KEY_TERMS[key]
    return " ".join([heading] + ["%s. %s" % (s, sections[s]) for s in SECTIONS if s in sections])


def measured(key):
    """Its length as the quotation measures it against SHORT_TERMS: Odoo's
    html2plaintext marks headings and bold text with asterisks."""
    heading, sections = KEY_TERMS[key]
    return len("*%s*" % heading) + sum(
        1 + len("*%s.* %s" % (s, sections[s])) for s in SECTIONS if s in sections)

#: HeriAfya per person: the same product, each person with the limit to themselves.
KEY_TERMS["heritage_heriafya_pp"] = (
    "Heritage HeriAfya – key terms",
    dict(KEY_TERMS["heritage_heriafya"][1], **{
        "Your premium": (
            "Annual; each person has the hospital limit to themselves: adults pay by age, "
            "the first child the child rate, other children less."),
    }))
