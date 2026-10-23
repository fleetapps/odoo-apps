# Motor Quotes

Quote motor cover the way medical cover is quoted. Go to **Cover Quotes ▸
New quote ▸ Motor**, or use **New ▾ Motor quote** on the Quotes list.

## The screen

1. **Client and vehicle.**
   * The client: a new prospect, an existing customer or a CRM lead, the
     same block medical quotes use.
   * The vehicle:
     * type;
     * **Comprehensive** or **Third party only**;
     * value (comprehensive);
     * year of manufacture;
     * passengers, for PSVs, school buses and the like;
     * tonnage, for third-party goods vehicles;
     * period (some third-party rates are for 1 or 6 months);
     * registration and make and model, both optional and printed on the
       quote.
2. **Insurers and premiums.**
   * Every insurer with a rate for the vehicle, cheapest basic premium
     first. The basic premium is a % of the value with the insurer's
     minimum, or a fixed third-party premium.
   * What is always charged is in the price, such as passenger legal
     liability (PLL) for a PSV. What is included is marked, for example
     "PVT included".
   * Optional extras have a Select button and their price:
     * political violence and terrorism (PVT);
     * excess protector;
     * PLL;
     * courtesy car for 10 or 20 days.
   * Insurers that will not take the vehicle are listed with the reason:
     too old, worth less than their minimum, or not for that period.
3. **Compare, then save and share.** Exactly as for medical:
   * a discount and a policy charge;
   * taxes and levies with stamp duty;
   * the total and the monthly instalments;
   * one recommended insurer.

Saving makes the same records as a medical quote:
* the contact;
* a CRM opportunity *Motor cover – Peter Kamau (KDA 123A)*, tagged
  **Motor**;
* one quotation per insurer, carrying the rate's notes as its terms;
* the quote in the Quotes register (Type: Motor).

Print (with *Vehicle details* in place of *Member details*), Send by email
(its own template), WhatsApp, Client accepted, the hand-over and the
insurance invoice all work unchanged.

## Rates

Rates are kept in **Cover Quotes ▸ Configuration ▸ Motor rates**, grouped by
insurer. Each line covers one insurer, vehicle type and cover, within an
optional band of value, tonnage or passengers, and a period:

* **Rate (%)** with a **minimum premium**, or a fixed **Premium**.
* **Vehicle age up to**: older vehicles are refused, with the reason.
* **PVT** and **Excess protector**: not offered, included, or optional at
  a % of value with a minimum, or a fixed price.
* **PLL per passenger**, and whether it is **required** (always charged)
  or optional.
* **Courtesy car** for 10 and 20 days.
* **Other charge**: anything always added, such as mandatory personal
  accident cover.
* **Notes**, printed with the quote.

When a vehicle's value falls in several bands of one insurer, the band it
falls in is used. Below an insurer's lowest band, the insurer says
"Vehicles worth X or more".

Vehicle types (private car, commercial, PSV, motorcycle, tuk-tuk...) are in
**Configuration ▸ Vehicle types**, with whether to ask for passengers or
tonnage.

**Levies:** the motor products (Motor premium, PVT, Excess protector, PLL,
Courtesy car, Motor cover charge) carry no taxes until you set the
insurance levies on them. `odin_bizsure_quotes` does that for Bizsure.
Stamp duty is *FEE-STAMP-MOTOR*, KES 40.

## Tests

`tests/` covers:
* rates with minimums, value bands, refusals by age and value, PSV
  passengers, totals with extras and a discount;
* saving to CRM, saving again, the PDF, email and acceptance;
* a browser tour from New ▾ Motor quote to a saved quote.
