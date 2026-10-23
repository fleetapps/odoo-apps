# Motor Quotes Online

**`/motor-quote`**: anyone can compare motor insurance for their vehicle in
plain words, keep the quote and ask to be called. It works like
`/medical-quote` (`odin_medical_quote_online`): the same look, the same
client quote page, PDF and Talk to an advisor. Quotes are followed up the
same way: the agent's link, the client's own agent, the person in Settings,
or the team pool with **Assign to me**.

## The visitor

1. **Your vehicle.**
   * **What do you drive?** Tiles in plain words: Private car, Taxi, Uber or
     matatu, Boda boda, Lorry or van... Only types with a rate shown online
     appear. *Something else, or several vehicles? Talk to us* leads to
     WhatsApp.
   * **What cover do you want?**
     * *Full cover (comprehensive)*: your own vehicle too, as well as damage
       and injuries you cause to others.
     * *Third party only*: the legal minimum.
   * The other questions:
     * what the vehicle is worth today, shown back as *1,500,000 KSh*;
     * the year it was made;
     * passengers or tonnes where the rate needs them;
     * for how long, where third-party rates exist for 1 or 6 months.
2. **Prices.**
   * Every insurer that takes the vehicle, cheapest first, with levies and
     stamp duty included and the monthly option.
   * What comes with each price is listed, for example *Riots, strikes &
     terrorism: included*.
   * **Add to this cover**: the insurer's extras with their price, and the
     total updates when one is ticked:
     * riots, strikes & terrorism;
     * excess protector;
     * passenger cover;
     * a replacement car for 10 or 20 days.
   * If nobody can quote it online, the page says why (*Vehicles up to 15
     years old*) and offers WhatsApp.
3. **Where should we send your quote?** The same as medical, plus an
   optional number plate and make and model, which are printed on the quote.

## The office

* **The opportunity:**
  * named *Online motor quote – Name (KDA 123A)*;
  * tagged *Online quote* and *Motor*;
  * source *Online motor quote*, or the ad's;
  * a note asks to confirm the value, year and use (logbook, valuation).
* **The quotations** are the same as Add Motor Quote's, one per insurer,
  with the extras chosen. Edit quote opens them there.
* **Configuration ▸ Vehicle types:**
  * *Show online*;
  * the public name, hint and icon.
  * Construction plant, farm vehicles, prime movers and special vehicles
    are not shown online by default.
* **Configuration ▸ Motor rates:** *Show online* on each rate.
* **Settings ▸ Online quotes** shows the motor page's link. **Share quote
  link ▸ Motor** gives an agent's own link or a campaign link.

## Tests

`tests/` covers:
* the vehicle types and covers offered;
* prices with what is included;
* extras changing the price;
* refusals and checks;
* third-party cover for a PSV;
* saving to CRM and the client's page;
* changing answers and sharing from a Motor lead;
* the pages over HTTP;
* a browser tour from the vehicle to the quote page.
