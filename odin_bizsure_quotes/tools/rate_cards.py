"""Rate cards added in 2026, transcribed as the insurers print them.

Every premium list is one row of the card with one value per column, in the
card's column order, so each line here can be read against the card it came
from. ``tools/generate_cover_products.py`` turns this into Odoo data; nothing
here is derived, rounded or interpolated.

* GA Insurance -- *Community/Retail Health Insurance -- Inpatient Only*,
  effective 1 January 2022 (brochure updated September 2022).
* Jubilee Health Insurance -- *J Care* and *J Care Junior*, effective
  1 December 2023.
* Jubilee Health Insurance -- *J Senior*, effective 1 December 2023.
"""

# =============================================================================
# GA Insurance -- Community/Retail Health Insurance, Inpatient Only
# =============================================================================

#: "IP LIMIT" columns of both premium tables (page 2)
GA_LIMITS = [250000, 500000, 1000000, 2000000, 2500000, 3000000, 4000000,
             5000000, 6000000, 7000000, 8000000, 9000000, 10000000]

#: "M", "M + 1" ... "M + 6": people covered, Member only is 1
GA_SIZES = [1, 2, 3, 4, 5, 6, 7]

#: Adults 19 to 65 years, last joining age 60; children from birth up to 18
#: (Particulars and eligibility, page 2). Rated by family size, so the ages
#: are the eligibility of the eldest member of a new policy.
GA_AGE_MIN = 19
GA_AGE_MAX = 60

#: RATES - EXCLUDING PANDEMIC, one row per family size (M to M + 6)
GA_EXCLUDING_PANDEMIC = [
    [14715, 20574, 27698, 29499, 30230, 32127, 35340, 38873, 42761, 44899, 47144, 49501, 51976],
    [19865, 27775, 37392, 39824, 40811, 43371, 47708, 52479, 57727, 60613, 63644, 66826, 70168],
    [25825, 36107, 48610, 51771, 53054, 56383, 62021, 68223, 75045, 78797, 82737, 86874, 91218],
    [32281, 45134, 60762, 64713, 66317, 70478, 77526, 85279, 93807, 98497, 103422, 108593, 114022],
    [38737, 54161, 72915, 77656, 79580, 84574, 93031, 102334, 112568, 118196, 124106, 130311, 136827],
    [46485, 64993, 87498, 93187, 95497, 101489, 111638, 122801, 135081, 141835, 148927, 156374, 164192],
    [55782, 77992, 104998, 111825, 114596, 121786, 133965, 147362, 162098, 170203, 178713, 187648, 197031],
]

#: RATES - INCLUDING PANDEMIC, one row per family size (M to M + 6)
GA_INCLUDING_PANDEMIC = [
    [17658, 24689, 33238, 35399, 36276, 38552, 42407, 46648, 51313, 53879, 56573, 59401, 62371],
    [23838, 33330, 44871, 47788, 48973, 52045, 57250, 62975, 69273, 72736, 76373, 80192, 84201],
    [30990, 43329, 58332, 62125, 63664, 67659, 74425, 81868, 90054, 94557, 99285, 104249, 109462],
    [38737, 54161, 72915, 77656, 79580, 84574, 93031, 102334, 112568, 118196, 124106, 130311, 136827],
    [46485, 64993, 87498, 93187, 95497, 101489, 111638, 122801, 135081, 141835, 148927, 156374, 164192],
    [55782, 77992, 104998, 111825, 114596, 121786, 133965, 147362, 162098, 170203, 178713, 187648, 197031],
    [66938, 93590, 125997, 134190, 137515, 146144, 160758, 176834, 194517, 204243, 214455, 225178, 236437],
]

#: MATERNITY AT ADDITIONAL PREMIUM: limit, "Maternity Premium - Per Family
#: Female Principal/Spouse"
GA_MATERNITY = [
    (50000, 13283),
    (100000, 17078),
    (150000, 18975),
    (200000, 20873),
    (250000, 22770),
    (300000, 34085),
]

#: BENEFIT/SUMS INSURED - SUB-LIMITS WITHIN OVERALL LIMITS (page 3), one
#: value per IP limit column. Ticked rows are in GA_BENEFITS_COVERED.
GA_SUBLIMITS = [
    ("Bed charges (subject to NHIF rebate), per day",
     [10000, 10000, 12500, 15000, 16500, 17500, 20000, 25000, 26500, 27500, 27500, 27500, 30000]),
    ("Newly diagnosed chronic conditions, declared pre-existing and/or chronic conditions, "
     "pain management, organ transplant, HIV/AIDS and related conditions, including "
     "Covid-19 if purchased",
     [150000, 250000, 350000, 400000, 425000, 450000, 500000, 700000, 750000, 800000,
      850000, 900000, 1000000]),
    ("Neo-natal, pre-maturity and congenital conditions",
     [200000, 225000, 250000, 275000, 285000, 300000, 325000, 350000, 375000, 375000,
      375000, 375000, 400000]),
    ("Psychiatric and psychotherapy illness",
     [200000, 225000, 250000, 275000, 285000, 300000, 325000, 350000, 350000, 375000,
      375000, 375000, 400000]),
    ("Inpatient ophthalmology resulting from an illness, excluding laser eye surgery",
     [50000, 75000, 100000, 125000, 135000, 150000, 175000, 200000, 250000, 250000,
      250000, 250000, 300000]),
    ("Cataract operation",
     [50000, 75000, 100000, 100000, 115000, 125000, 125000, 150000, 150000, 150000,
      150000, 150000, 150000]),
    ("Inpatient dental hospitalisation resulting from an illness",
     [50000, 75000, 100000, 125000, 135000, 150000, 175000, 200000, 250000, 250000,
      250000, 250000, 300000]),
    ("Maternity-related complications and 1st ever emergency caesarean section only",
     [50000, 75000, 100000, 125000, 135000, 150000, 175000, 200000, 200000, 250000,
      250000, 250000, 300000]),
    ("Last expense cover, any one death in the family",
     [50000, 75000, 100000, 100000, 115000, 125000, 125000, 150000, 150000, 150000,
      150000, 150000, 150000]),
    ("Ayurvedic treatment",
     [50000, 75000, 100000, 100000, 115000, 125000, 125000, 150000, 150000, 150000,
      150000, 150000, 150000]),
    ("External appliances on prescription (wheelchairs, walking frames, crutches and all "
     "assistive devices for PWDs)",
     [30000, 50000, 70000, 90000, 100000, 110000, 130000, 150000, 170000, 190000,
      210000, 230000, 250000]),
    ("Air ticket for treatment available locally but cheaper outside Kenya",
     [30000, 30000, 30000, 40000, 40000, 40000, 40000, 50000, 50000, 50000, 50000,
      50000, 50000]),
    ("Post-hospitalisation following an illness or accident: visits and follow-ups within "
     "30 days after discharge (reimbursement only)",
     [15000, 20000, 25000, 30000, 32500, 35000, 40000, 45000, 50000, 50000, 55000,
      55000, 60000]),
    ("Traumacare (principal only): accident-related outpatient expenses",
     [100000] * 13),
    ("Traumacare (principal only): last expense",
     [50000] * 13),
]

GA_BENEFITS_COVERED = [
    "Specialists, physicians, surgeons and anaesthetist fees, subject to GA's panel rates",
    "Emergency evacuation by air and/or road ambulance leading to an admission",
    "Nursing care, ICU/HDU and theatre charges",
    "Day case surgery under general anaesthesia",
    "Diagnostic and surgical procedures while admitted, including pathology, X-ray, "
    "ultrasound, ECG, MRI and CT scan",
    "Overseas referral for treatment not locally available, including economy air ticket",
    "Lodging fee for a parent or guardian accompanying a child below 12 years of age",
    "Optical and dental treatment following an accident",
    "Home nursing on a doctor's recommendation, up to 30 days after discharge",
]


# =============================================================================
# Jubilee Health Insurance -- J Care (Premium Individual/Family Health)
# =============================================================================

JC_LEVELS = ["Classic", "Premier", "Advanced", "Executive", "Royal", "Prestige"]

#: J CARE MEDICAL INSURANCE RATES - INPATIENT: "Limit per family (KES)"
JC_LIMITS = [500000, 1000000, 2000000, 3000000, 5000000, 10000000]

#: INPATIENT, per person: (who, age from, age to, premium per level). The
#: principal's and spouse's band is the oldest applicant's (General
#: Conditions 6); the card repeats the same child row in every band.
JC_INPATIENT = [
    ("principal", 18, 30, [21316, 25621, 30215, 36928, 48028, 55033]),
    ("spouse", 18, 30, [15673, 21513, 25380, 32004, 42539, 48744]),
    ("principal", 31, 40, [22569, 26709, 32027, 40375, 50773, 58178]),
    ("spouse", 31, 40, [18808, 22480, 27192, 35082, 43911, 50316]),
    ("principal", 41, 50, [23215, 32027, 39883, 46776, 62313, 71401]),
    ("spouse", 41, 50, [19453, 25984, 32632, 41852, 52275, 59900]),
    ("principal", 51, 59, [30989, 39266, 48186, 61263, 78793, 90287]),
    ("spouse", 51, 59, [26030, 32957, 40463, 51404, 66196, 75852]),
    ("principal", 60, 64, [38737, 49083, 60232, 67391, 86672, 99315]),
    ("spouse", 60, 64, [32538, 41197, 50579, 56544, 72815, 83437]),
    # "Child Birth -17 years", identical in all five bands
    ("child", 0, 17, [10282, 13899, 16920, 21295, 27444, 31448]),
]

#: OUTPATIENT - (OPTIONAL): "Limit per person per annum"
JC_OUTPATIENT_LIMITS = [50000, 50000, 80000, 100000, 150000, 200000]

#: OUTPATIENT, "Premium per person" by age band, everyone at the oldest
#: applicant's band (there is no child row)
JC_OUTPATIENT = [
    (0, 40, [26555, 26555, 31982, 40354, 50175, 55168]),   # "40 years and below"
    (41, 50, [30315, 30315, 39711, 49158, 55750, 60637]),
    (51, 59, [35537, 35537, 46641, 54294, 61006, 65792]),
    (60, 64, [40537, 40537, 55302, 63832, 77731, 82197]),
]

#: SHARED OUTPATIENT PER FAMILY - (OPTIONAL), Royal and Prestige only:
#: level -> (limit per family, rows of (age from, age to, premiums for M,
#: M+1, ...)). "--" on the card is a family size with no rate: left out.
JC_SHARED_OUTPATIENT = {
    "Royal": (150000, [
        (0, 40, [50175, 94615, 123286]),            # "Below 40 years"
        (41, 50, [55750, 105129, 136986]),
        (51, 59, [61006, 115040, 149901]),
        (60, 64, [77731, 146578]),
    ]),
    "Prestige": (200000, [
        (0, 40, [55168, 104031, 135555, 167079, 198603]),
        (41, 50, [60637, 114343, 148993, 183642]),
        (51, 59, [65792, 124065, 161661, 199256]),
        (60, 64, [82197, 155000]),
    ]),
}

#: MATERNITY - (OPTIONAL): "Limit per family", "Premium per principal/
#: spouse per annum"
JC_MATERNITY_LIMITS = [80000, 100000, 120000, 120000, 150000, 200000]
JC_MATERNITY = [30129, 32356, 34818, 34818, 41618, 50633]

#: DENTAL - (OPTIONAL) and OPTICAL - (OPTIONAL): "Limit per Person",
#: "Premium per person per annum" (the two tables print the same figures)
JC_DENTAL_LIMITS = [5000, 10000, 20000, 30000, 40000, 50000]
JC_DENTAL = [2696, 3517, 7151, 10668, 17819, 21483]
JC_OPTICAL_LIMITS = [5000, 10000, 20000, 30000, 40000, 50000]
JC_OPTICAL = [2696, 3517, 7151, 10668, 17819, 21483]

#: LAST EXPENSE - (OPTIONAL): "Limit per Person", "Premium per person per
#: annum"; free for the principal member, optional for dependants
JC_LAST_EXPENSE_LIMITS = [50000, 50000, 75000, 100000, 100000, 100000]
JC_LAST_EXPENSE = [1007, 1007, 1567, 2014, 2014, 2014]

#: PERSONAL ACCIDENT - (OPTIONAL): "Limit per Person", "Premium per Adult
#: (18 Years & Above)"; free for the principal, optional for dependants over 18
JC_ACCIDENT_LIMITS = [500000] * 6
JC_ACCIDENT = [586] * 6

#: ENHANCED COVID-19 COVER - OPTIONAL: (option, overall annual limit,
#: premium per person)
JC_COVID = [
    ("Option 1", 500000, 6141),
    ("Option 2", 1000000, 9490),
    ("Option 3", 1500000, 10886),
    ("Option 4", 2000000, 12840),
]

#: J CARE JUNIOR - FOR CHILDREN 0-17 YEARS AS MAIN MEMBER
JCJ_LIMITS = [500000, 1000000, 2000000, 3000000, 5000000, 10000000]   # per person
JCJ_INPATIENT = [21316, 25621, 30215, 36928, 48028, 55033]
JCJ_OUTPATIENT_LIMITS = [50000, 50000, 80000, 100000, 150000, 200000]
JCJ_OUTPATIENT = [26555, 26555, 31982, 40354, 50175, 55168]
JCJ_DENTAL_LIMITS = [5000, 10000, 20000, 30000, 40000, 50000]
JCJ_DENTAL = [2696, 3517, 7151, 10668, 17819, 21483]
JCJ_OPTICAL_LIMITS = [5000, 10000, 20000, 30000, 40000, 50000]
JCJ_OPTICAL = [2696, 3517, 7151, 10668, 17819, 21483]

#: INPATIENT BENEFITS - (CORE PLAN), per level
JC_INPATIENT_BENEFITS = [
    ("Pre-existing and/or chronic conditions, gynaecological conditions, hernias, "
     "haemorrhoids, thyroidectomy, adenoidectomy, congenital conditions, organ transplant, "
     "HIV/AIDS and related conditions existing and/or diagnosed at the time of joining "
     "(1-year waiting period); cancer treatment within this limit (2-year waiting period)",
     [250000, 300000, 400000, 500000, 1000000, 2000000]),
    ("Confirmed newly diagnosed chronic conditions after 3 months of cover",
     [200000, 400000, 800000, 1200000, 2000000, 3000000]),
    ("Psychiatric conditions (1-year waiting period)",
     [100000, 200000, 250000, 300000, 500000, 750000]),
    ("Last expense (free for the principal member, optional for dependants)",
     [50000, 50000, 75000, 100000, 100000, 100000]),
    ("Prematurity, for a child born to a member who has completed the 1-year waiting "
     "period on the maternity benefit",
     [50000, 100000, 150000, 200000, 250000, 500000]),
    ("Bed limit per day (NHIF rebate applied on the limit)",
     ["General ward bed", "General ward bed", "General ward bed",
      "Standard private room up to KES 12,500", "Standard private room up to KES 24,000",
      "Ensuite room up to KES 32,000"]),
    ("Post-hospitalisation treatment/review up to 3 weeks after discharge",
     ["up to KES 10,000", "up to KES 15,000", "up to KES 20,000", "up to KES 25,000",
      "up to KES 30,000", "up to KES 30,000"]),
    ("1st emergency caesarean (12-month waiting period from the date of purchase; "
     "principal member or spouse only; maternity benefit must be bought)",
     [100000, 120000, 150000, 150000, 150000, 200000]),
    ("COVID-19", [500000] * 6),
    ("Lodger fees for a parent accompanying a sick child member", ["covered for a child "
     "under 10 years"] * 6),
    ("Commercial air evacuation out of Kenya (pre-authorised) for inpatient treatment not "
     "available or not safe to undertake locally",
     ["not applicable", "not applicable",
      "economy return fare only, within Africa, India and Pakistan",
      "economy return fare only, within Africa, India and Pakistan",
      "economy return fare only, within Africa, India and Pakistan",
      "economy return fare only, within Africa, India and Pakistan"]),
    ("Personal accident (free for the principal member, optional for dependants over 18)",
     [500000] * 6),
    ("Physicians, specialists, surgical fees including anaesthetist fees, theatre charges, "
     "HDU, CCU and ICU, diagnostic tests and physiotherapy as part of inpatient treatment",
     ["covered"] * 6),
    ("Inpatient MRI/CT and PET scans (subject to pre-authorisation)", ["covered"] * 6),
    ("Surgical appliances/internal prostheses", ["covered"] * 6),
    ("External aids on prescription, including wheelchairs, corsets/walking frames, "
     "crutches, hearing aids and hearing tests", [100000] * 6),
    ("Reconstructive surgery following an accident", ["covered"] * 6),
    ("Inpatient non-accident-related dental surgery/treatment (1-year waiting period)",
     [100000] * 6),
    ("Inpatient dental surgery from an accident", ["covered"] * 6),
    ("Inpatient ophthalmology surgery as a result of an accident", ["covered"] * 6),
    ("Inpatient non-accident-related eye treatment, excluding correction of refractive "
     "errors and laser treatment (1-year waiting period)", [100000] * 6),
    ("Day case admission", ["covered under the relevant sub-limit"] * 6),
    ("Medically necessary home nursing (subject to pre-authorisation)", ["45 days"] * 6),
    ("Take-home prescribed medication after discharge from hospital",
     ["up to 30 days after discharge"] * 6),
    ("Occupational and/or speech therapy caused by an acute episode of an eligible "
     "condition or by an accident, up to 6 months from the onset of the episode within the "
     "cover period, subject to pre-authorisation", ["covered"] * 6),
    ("Pain management, within inpatient, subject to the pre-existing and chronic sub-limit",
     ["covered"] * 6),
    ("Medically necessary local road ambulance leading to admission in hospital",
     ["covered"] * 6),
]

#: OUTPATIENT BENEFITS - (OPTIONAL), the same at every level
JC_OUTPATIENT_BENEFITS = [
    ("COVID-19 testing for symptomatic cases at designated facilities", 15000),
    ("Consultation fees for GPs (doctors on panel)", "covered"),
    ("Consultation fees for specialists, on referral only (doctors on panel)", "covered"),
    ("Pathology, X-rays, MRI, CT scan and other necessary diagnostic tests "
     "(pre-authorisation required)", "covered"),
    ("Prescription drugs and dressings, up to a maximum of 30 days' dosage", "covered"),
    ("Pre-existing and/or chronic conditions, gynaecological conditions, hernias, "
     "haemorrhoids, thyroidectomy, adenoidectomy, psychiatric, congenital, organ transplant, "
     "HIV/AIDS and related conditions existing and/or diagnosed at the time of joining, "
     "subject to full disclosure at joining (1-year waiting period)", "covered"),
    ("Cancer treatment (2-year waiting period)", "covered"),
    ("Confirmed newly diagnosed chronic conditions after 3 months of cover", "covered"),
    ("KEPI/baby-friendly vaccinations", "covered"),
    ("Physiotherapy (pre-authorisation required)", "covered"),
    ("Routine pre-natal and post-natal outpatient treatment (1-year waiting period), only "
     "if maternity benefits have been purchased", "covered"),
    ("Annual medical check-up for the main member and/or spouse, within outpatient", 10000),
    ("Occupational and/or speech therapy caused by an acute episode of an eligible condition "
     "or by an accident, up to 6 months from the onset of the episode within the cover "
     "period, subject to pre-authorisation", "covered"),
    ("Pain management, within outpatient, subject to the pre-existing and chronic sub-limit",
     "covered"),
    ("Family planning limited to long-acting methods (IUCD and Norplant implants only), "
     "within outpatient", 2500),
]

JC_MATERNITY_BENEFITS = (
    "Normal and C-section delivery; labour/recovery ward; professional fees; "
    "pregnancy/maternity-related hospitalisation; other related ailments and complications, "
    "including ectopic pregnancy and miscarriage (1-year waiting period). Excludes expenses "
    "related to the newborn.")

JC_DENTAL_BENEFITS = (
    "Dental consultations, extractions, fillings, dental X-rays and prescriptions; removal "
    "of impacted or buried teeth and root canal treatment (pre-authorisation required).")

JC_OPTICAL_BENEFITS = (
    "1 eye test per person per year; frames and prescription lenses (subject to "
    "pre-authorisation).")

#: "Frames limit (1 every 2 years)", per level
JC_OPTICAL_FRAMES = ["up to the full optical limit", "up to the full optical limit",
                     "KES 10,000 per frame", "KES 10,000 per frame", "KES 10,000 per frame",
                     "KES 10,000 per frame"]

JC_COVID_BENEFITS = (
    "Covid-19 admissions and mild symptomatic cases: covered. Confirmed asymptomatic "
    "outpatient cases: KES 50,000 within the overall limit.")

#: OUTPATIENT CO-PAYMENTS (both Jubilee cards)
JUBILEE_COPAY_2000 = [
    "Nairobi Hospital (main facility only)", "MP Shah Hospital (Parklands)",
    "Karen Hospital (Karen)", "Aga Khan University Hospital (Parklands only)",
    "Gertrude's Children's Hospital (Muthaiga)", "AAR Hospital (Kiambu Road)",
]
JUBILEE_COPAY_1000 = [
    "Mater Hospital (main hospital)", "Avenue Hospital (Parklands)",
    "Mediheal Hospital (Parklands and Eldoret)", "Eldoret Hospital",
    "St Luke's Orthopaedic Hospital (Eldoret)", "Mombasa Hospital",
    "Premier Hospital (Nyali)",
]

#: General Conditions 1 to 5 and 9 to 18, word for word in substance; 6
#: (oldest applicant), 7 and 8 (eligibility) are in the terms' own sections.
JUBILEE_CONDITIONS = [
    "General waiting period of 30 days for new entrants on illness claims. No waiting "
    "period for accident-related treatment.",
    "MRI, CT and PET scans on pre-authorisation.",
    "Cancer treatment is subject to a 2-year waiting period.",
    "Pre-existing, chronic, psychiatric, congenital, organ transplant, HIV/AIDS and related "
    "treatment, maternity and related complications, inpatient non-accident-related "
    "ophthalmology, dental surgery, fibroids and all gynaecological illness and treatment, "
    "adenoidectomy, haemorrhoidectomy, hernias, tonsillectomy and thyroidectomy procedures "
    "are subject to a 1-year waiting period.",
    "Pregnancy, childbirth, maternity benefits, maternity-related complications, antenatal "
    "or post-natal care, prematurity and caesarean operation, where purchased, are subject "
    "to a 1-year waiting period.",
    "New applicants aged 50 years and above must undergo a medical examination at specific "
    "providers, at their own cost, before membership and eligibility of cover can be "
    "confirmed.",
    "All applications, including continuous transfer applications, are subject to medical "
    "underwriting and acceptance.",
    "Cover must be confirmed in writing and premiums paid in advance and in full to "
    "Jubilee Health Insurance for the benefits to be effective.",
    "Co-payments apply at select hospital facilities, as listed.",
    "All scheduled admissions must be pre-authorised at least 48 hours before admission. "
    "For emergency admissions, the hospital contacts Jubilee Health Insurance within 48 "
    "hours of admission.",
    "All inpatient hospital bills are paid net of all National Hospital Insurance Fund "
    "(NHIF) rebates.",
    "Medical cards must be used at the accredited panel of providers, with identification. "
    "Each member completes and signs a claim form and confirms the services received by "
    "signing the provider's invoice.",
    "A member travelling outside the country is eligible for accident and emergency medical "
    "benefits for up to six (6) weeks in any one trip, on reimbursement, subject to "
    "reasonable and customary rates and the policy terms and conditions. Accommodation and "
    "travel costs are not covered.",
    "Reimbursement claims are only acceptable once the outpatient credit limit has been "
    "reached. Eligible claims are paid up to 100% within the panel and 100% outside the "
    "panel, subject to reasonable and customary rates.",
]

#: Exclusions 1 to 21 of both Jubilee cards; J Care adds 22.
JUBILEE_EXCLUSIONS = [
    "cosmetic or plastic surgery, unless necessitated by an accidental injury while covered",
    "riding or driving in any kind of race",
    "beauty treatment or massage",
    "naval, military and air force operations",
    "stays at sanatoria, old age homes, places of rest and the like",
    "vaccinations, except KEPI and baby-friendly vaccinations",
    "transportation other than a licensed ambulance as provided for under inpatient cover",
    "nutritional food supplements or replacements",
    "injury or illness resulting from insurrection or war, civil commotion or an act of "
    "terrorism, declared or undeclared",
    "injury from participating in a riot or strike",
    "alternative treatment such as herbal treatment, acupuncture and chiropractors",
    "expenses from participating in extreme or hazardous sports and activities",
    "dental treatment (extractions, fillings, scaling and the like) unless dental cover is "
    "bought",
    "optical treatment correcting eyesight (eye glasses, contact lenses, laser eye "
    "treatment) unless optical cover is bought",
    "intentional self-injury, suicide or attempted suicide, treatment of acute or chronic "
    "alcoholism and drug addiction",
    "expenses recoverable under any other insurance, e.g. NHIF or GPA",
    "any injury, illness or disease specified as an exclusion, and complications of an "
    "excluded condition",
    "services primarily for weight reduction or treatment of obesity, slimming "
    "preparations, or care involving weight reduction as the main method of treatment",
    "peri-menopause, menopause, andropause, hormone replacement therapy, and age- and "
    "puberty-related treatment",
    "epidemics, pandemics or unknown diseases, except COVID-19 up to the limits shown",
    "treatment required as a result of non-compliance, failure or refusal to comply with "
    "medical advice",
]
JC_EXTRA_EXCLUSIONS = [
    "fertility treatment, e.g. treatment related to infertility and impotence",
]


# =============================================================================
# Jubilee Health Insurance -- J Senior (Health Cover for Senior Citizens)
# =============================================================================

JS_LEVELS = ["Copper", "Bronze", "Silver", "Gold", "Diamond"]

#: INPATIENT - (CORE PLAN): "Limit per family per annum (KES)"
JS_LIMITS = [500000, 1000000, 3000000, 5000000, 10000000]

#: INPATIENT, per person: (who, age from, age to, premium per level). The
#: principal's and spouse's band is the oldest applicant's.
JS_INPATIENT = [
    ("principal", 65, 69, [57952, 81063, 121067, 139806, 182754]),
    ("spouse", 65, 69, [49781, 68199, 101653, 117426, 156987]),
    ("principal", 70, 74, [68819, 96264, 143766, 166019, 217020]),
    ("spouse", 70, 74, [59115, 82691, 123254, 142379, 186421]),
    ("principal", 75, 79, [72441, 101330, 151333, 174757, 228443]),
    ("spouse", 75, 79, [62227, 85248, 127066, 146783, 196234]),
    # "Child (1month - 17yrs)", identical in all three bands
    ("child", 0, 17, [9638, 13482, 20281, 23447, 30393]),
]

#: OUTPATIENT - (OPTIONAL): "Limit per person per annum (KES)"
JS_OUTPATIENT_LIMITS = [100000, 100000, 150000, 200000, 200000]

#: OUTPATIENT: (who, age from, age to, premium per level). "Premium per
#: person" by the oldest applicant's band; children on their own row.
JS_OUTPATIENT = [
    ("any", 65, 69, [60080, 60080, 69771, 80971, 80971]),
    ("any", 70, 74, [66089, 66089, 76749, 89068, 89068]),
    ("any", 75, 79, [76662, 76662, 88833, 102578, 102578]),
    ("child", 0, 17, [28136, 28136, 39805, 43309, 43309]),
]

#: DENTAL - (OPTIONAL) and OPTICAL - (OPTIONAL)
JS_DENTAL_LIMITS = [30000, 30000, 40000, 50000, 50000]
JS_DENTAL = [14068, 14068, 23212, 24619, 24619]
JS_OPTICAL_LIMITS = [30000, 30000, 40000, 50000, 50000]
JS_OPTICAL = [14068, 14068, 23212, 24619, 24619]

#: LAST EXPENSE - (OPTIONAL): "Limit per person per annum (KES)", premium
#: per person by the oldest applicant's band; free for the principal
JS_LAST_EXPENSE_LIMITS = [50000, 50000, 75000, 100000, 100000]
JS_LAST_EXPENSE = [
    (65, 69, [1466, 1466, 2931, 4397, 4397]),
    (70, 74, [2638, 2638, 5275, 7914, 7914]),
    (75, 79, [4924, 4924, 9789, 14654, 14654]),
]

#: PERSONAL ACCIDENT - (OPTIONAL): "Premium per adult (18 years & above)"
JS_ACCIDENT_LIMITS = [500000] * 5
JS_ACCIDENT = [586] * 5

#: ENHANCED COVID-19 COVER - OPTIONAL
JS_COVID = [
    ("Option 1", 500000, 6141),
    ("Option 2", 1000000, 9490),
    ("Option 3", 1500000, 10886),
    ("Option 4", 2000000, 12840),
]

#: INPATIENT BENEFITS - (CORE PLAN), per level
JS_INPATIENT_BENEFITS = [
    ("Pre-existing and/or chronic and congenital conditions, gynaecological conditions, "
     "hernias, haemorrhoids, adenoidectomy, HIV/AIDS and related conditions, thyroidectomy "
     "existing at the time of joining, subject to full disclosure (1-year waiting period)",
     [200000, 300000, 600000, 1000000, 1500000]),
    ("Cancer treatment (2-year waiting period)", [200000, 300000, 600000, 1000000, 1500000]),
    ("Organ transplant for the insured (1-year waiting period)",
     [200000, 300000, 600000, 1000000, 1500000]),
    ("Knee and hip replacement (1-year waiting period)",
     [200000, 300000, 600000, 1000000, 1500000]),
    ("Confirmed newly diagnosed chronic conditions after 3 months of cover",
     [200000, 400000, 1200000, 2000000, 3000000]),
    ("Covid-19 hospitalisation benefit", [500000] * 5),
    ("Psychiatric conditions (1-year waiting period)", ["covered"] * 5),
    ("Bed limit per day (NHIF applicable on the limit)",
     ["general ward", "general ward", "standard private room up to KES 12,500",
      "standard private room up to KES 24,000", "standard private room up to KES 32,000"]),
    ("Physiotherapy as part of inpatient treatment", ["covered"] * 5),
    ("Physicians, specialists, surgical fees including anaesthetist fees, theatre charges, "
     "HDU, CCU and ICU, diagnostic tests", ["covered"] * 5),
    ("Inpatient MRI/CT and PET scans (pre-authorisation required)", ["covered"] * 5),
    ("Surgical appliances/internal prostheses", ["covered"] * 5),
    ("Reconstructive surgery following an accident", ["covered"] * 5),
    ("Inpatient non-accident-related dental surgery/treatment (1-year waiting period)",
     [100000, 100000, 100000, 100000, 150000]),
    ("Inpatient dental surgery from an accident, and inpatient ophthalmological surgery as "
     "a result of an accident", ["covered under the applicable benefit"] * 5),
    ("Inpatient non-accident-related eye treatment, excluding correction of refractive "
     "errors and laser treatment (1-year waiting period)",
     [100000, 100000, 100000, 100000, 150000]),
    # The card prints this across Bronze, Silver and Gold only; the Copper
    # and Diamond cells are empty.
    ("Day case admission",
     [None, "covered under the applicable sub-limit", "covered under the applicable sub-limit",
      "covered under the applicable sub-limit", None]),
    ("Medically necessary home nursing on a physician's recommendation, after discharge "
     "from hospital (subject to pre-authorisation)", ["30 days"] * 5),
    ("Post-hospitalisation treatment within 60 days of discharge from hospital",
     ["up to KES 20,000", "up to KES 20,000", "up to KES 30,000", "up to KES 50,000",
      "up to KES 50,000"]),
    ("Prescription drugs and dressings on discharge, up to 30 days' supply",
     ["covered"] * 5),
    ("Hospice care for the terminally ill", [100000, 100000, 200000, 300000, 300000]),
    ("Medically necessary local road ambulance leading to hospitalisation", ["covered"] * 5),
    ("Commercial air evacuation out of Kenya (pre-authorised) for inpatient treatment not "
     "available or not safe to undertake locally",
     ["not covered", "not covered", "economy return fare to India",
      "economy return fare to India", "economy return fare to India"]),
    ("Personal accident (free cover for the principal member, optional for dependants over "
     "18)", [500000] * 5),
    ("Last expense per person (free cover for the principal member, optional for "
     "dependants)", [50000, 50000, 75000, 100000, 100000]),
    ("Occupational and/or speech therapy caused by an acute episode of an eligible "
     "condition or by an accident, up to 6 months from the onset of the episode within the "
     "cover period, subject to pre-authorisation", ["covered"] * 5),
    ("Pain management, within inpatient, subject to the pre-existing and chronic sub-limit",
     ["covered"] * 5),
]

#: OUTPATIENT BENEFITS - (OPTIONAL), per level
JS_OUTPATIENT_BENEFITS = [
    ("COVID-19 testing for symptomatic cases at designated facilities", [15000] * 5),
    ("Consultation fees (doctors on panel)", ["covered"] * 5),
    ("Pathology, X-rays, MRI, CT scan and other necessary diagnostic tests",
     ["covered"] * 5),
    ("Prescription drugs and dressings, up to a maximum of 30 days' dosage",
     ["covered"] * 5),
    ("Outpatient medical appliances/equipment (crutches, non-motor wheelchair, oxygen "
     "purchase or hire) after hospitalisation/surgery and on a doctor's recommendation",
     [25000, 25000, 35000, 50000, 50000]),
    ("Hearing aids, 1 every 3 years (1-year waiting period)",
     [50000, 50000, 75000, 100000, 100000]),
    ("Pre-existing and/or chronic and congenital conditions, gynaecological conditions, "
     "hernias, haemorrhoids, adenoidectomy, HIV/AIDS and related conditions, thyroidectomy "
     "existing at the time of joining, subject to full disclosure (1-year waiting period)",
     ["covered"] * 5),
    ("Pain management", ["covered"] * 5),
    ("Cancer treatment (2-year waiting period)", ["covered"] * 5),
    ("Psychiatric treatment (1-year waiting period)", ["covered"] * 5),
    ("Physiotherapy/occupational therapy (pre-authorisation required)", ["covered"] * 5),
    ("Confirmed newly diagnosed chronic conditions after 3 months of cover",
     ["covered"] * 5),
    ("One annual routine check", ["up to KES 10,000 a year"] * 5),
    ("Preventative vaccinations (pneumonia and flu vaccines)", ["up to KES 5,000 a year"] * 5),
    ("Occupational and/or speech therapy caused by an acute episode of an eligible "
     "condition or by an accident, up to 6 months from the onset of the episode within the "
     "cover period, subject to pre-authorisation", ["covered"] * 5),
    ("Prescription medicine delivery programme", ["included"] * 5),
]

JS_DENTAL_BENEFITS = (
    "Dental consultations, extractions, fillings, dental X-rays, dentures and prescriptions "
    "(subject to pre-authorisation).")

#: "Spectacle frames and prescription lenses", per level
JS_OPTICAL_FRAMES = [10000, 10000, 12500, 15000, 15000]
JS_OPTICAL_BENEFITS = (
    "1 eye test a year by an ophthalmologist; the first prescription must be from an "
    "ophthalmologist. Frames and prescription lenses every 2 years (subject to "
    "pre-authorisation).")
