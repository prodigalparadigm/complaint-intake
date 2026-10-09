"""Synthetic complaint generator.

Synthetic because real complaint text is exactly the data the client told us
must not move. Everything here is fabricated: names, accounts, card numbers
(Luhn-valid but from the reserved test ranges), 555 phone numbers, example.com
addresses.

Each record carries gold labels, and the choice of what to label is itself a
finding:

  gold.regulatory     categories Compliance would expect to be flagged.
                      Labelled, because this is close to objective — a
                      disputed transaction is a Reg E matter whatever anyone's
                      mood.
  gold.department     owning department per the ops head's own rules.
  gold.pii_values     the exact identifier strings injected, so redaction
                      recall is measured rather than asserted.
  gold.severity_floor the band BOTH senior analysts would agree is a minimum,
                      and ONLY for cases where that agreement is obvious.

There is deliberately no gold severity band for ordinary cases. We cannot
score a system against a definition the client does not have, and producing a
gold column here would have meant inventing the answer and then grading
ourselves against our own invention. The severity evidence is a measured
disagreement rate instead. See docs/EVIDENCE.md.

  gold.quiet_regulatory  marks complaints that are genuinely regulatory but
                      use none of the trigger vocabulary. These exist to
                      measure the keyword engine's blind spot honestly rather
                      than to flatter it.
"""
from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "synthetic" / "complaints.jsonl"

SEED = 20261008

FIRST = ["Harold", "Denise", "Marcus", "Yolanda", "Ravi", "Beatrice", "Curtis", "Imelda",
         "Tomas", "Gwen", "Abdul", "Lorraine", "Sean", "Priya", "Walter", "Noemi",
         "Dwight", "Clara", "Felix", "Rosalind"]
LAST = ["Ferris", "Okonkwo", "Baptiste", "Vargas", "Lindqvist", "Mbeki", "Toomey",
        "Castellanos", "Whitfield", "Nakamura", "Durand", "Ibarra", "Pulaski",
        "Rasheed", "Hollings", "Moreau"]
STREETS = ["Pearl Street", "Alder Avenue", "Kestrel Lane", "Foundry Road", "Lindell Drive",
           "Beaumont Court", "Harrow Way", "Tupelo Boulevard"]
BRANCHES = ["Riverside", "Oakvale", "Pine Hollow", "Dunmore", "Cedar Point", "Westgate"]


def luhn_complete(prefix: str, length: int) -> str:
    digits = [int(c) for c in prefix]
    while len(digits) < length - 1:
        digits.append(random.randint(0, 9))
    total, parity = 0, (len(digits) + 1) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    digits.append((10 - total % 10) % 10)
    return "".join(map(str, digits))


def aba_complete() -> str:
    while True:
        d = [random.randint(0, 9) for _ in range(8)]
        for last in range(10):
            full = d + [last]
            chk = 3 * (full[0] + full[3] + full[6]) + 7 * (full[1] + full[4] + full[7]) + (full[2] + full[5] + full[8])
            if chk % 10 == 0 and full[0] != 0:
                return "".join(map(str, full))


# ---------------------------------------------------------------- archetypes
# (id, gold_regulatory, gold_department, severity_floor, quiet, body)

# Label corrections made during evaluation, recorded rather than quietly applied:
#
#  loan_discrim  was labelled ECOA_FAIRLEND + UDAAP. Dropped UDAAP: the primary
#                citation for a fair-lending complaint is ECOA, and double-labelling
#                it scored a correct ECOA-only detection as a category miss.
#  hold_forever  was labelled BRANCH_OPS. Corrected to GENERAL_SERVICING, because a
#                "you kept me on hold and nobody called back" complaint is not a
#                branch matter. This exposed a real gap: the department list the ops
#                head gave us has no owner for contact-centre service failures.
#                See docs/DECISIONS.md Q5.
A = [
 ("unauth_card", ["REG_E_EFT"], "CARDS_DISPUTES", None, False,
  "I am writing about four charges on my debit card ending in {CARD4} that I did not authorize. "
  "They total {AMOUNT} and they all posted on the same day to a merchant I have never heard of. "
  "I called the number on the back of the card and was told to write in. My account is {ACCT}. "
  "I need this money back."),

 ("atm_no_cash", ["REG_E_EFT"], "CARDS_DISPUTES", None, False,
  "On Tuesday I went to the {BRANCH} branch ATM and asked for {AMOUNT}. The machine made the noise "
  "like it was counting but the ATM did not dispense any cash. My account was debited anyway. "
  "Account {ACCT}. I have the receipt showing the transaction."),

 ("double_charge", ["REG_E_EFT"], "CARDS_DISPUTES", None, False,
  "I was charged twice for the same purchase of {AMOUNT} at a grocery store. It is a duplicate charge, "
  "same amount, same date, same merchant. I was double charged and nobody at the 800 number could "
  "explain it. Card ending {CARD4}, account {ACCT}."),

 ("od_stacking", ["REG_DD_FEES", "UDAAP"], "DEPOSIT_OPS", None, False,
  "I had three overdraft fees of $38 each hit my checking account {ACCT} on the same morning. "
  "You reordered my transactions so the biggest one came out first, which is what caused the other "
  "two to bounce. That is $114.00 in fees on a {SMALL} shortfall. This feels deliberately designed "
  "to generate fees and it is unfair."),

 ("fee_undisclosed", ["REG_DD_FEES", "UDAAP"], "DEPOSIT_OPS", None, False,
  "There is a monthly fee of {SMALL} on my savings account {ACCT} that was never disclosed to me "
  "when I opened it at the {BRANCH} branch. The person who opened the account told me there was no "
  "monthly fee. I was misled. I want every one of these fees back, it adds up to {AMOUNT}."),

 ("quiet_fee", ["REG_DD_FEES"], "DEPOSIT_OPS", None, True,
  "When I opened my checking account the banker went through the paperwork with me and said the "
  "account had nothing taken out of it each month as long as I kept my direct deposit. I have had my "
  "direct deposit the whole time. Looking at my statements going back to the spring there is a {SMALL} "
  "charge every single month. Nobody mentioned that this could happen. Account {ACCT}. Over the year "
  "that is {AMOUNT} and I would like to understand what the basis for it is."),

 ("quiet_dispute", ["REG_E_EFT"], "CARDS_DISPUTES", None, True,
  "There is a transaction on my statement for {AMOUNT} from a company I have never done business with. "
  "I have checked with my husband and with my daughter who is on the account and neither of them "
  "recognise it either. None of us made this purchase. It came out of account {ACCT} on the 14th. "
  "Could someone look into where it came from, please."),

 ("deposit_hold", [], "DEPOSIT_OPS", None, False,
  "I made a mobile deposit of {AMOUNT} and you have put a hold on my deposit for nine business days. "
  "Nobody told me that could happen. I can't access my money and I have bills coming out. "
  "Account {ACCT}."),

 ("app_login", [], "DIGITAL_BANKING", None, False,
  "Your mobile app has been crashing every time I try to log in since the update. I reset my password "
  "twice and the two factor code never arrives. I have a phone number on file, {PHONE}. I have had to "
  "drive to the {BRANCH} branch twice just to check my balance."),

 ("zelle_missing", ["REG_E_EFT"], "CARDS_DISPUTES", None, False,
  "I sent {AMOUNT} by Zelle to my son on the 3rd and the money never arrived. The transfer never "
  "showed up on his end and it is gone from my account {ACCT}. Your app says it was delivered. "
  "It was not."),

 ("loan_denied_vague", ["ECOA_FAIRLEND"], "LENDING", None, False,
  "I applied for a personal loan of {AMOUNT} and was denied without any reason given. When I called, "
  "the person wouldn't tell me why I was denied, just that it was the system. I have banked here for "
  "{TENURE} years. I would like to know the actual basis for the decision."),

 ("loan_discrim", ["ECOA_FAIRLEND"], "LENDING", "S2", False,
  "My husband and I applied for the same auto loan with the same income and the same credit. "
  "My husband was approved and I was declined. The only difference is that I am a woman and I am 61. "
  "I think I was treated differently than he was because of my age. I want this looked at by someone "
  "senior. Loan application on account {ACCT}."),

 ("escrow_tax", ["RESPA_SERVICING"], "MORTGAGE_SERVICING", "S2", False,
  "My escrow account did not pay my property tax and I have now received a delinquency notice from "
  "the county for {AMOUNT}. I have paid my mortgage every month on time for {TENURE} years. "
  "The property tax was not paid out of the escrow you hold. Loan {ACCT}. This needs fixing before "
  "the county adds more interest."),

 ("payoff_wrong", ["RESPA_SERVICING"], "MORTGAGE_SERVICING", None, False,
  "I requested a payoff statement to refinance and the figure you sent is {AMOUNT} higher than what "
  "my own amortization shows. My payment was applied to principal incorrectly in at least two months. "
  "Mortgage {ACCT}. My closing is in eleven days."),

 ("collections_work", ["FDCPA_COLLECT"], "COLLECTIONS", "S2", False,
  "Your collection department called me at work four times on Thursday after I asked them to stop "
  "calling me at work. They told my coworker who answered that it was about a debt. That is nobody "
  "else's business. The person also threatened to garnish my paycheck. Account {ACCT}."),

 ("fcra_late", ["FCRA_FURNISH"], "DEPOSIT_OPS", None, False,
  "You reported me as delinquent to Experian on a payment I made on time. My credit score dropped "
  "{POINTS} points and I am in the middle of buying a house. I have the confirmation number for the "
  "payment. Account {ACCT}. This has damaged my credit and I need it corrected with the credit bureau."),

 ("teller_rude", [], "BRANCH_OPS", None, False,
  "The teller at the {BRANCH} branch was rude to me on Saturday. I asked her to explain a hold on my "
  "account and she rolled her eyes and told me to use the app. I am {AGE} and I do not use the app. "
  "I have banked at that branch for {TENURE} years."),

 ("branch_hours", [], "BRANCH_OPS", None, False,
  "The {BRANCH} branch closed early on Friday with no notice. I drove twenty minutes to get there "
  "before five and the door was locked at ten to. The posted hours say five thirty. This is the second "
  "time this has happened."),

 ("hold_forever", ["UDAAP"], "GENERAL_SERVICING", None, False,
  "I have called three times about a hold on my account and each time you kept me on hold for over "
  "forty minutes and then the call dropped. Nobody would help me. Nobody has ever called me back. "
  "I am {AGE} years old and this is no way to treat a customer of {TENURE} years."),

 ("apr_jump", ["REG_Z_BILLING"], "CARDS_DISPUTES", None, False,
  "My credit card APR went up from 17.99 to 29.99 with no explanation. I have never missed a payment. "
  "The promotional rate was supposed to run through next year. My minimum payment is now {AMOUNT} "
  "higher. Card ending {CARD4}."),

 ("payment_not_credited", ["REG_Z_BILLING"], "CARDS_DISPUTES", None, False,
  "I paid {AMOUNT} on my credit card on the 2nd and the payment was not credited until the 9th, "
  "so you charged me a late fee and a finance charge. My statement is wrong. Card {ACCT}."),

 ("identity_theft", ["REG_E_EFT"], "FRAUD_SECURITY", "S2", False,
  "Someone used my identity to open an account and there have been withdrawals I did not authorize. "
  "Someone used my social security number {SSN}. I have filed a police report. There is a suspicious "
  "login from a state I have never been to. My own account {ACCT} has been emptied of {AMOUNT}. "
  "This is identity theft and I need my account frozen and protected."),

 ("cd_apy", ["REG_DD_FEES", "UDAAP"], "DEPOSIT_OPS", None, False,
  "I opened a certificate of deposit for {AMOUNT} on the understanding that the APY was 4.75 percent. "
  "The statement shows 2.1 percent. The banker at {BRANCH} told me one thing and the paperwork says "
  "another. This is a bait and switch."),

 ("closed_no_notice", ["UDAAP"], "DEPOSIT_OPS", "S2", False,
  "You closed my account {ACCT} without any notice and without telling me why. I found out when my "
  "card was declined at the pharmacy. There was {AMOUNT} in it. I can't access my money and nobody "
  "will explain. I have asked three times and nobody would explain."),

 ("wire_stuck", [], "DEPOSIT_OPS", "S2", False,
  "I wired {AMOUNT} for my daughter's tuition eight days ago and it has not arrived. The money is "
  "gone from account {ACCT} and the school says they have nothing. Funds unavailable on both ends. "
  "Her enrolment is at risk."),

 ("od_optout", ["UDAAP", "REG_DD_FEES"], "DEPOSIT_OPS", None, False,
  "I asked to be taken off overdraft protection in March and you enrolled me without my consent "
  "again in June. I have now paid {AMOUNT} in overdraft fees that I would not have paid. "
  "I wouldn't let me cancel is what it feels like, I have asked twice. Account {ACCT}."),

 ("auto_gap", [], "LENDING", None, False,
  "I paid off my auto loan early and I am owed a refund on the GAP policy that was sold with it. "
  "That is about {AMOUNT}. It has been {WEEKS} weeks and three phone calls. Loan {ACCT}."),

 ("deceased", [], "BRANCH_OPS", "S2", False,
  "My late husband passed away in August and I am trying to get access to our joint account {ACCT}. "
  "I have brought the death certificate to the {BRANCH} branch twice and been sent away both times "
  "for a different document. I am {AGE} and on a fixed income and his social security was going into "
  "that account. I cannot pay the utilities this month."),

 ("cfpb_fee", ["REG_DD_FEES", "REGULATOR_CONTACT", "LEGAL_MEDIA_THREAT", "UDAAP"], "DEPOSIT_OPS", "S1", False,
  "This is my third time writing about {AMOUNT} in fees on account {ACCT} that nobody can justify. "
  "I have filed a complaint with the CFPB and I have spoken to a lawyer. I was misled about these "
  "fees when the account was opened. I expect a written response."),

 ("negated_dispute", [], "DIGITAL_BANKING", None, False,
  "I want to be clear that I am not saying this was unauthorized — I think my wife made the purchase. "
  "What I am complaining about is that your app showed the wrong balance for three days afterwards, "
  "which made me think we had been charged twice. The app is wrong, not the charge. "
  "Account {ACCT}, {AMOUNT}."),
]

# Modifier cycles. Fixed-length and indexed by the global counter so the corpus
# is deterministic AND the proportions are deliberate. The proportions matter:
# an earlier draft gave a third of all complaints acute hardship language,
# which pushed 60% of the corpus into S1 and made the severity distribution
# useless for judging the rubrics. A real complaint stream is mostly mundane.
_N = ""
_MILD = " Money is tight this month and I am paycheck to paycheck."
_FIXED = " I am on a fixed income and this is my social security money."
_RETIRED = " I am {AGE} and retired."
_RENT = " My rent is due on the first and I cannot pay my rent without this money."
_MEDS = " I cannot afford my medication until this is sorted out."

VULN_CYCLE = (
    [_N] * 11 + [_MILD] * 3 + [_FIXED] * 2 + [_RETIRED] * 2 + [_RENT] + [_MEDS]
)  # 20 slots: 55% none, 15% mild, 20% status, 10% acute

ESC_CYCLE = (
    [""] * 12
    + [" If this is not fixed I am closing my accounts and moving my money."] * 4
    + [" I have contacted the Better Business Bureau about this."] * 2
    + [" My attorney has advised me to put this in writing."]
    + [" I have already filed a complaint with the CFPB."]
)  # 20 slots: 5% regulator, 5% attorney, 10% BBB

RECUR_CYCLE = (
    [("", 0)] * 11
    + [(" This is the second time I have written about this.", 1)] * 5
    + [(" This is the third time I have written about this and nobody has called me back.", 2)] * 3
    + [(" This is the fourth time. Every month this happens.", 3)]
)  # 20 slots


ACCOUNT_WORD = re.compile(
    r"(?i)(?:acct|account|a/c|checking|savings|loan|card|mortgage)\W{0,18}\{ACCT\}"
)


def _has_account_word(template: str) -> bool:
    """Does the template already put an account-ish word before the slot?

    The keyword-anchored ACCOUNT redaction rule needs one nearby to fire. An
    earlier version tested only for the lowercase literal "account {ACCT}", so
    a template reading "Account {ACCT}." got a second one prepended and the
    corpus said "Account account 4471902233."
    """
    return bool(ACCOUNT_WORD.search(template))


def money(lo: int, hi: int) -> str:
    v = random.randint(lo, hi)
    return f"${v:,}.{random.choice(['00','00','49','95','12'])}"


# Per-archetype amount ranges. Without these the generator put a tuition-sized
# figure on a duplicate grocery charge, which makes a demo corpus that nobody
# in the room believes.
AMOUNT_RANGE = {
    "unauth_card": (150, 2500), "atm_no_cash": (40, 500), "double_charge": (15, 200),
    "od_stacking": (60, 95), "fee_undisclosed": (95, 400), "quiet_fee": (95, 400),
    "deposit_hold": (300, 3000), "zelle_missing": (50, 1200),
    "loan_denied_vague": (5000, 35000), "loan_discrim": (8000, 40000),
    "escrow_tax": (800, 4200), "payoff_wrong": (500, 6000),
    "fcra_late": (100, 900), "apr_jump": (20, 120),
    "payment_not_credited": (50, 900), "identity_theft": (500, 9000),
    "cd_apy": (5000, 40000), "closed_no_notice": (200, 5000),
    "wire_stuck": (2000, 18000), "od_optout": (100, 500),
    "auto_gap": (200, 900), "cfpb_fee": (100, 600),
    "negated_dispute": (20, 200), "collections_work": (300, 4000),
    "hold_forever": (50, 500), "teller_rude": (50, 500),
    "branch_hours": (50, 500), "app_login": (50, 500),
    "deceased": (200, 2000), "quiet_dispute": (40, 600),
}


def build_email(name: str, subject: str, body: str, email: str, phone: str, addr: str) -> str:
    return (
        f"{body}\n\n"
        f"Please call me at {phone} or reply to {email}.\n\n"
        f"Best regards,\n{name}\n{addr}\n\n"
        f"-- \nSent from my iPhone\n\n"
        f"On Mon, Sep 28, 2026 at 9:02 AM Meridian Harbor Bank <noreply@meridianharbor.example> wrote:\n"
        f"> Thank you for contacting us. Your reference is 88213.\n"
    )


def build_form(name: str, body: str, email: str, phone: str, acct_type: str, branch: str) -> str:
    return (
        f"Full name: {name}\n"
        f"Email: {email}\n"
        f"Phone: {phone}\n"
        f"Account type: {acct_type}\n"
        f"Branch: {branch}\n"
        f"Preferred contact: Email\n"
        f"Describe your complaint:\n{body}\n"
    )


# First person to third person, longest forms first. A rep writes "cx has the
# receipt", not "I have the receipt" — and the intake agent expands "cx" back to
# "customer", so the result has to be grammatical on the way out.
THIRD_PERSON = [
    ("I am writing about", "cx clmd re:"),
    ("I have ", "cx has "), ("I had ", "cx had "), ("I am ", "cx is "),
    ("I was ", "cx was "), ("I will ", "cx will "), ("I would ", "cx would "),
    ("I do not ", "cx does not "), ("I don't ", "cx doesn't "),
    ("I did not ", "cx did not "), ("I didn't ", "cx didn't "),
    ("I can't ", "cx cannot "), ("I cannot ", "cx cannot "),
    ("I want ", "cx wants "), ("I need ", "cx needs "),
    ("I think ", "cx thinks "), ("I expect ", "cx expects "),
    ("I called ", "cx called "), ("I asked ", "cx asked "),
    ("I went ", "cx went "), ("I applied ", "cx applied "),
    ("I paid ", "cx paid "), ("I made ", "cx made "),
    ("I opened ", "cx opened "), ("I sent ", "cx sent "),
    ("I wired ", "cx wired "), ("I reset ", "cx reset "),
    ("I requested ", "cx requested "), ("I filed ", "cx filed "),
    ("I spoke ", "cx spoke "), ("I bank ", "cx banks "),
    ("I live ", "cx lives "), ("I found ", "cx found "),
    ("I ", "cx "), ("My ", "cx "), ("my ", "cx "), (" me ", " cx "),
]

ABBREV = [
    ("unauthorized", "unauth"), ("overdraft", "OD"), ("account", "acct"),
    ("statement", "stmt"), ("transaction", "txn"), ("branch", "br"),
    ("manager", "mgr"), ("dispute", "disp"), ("frustrated", "frst"),
]


def build_note(name: str, body: str, phone: str, rep: str) -> str:
    short = body
    for a, b in THIRD_PERSON:
        short = short.replace(a, b)
    for a, b in ABBREV:
        short = short.replace(a, b)
    return (
        f"CALL NOTE - rep {rep}\n"
        f"Caller: {name} - CB {phone}\n"
        f"{short}\n"
        f"cx frst, refd transfer to sup. advised written response. ESC per matrix.\n"
    )


def main() -> None:
    random.seed(SEED)
    base = datetime(2026, 9, 14, 8, 0, 0)
    records = []
    idx = 0

    # Four variants of each archetype, cycling channel and modifiers, so every
    # archetype is exercised through every channel's normalisation path.
    for rep in range(4):
        for a_i, (arch_id, gold_reg, gold_dept, floor, quiet, body_t) in enumerate(A):
            idx += 1
            channel = ("email", "web_form", "call_note", "email")[(rep + a_i) % 4]
            name = f"{random.choice(FIRST)} {random.choice(LAST)}"
            title_name = f"{random.choice(['Mr.', 'Ms.', 'Mrs.', 'Dr.'])} {name}"
            acct = str(random.randint(10**9, 10**10 - 1))
            card = luhn_complete(random.choice(["4", "51", "55"]), 16)
            ssn = f"{random.randint(100,665)}-{random.randint(10,99)}-{random.randint(1000,9999)}"
            phone = f"({random.randint(201,989)}) 555-{random.randint(1000,9999)}"
            email = f"{name.split()[0].lower()}.{name.split()[1].lower()}@example.com"
            addr = f"{random.randint(100,9899)} {random.choice(STREETS)}"
            branch = random.choice(BRANCHES)
            tenure = random.choice([2, 4, 7, 11, 14, 22, 31])
            age = random.choice([58, 63, 71, 74, 78, 81])
            routing_no = aba_complete()

            lo, hi = AMOUNT_RANGE.get(arch_id, (140, 4200))
            amount = money(lo, hi)
            body = (body_t
                    .replace("{AMOUNT}", amount)
                    .replace("{SMALL}", money(8, 95))
                    .replace("{ACCT}", acct if _has_account_word(body_t) else f"account {acct}")
                    .replace("{CARD4}", card[-4:])
                    .replace("{SSN}", ssn)
                    .replace("{PHONE}", phone)
                    .replace("{BRANCH}", branch)
                    .replace("{TENURE}", str(tenure))
                    .replace("{AGE}", str(age))
                    .replace("{POINTS}", str(random.choice([42, 58, 71, 90])))
                    .replace("{WEEKS}", str(random.choice([3, 5, 7]))))

            vuln_txt = VULN_CYCLE[idx % len(VULN_CYCLE)]
            esc_txt = ESC_CYCLE[(idx * 7) % len(ESC_CYCLE)]
            recur_txt, prior = RECUR_CYCLE[(idx * 11) % len(RECUR_CYCLE)]
            body = body + vuln_txt.replace("{AGE}", str(age)) + esc_txt + recur_txt

            gold_reg = list(gold_reg)
            if "CFPB" in esc_txt:
                gold_reg.append("REGULATOR_CONTACT")
            if "Better Business Bureau" in esc_txt or "attorney" in esc_txt:
                gold_reg.append("LEGAL_MEDIA_THREAT")
            gold_reg = sorted(set(gold_reg))

            # A quarter of records carry an explicit routing number, which
            # exercises the ABA validator.
            if idx % 4 == 0:
                body += f" My routing number is {routing_no} if you need it."

            subject_map = {
                "email": f"Complaint about {arch_id.replace('_', ' ')}",
                "web_form": "",
                "call_note": "",
            }
            if channel == "email":
                text = build_email(title_name, subject_map[channel], body, email, phone, addr)
            elif channel == "web_form":
                text = build_form(name, body, email, phone,
                                  random.choice(["Checking", "Savings", "Credit Card", "Mortgage"]), branch)
            else:
                text = build_note(title_name, body, phone, f"R{random.randint(100,999)}")

            received = base + timedelta(hours=idx * 3, minutes=random.randint(0, 59))
            pii_values = [acct, phone, email]
            if "{SSN}" in body_t:
                pii_values.append(ssn)
            if channel == "email":
                pii_values.append(addr)
            if idx % 4 == 0:
                pii_values.append(routing_no)
            if "{CARD4}" not in body_t and channel != "call_note":
                pass

            records.append({
                "complaint_id": f"SYN-{idx:04d}",
                "archetype": arch_id,
                "channel": channel,
                "received_at": received.strftime("%Y-%m-%dT%H:%M:%S"),
                "subject": subject_map[channel],
                "text": text,
                "metadata": {
                    "prior_contacts": prior,
                    "days_open": random.choice([1, 3, 6, 9, 16, 24, 45]),
                    "tenure_years": tenure,
                    "product_count": random.randint(1, 4),
                    "relationship_flag": idx % 37 == 0,
                    "branch": branch,
                },
                "gold": {
                    "regulatory": gold_reg,
                    "department": gold_dept,
                    "severity_floor": floor,
                    "quiet_regulatory": quiet,
                    "pii_values": pii_values,
                    "vulnerability_hint": (3 if vuln_txt in (_RENT, _MEDS) else
                                           2 if vuln_txt in (_FIXED, _RETIRED) else
                                           1 if vuln_txt == _MILD else 0),
                },
            })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")

    chan = {}
    for r in records:
        chan[r["channel"]] = chan.get(r["channel"], 0) + 1
    reg = sum(1 for r in records if r["gold"]["regulatory"])
    quiet = sum(1 for r in records if r["gold"]["quiet_regulatory"])
    print(f"wrote {len(records)} complaints -> {OUT.relative_to(ROOT)}")
    print(f"  channels: {chan}")
    print(f"  regulatory (gold): {reg}  of which quiet/no-trigger-vocabulary: {quiet}")
    print(f"  severity floors labelled: {sum(1 for r in records if r['gold']['severity_floor'])}")
    print(f"  distinct archetypes: {len({r['archetype'] for r in records})}")


if __name__ == "__main__":
    main()
