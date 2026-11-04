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


# =============================================================================
# GA Insurance -- Hadhi Health Plan (senior citizens), updated October 2022
# =============================================================================

#: Cover Plans: (plan, "In Patient Limit per Person/Couple", maximum joining
#: age from the "AGE" row: 65, 70, 75 and 75 years)
HADHI_PLANS = [
    ("A", 5000000, 65),
    ("B", 2500000, 70),
    ("C", 1000000, 75),
    ("D", 500000, 75),
]

#: "Age Group" rows of both premium tables
HADHI_BANDS = [(50, 60), (61, 65), (66, 70), (71, 75), (76, 80), (81, 100)]

#: HADHI STANDARD PREMIUMS: per age group, (M, M+1) for plans A, B, C, D;
#: None where the card prints "-"
HADHI_STANDARD = [
    [(83545, 167090), (62097, 124195), (51879, 103757), (42457, 84913)],
    [(92828, 185656), (68997, 137994), (57643, 115286), (47174, 94348)],
    [(125063, 250126), (92957, 185914), (77660, 155320), (63556, 127112)],
    [(169486, 338972), (125975, 251950), (105245, 210490), (77280, 154560)],
    [(211984, 423968), (157563, 315126), (131635, 263270), (86131, 172262)],
    [None, None, None, (96658, 193316)],
]

#: HADHI ENHANCED PREMIUMS WITH COVID-19 COVERAGE, same layout. The card
#: prints "*Minimum of 10 members and above." under this table.
HADHI_ENHANCED = [
    [(100254, 200508), (74517, 149034), (62254, 124509), (50948, 101896)],
    [(111394, 222787), (82796, 165593), (69172, 138343), (56609, 113218)],
    [(150076, 300151), (111548, 223097), (93192, 186384), (76267, 152534)],
    [(203383, 406766), (151170, 302340), (126294, 252588), (92736, 185472)],
    [(254381, 508762), (189076, 378151), (157962, 315924), (103357, 206714)],
    [None, None, None, (115990, 231979)],
]

#: PARTICULARS, per plan A, B, C, D
HADHI_BENEFITS = [
    ("Bed capacity (charges net of NHIF)", [20000, 16500, 14500, 10500]),
    ("Newly diagnosed conditions including HIV/AIDS", ["full limit"] * 4),
    ("Pre-existing, chronic and HIV/AIDS cover", [1000000, 600000, 400000, 250000]),
    ("Organ transplant (2 years waiting period), within the chronic limit",
     [1000000, 500000, 300000, 250000]),
    ("Prematurity and congenital conditions", [250000, 150000, 100000, 75000]),
    ("Psychiatric and psychotherapy illness", [600000, 400000, 250000, 100000]),
    ("Inpatient accidental optical and dental expenses", ["full limit"] * 4),
    ("Inpatient non-accidental dental cover", [200000, 150000, 100000, 75000]),
    ("Inpatient non-accidental optical cover, including cataract operation but excluding "
     "laser eye surgery", [200000, 150000, 100000, 75000]),
    ("Post-hospitalisation visits/follow-ups within 30 days after discharge or the limit, "
     "whichever comes first (accident cases)", [50000, 40000, 30000, 25000]),
    ("Day case surgery under general anaesthesia", ["covered"] * 4),
    ("Home care for 1 month", ["covered"] * 4),
    ("Emergency air evacuation within Kenya", ["covered"] * 4),
    ("Emergency local road ambulance service leading to an admission", ["covered"] * 4),
    ("Overseas evacuation, to the specified limit", ["covered"] * 4),
    ("Cover outside Kenya while on business or leisure, on reimbursement, up to 8 weeks",
     ["covered"] * 4),
    ("Pathology, X-ray, ultrasound, MRI scans and other radiological procedures",
     ["covered"] * 4),
    ("Economy air ticket for treatment not locally available", ["covered"] * 4),
    ("Radiotherapy and chemotherapy", ["covered"] * 4),
    ("Prescription drugs", ["covered"] * 4),
    ("External appliances on prescription (wheelchairs, corsets, walking frames, crutches "
     "and all assistive devices for PWDs)", [150000, 100000, 80000, 50000]),
    ("Internal appliances on prescription, to the specified limit", ["covered"] * 4),
    ("Terrorism and political violence", ["covered"] * 4),
    ("Last expense per family", [100000, 100000, 100000, 50000]),
    ("Traumacare extended to cover illness emergencies", ["covered"] * 4),
]

#: "Optional COVID-19 hospitalization cover -- Within Chronic limit"
HADHI_COVID = [1000000, 500000, 300000, 250000]


# =============================================================================
# Heritage Insurance -- HeriAfya Medical Cover (brochure 10.12.2024) and the
# HeriAfya quotation tool (HERIAFYA QUOTATION TOOL - HERITAGE INSURANCE.xlsm)
# =============================================================================

#: A: INPATIENT COVER SHARED PER FAMILY, Option 1 to Option 10
HA_LIMITS = [500000, 750000, 1000000, 1500000, 2000000, 2500000, 3000000, 5000000,
             7500000, 10000000]

#: Age groups of the principal and spouse rows: the brochure prints 0-30 and
#: 61-65, the quotation tool 18-30 and 61-70 (to 70 for members renewing);
#: entry is 18 to 65 ("Simple Eligibility").
HA_BANDS = [(18, 30), (31, 40), (41, 50), (51, 60), (61, 65)]

#: per option: ([principal by band], [spouse by band], child, extra). The
#: quotation tool prices each adult on their own age group, a first child on
#: "Child" and every other child on "Extra" (its "Additional Child").
HA_INPATIENT = [
    ([13500, 20250, 23625, 27000, 40500], [11475, 17213, 20081, 22950, 34425], 8438, 6750),
    ([18225, 27338, 31894, 36450, 54675], [15491, 23237, 27110, 30983, 46474], 11391, 9113),
    ([21600, 32400, 37800, 43200, 64800], [18360, 27540, 32130, 36720, 55080], 13500, 10800),
    ([26325, 34020, 39690, 46251, 68040], [22376, 28917, 33737, 39313, 57834], 16453, 13163),
    ([29700, 35640, 41580, 49302, 71280], [25245, 30294, 35343, 41907, 60588], 18563, 14850),
    ([32063, 39204, 45738, 57051, 79380], [27253, 33323, 38877, 48493, 67473], 20039, 16031),
    ([32400, 42768, 49896, 64800, 87480], [27540, 36353, 42412, 55080, 74358], 20250, 16200),
    ([40500, 53460, 62370, 79380, 121500], [34425, 45441, 53015, 67473, 103275], 25313, 20250),
    ([47250, 58725, 66623, 85050, 133650], [40163, 49916, 56629, 72293, 113603], 31641, 25313),
    ([54000, 63990, 70875, 90720, 145800], [45900, 54392, 60244, 77112, 123930], 33750, 27000),
]

#: B1/B2 outpatient limits
HA_OUTPATIENT_LIMITS = [50000, 75000, 100000, 150000, 200000, 250000, 300000, 350000]

#: B1: OUTPATIENT COVER PER-PERSON BENEFIT, "Per-Person Rates/ M Rates", by
#: each person's own age ("1-30"; the tool prices 0-17 on the same rates)
HA_OUTPATIENT_PER_PERSON = [
    (0, 30, [21600, 27000, 32400, 37800, 47520, 56038, 59535, 65927]),
    (31, 40, [30375, 36450, 42525, 48600, 71685, 82080, 86400, 95040]),
    (41, 50, [40500, 47250, 54000, 60750, 95850, 106542, 112860, 122905]),
    (51, 60, [47250, 54000, 60750, 67500, 118800, 133515, 139320, 150930]),
    (61, 65, [49275, 73913, 98550, 131625, 175500, 219375, 263250, 307125]),   # "61-65*"
]

#: B2: OUTPATIENT COVER SHARED PER FAMILY, "Per-Family Shared Benefit": the
#: M row, then M+1 to M+5, then "Extra" (each person after M+5 -- the tool's
#: M+6 to M+8 rows are M+5 plus one, two and three Extras).
HA_OUTPATIENT_SHARED_M = [35438, 41850, 48263, 54675, 83768, 94311, 99630, 108973]
HA_OUTPATIENT_SHARED = [
    [45849, 55308, 59636, 71820, 75026, 95040, 109350, 119273],     # M+1
    [46996, 59456, 68582, 82593, 86569, 113400, 131220, 142358],    # M+2
    [48170, 63915, 78869, 94982, 100035, 135000, 155520, 168008],   # M+3
    [49375, 68709, 90699, 109229, 114464, 158760, 183465, 198788],  # M+4
    [49700, 73862, 99000, 125614, 131777, 186840, 216270, 233415],  # M+5
]
HA_OUTPATIENT_SHARED_EXTRA = [1485, 4658, 13770, 16065, 18225, 28080, 32805, 34628]
#: the tool rates families up to M+8
HA_OUTPATIENT_SHARED_EXTRAS = 3

#: C: MATERNITY COVER PER FAMILY: (cover benefit, premium)
HA_MATERNITY = [
    (50000, 16875), (75000, 25313), (100000, 33750), (150000, 50625),
    (200000, 67500), (250000, 84375), (300000, 101250), (350000, 118125),
]

#: D: DENTAL/OPTICAL COVER: (cover limit, premium per person = per family).
#: "Optical and dental limits are twin benefits and should be taken together
#: with equal limits and distribution" (the tool's notes).
HA_DENTAL_OPTICAL = [
    (10000, 3780), (15000, 5670), (20000, 7560), (25000, 9450),
    (30000, 11340), (40000, 15120), (50000, 18900), (60000, 22680),
]

#: E: DENTAL & OPTICAL COMBINED SOLUTION: (cover limit, premium)
HA_DENTAL_OPTICAL_COMBINED = [
    (10000, 6048), (15000, 9072), (20000, 12096), (25000, 15120),
    (30000, 18144), (40000, 24192), (50000, 30240), (60000, 36288),
]

#: SUB-LIMITS WITHIN THE OVERALL BENEFIT, per inpatient option
HA_INPATIENT_BENEFITS = [
    ("Ward", ["general ward bed"] * 4 + ["standard private room (SPR) up to KES 13,500",
     "standard private room (SPR) up to KES 15,000", "private room up to KES 18,500",
     "private room up to KES 22,500", "private room up to KES 27,500",
     "private room up to KES 30,000"]),
    ("Acute conditions or accidents", ["covered"] * 10),
    ("Pre-existing and chronic conditions (1-year waiting period)",
     [300000, 350000, 400000, 450000, 500000, 600000, 750000, 1200000, 1500000, 2000000]),
    ("Congenital, pre-maturity and neonatal conditions treatment (for members with an "
     "active maternity benefit)",
     [100000, 125000, 150000, 175000, 200000, 225000, 250000, 300000, 500000, 650000]),
    ("Newly diagnosed chronic conditions after inception of cover",
     [350000, 450000, 750000, 900000, 1200000, 1800000, 2000000, 2500000, 3000000, 3500000]),
    ("Cancer treatment (2-year waiting period)",
     [250000, 300000, 350000, 500000, 750000, 1000000, 1500000, 2000000, 2500000, 3000000]),
    ("HIV/AIDS and related conditions (1-year waiting period)",
     [200000, 200000, 200000, 200000, 300000, 300000, 500000, 750000, 1000000, 1500000]),
    ("Post-hospitalisation treatment related to the cause of pre-authorisation, limited to "
     "3 weeks from the date of discharge",
     [15000, 15000, 20000, 25000, 30000, 30000, 30000, 40000, 50000, 60000]),
    ("Non-accident-related dental surgery/treatment (6-month waiting period)",
     [75000, 75000, 75000, 75000, 75000, 100000, 100000, 100000, 150000, 200000]),
    ("Non-accident-related eye treatments, excluding surgery for refractive errors and laser "
     "treatment (6-month waiting period)",
     [75000, 75000, 75000, 75000, 75000, 100000, 100000, 100000, 150000, 200000]),
    ("Diagnostic tests", ["covered"] * 10),
    ("Inpatient MRI/CT and PET scans (subject to pre-authorisation)", ["covered"] * 10),
    ("Theatre charges, HDU and ICU", ["covered"] * 10),
    ("Gynaecological surgery (1-year waiting period)",
     [150000, 150000, 200000, 250000, 300000, 300000, 300000, 350000, 400000, 500000]),
    ("Organ transplantation, excluding the cost of the donor or securing the organ (2-year "
     "waiting period)",
     [250000, 250000, 300000, 350000, 400000, 450000, 450000, 500000, 550000, 600000]),
    ("Psychiatry and psychotherapy",
     [100000, 150000, 150000, 200000, 250000, 250000, 250000, 300000, 400000, 450000]),
    ("Illness-related reconstructive/plastic surgery (3-year waiting period)",
     [150000] * 8 + [200000, 300000]),
    ("Covid treatment on inpatient",
     [250000, 250000, 300000, 350000, 400000, 450000, 450000, 500000, 550000, 600000]),
    ("Funeral expenses per member (death as a result of covered conditions)",
     [50000, 50000] + [100000] * 8),
    ("Lodger fee for an accompanying parent/guardian", ["for a child up to 12 years"] * 10),
    ("Passive war/terrorism and political violence",
     [200000, 200000, 200000, 200000, 300000, 300000, 500000, 750000, 1000000, 1500000]),
    ("External appliances", [75000] * 4 + [100000] * 3 + [150000] * 3),
    ("Emergency evacuation", ["covered"] * 10),
    ("Internal appliances and prostheses",
     ["covered within the applicable sub-limits and waiting periods"] * 10),
    ("Home nursing, for a maximum of 30 days", ["covered within the applicable sub-limits"] * 10),
]

#: Outpatient, the same at every outpatient limit
HA_OUTPATIENT_BENEFITS = [
    "Consultation fees: covered",
    "COVID-19 testing: KES 15,000",
    "Cancer treatment (2-year waiting period): covered",
    "Prescribed drugs and dressings: covered",
    "Specialist's fees, strictly on referral by a GP: covered",
    "Pathology, X-rays, MRI, CT scan and other necessary diagnostic tests (pre-authorisation "
    "required): covered",
    "Pre-existing and/or chronic conditions (1-year waiting period): covered",
    "Pain management, subject to the pre-existing and chronic sub-benefit: covered",
    "Family planning limited to long-acting methods: KES 2,500",
    "Pre-natal and post-natal care, for members with the maternity benefit: covered under "
    "outpatient",
]
#: and by outpatient limit
HA_OUTPATIENT_BY_LIMIT = [
    ("Annual medical check-up (main member and/or spouse)",
     [5000, 7500, 10000, 15000, 20000, 20000, 20000, 20000]),
    ("KEPI/baby-friendly and private vaccinations (5 years and below)",
     [5000, 7500, 10000, 15000, 20000, 20000, 20000, 20000]),
]

#: HeriAfya inpatient PER PERSON (the quotation tool's "Per Person" basis): each
#: person has the option's limit to themselves. The tool's per-person table is
#: the per-family one with the spouse on the principal's rate, by their own age
#: group; Child and Additional Child are unchanged. Built from HA_INPATIENT by
#: the generator (spouse = principal), not retyped.


# =============================================================================
# APA Insurance -- Afya Nafuu (brochure; "Rates are as at June 2017")
# =============================================================================

#: Core plans and their inpatient overall maximum benefit per year
AN_PLANS = ["Affluent", "Executive", "Standard", "Select", "Mwananchi"]
AN_LIMITS = [1000000, 750000, 500000, 300000, 100000]

#: "Premiums are calculated based on the members age at next birthday". The
#: bands as printed (age next birthday); the generator moves each down a year
#: so the agent enters the client's age today, as for every other card.
#: "1 Months - 20 Years" is 0 to 20.
AN_INPATIENT_BANDS = [(0, 20), (21, 40), (41, 54), (55, 65), (66, 75)]
#: PER PERSON INPATIENT RATES, by band, Affluent to Mwananchi
AN_INPATIENT = [
    [9417, 7788, 6727, 6115, 5500],
    [16300, 13480, 11643, 10584, 8900],
    [23499, 19779, 17043, 15084, 11120],
    [31552, 26804, 23066, 20128, 16543],
    [42350, 38115, 30492, 25918, 19890],
]

#: OUT PATIENT COVER PER PERSON (OPTIONAL): the rates page's limits, which
#: rise with the premium (the benefits page prints the same five limits in the
#: opposite order under the plan names). Offered as five tiers with every plan.
AN_OUTPATIENT_LIMITS = [100000, 75000, 50000, 40000, 30000]
AN_OUTPATIENT_BANDS = [(0, 20), (21, 40), (41, 60), (61, 75)]
AN_OUTPATIENT = [
    [18200, 13950, 11100, 9000, 6700],
    [20700, 16500, 12900, 11090, 9100],
    [23200, 19050, 16300, 13450, 11520],
    [26200, 23300, 20750, 17818, 14750],
]
#: Maximum consultation limit per visit, by outpatient limit (benefits page)
AN_OUTPATIENT_CONSULTATION = {30000: 1000, 40000: 1000, 50000: 1500, 75000: 1500,
                              100000: 2000}

#: OPTIONAL MATERNITY COVER PER PERSON (FEMALE PRINCIPAL OR SPOUSE): one
#: premium per policy. Printed under Standard, Select and Mwananchi only;
#: offered as three tiers with every plan.
AN_MATERNITY = [(50000, 7500), (75000, 10000), (100000, 15000)]

#: Inpatient benefits, Affluent to Mwananchi
AN_INPATIENT_BENEFITS = [
    ("Bed", ["general ward bed"] * 5),
    ("Prescription drugs and dressings (discharge drugs up to 14 days' supply)",
     ["covered"] * 5),
    ("Physicians', specialists' and surgical fees, including anaesthetists' fees, at APA "
     "panel rates", ["covered"] * 5),
    ("Theatre charges, HDU and ICU", ["covered"] * 5),
    ("Diagnostic tests; CT and MRI scans subject to pre-authorisation", ["covered"] * 5),
    ("Physiotherapy as part of treatment", ["covered"] * 5),
    ("Pre-existing, chronic and congenital conditions, HIV/AIDS and psychiatry, after 12 "
     "months of cover and on full disclosure; organ transplant from the 3rd year",
     [250000, 200000, 150000, 100000, 50000]),
    ("Inpatient dental", [40000, 30000, 30000, 20000, 10000]),
    ("Inpatient ophthalmology, including cataract removal (1-year waiting period)",
     [40000, 30000, 30000, 20000, 10000]),
    ("First-ever emergency caesarean section and maternity complications, after 10 months "
     "of cover", [50000, 40000, 30000, 30000, 30000]),
    ("Reconstructive surgery, and emergency dental or optical treatment, following an "
     "accident", ["covered"] * 5),
    ("Post-hospitalisation treatment, reimbursement only, first 2 weeks after discharge",
     [20000, 15000, 10000, 10000, 7500]),
    ("Funeral expenses", [30000, 25000, 25000, 20000, 20000]),
    ("Day care surgery under general anaesthesia", ["covered"] * 5),
    ("Home nursing on a doctor's recommendation", ["up to 30 days"] * 5),
    ("Accommodation for one parent staying with an insured child under 8", ["covered"] * 5),
    ("Local ambulance to hospital for emergencies", ["covered"] * 5),
]
