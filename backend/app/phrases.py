"""What Kahie SAYS — short, everyday spoken Malagasy, as heard in Tana markets.

Two variants per state: [0] direct (first time), [1] softer (when Kahie has to ask again).
Numbers stay as digits here; numbers_mg.speakify() turns them into words for the speech
synthesiser ("2000 Ar" → "roa arivo Ariary"). The same sentences are shown on screen.
"""
from .agent.answers import ar

PHRASES = {
    "clarify_amount":   ["Firy marina: {a} sa {b}?",
                         "Azafady, {a} ve sa {b} no marina?"],
    "clarify_name":     ["Iza na inona marina: {a} sa {b}?",
                         "Azafady, {a} ve sa {b}?"],
    "clarify_customer": ["Iza no mitrosa?",
                         "Azafady, iza no mbola tsy nandoa ny ambiny?"],
    "clarify_product":  ["Inona no entana?",
                         "Azafady, inona ilay entana nalainy?"],
    "clarify_unknown_product": ["Tsy fantatro ny « {product} ». Inona marina?",
                                "Azafady, inona marina ilay « {product} »?"],
    "clarify_quantity": ["Firy ny {product}?",
                         "Azafady, firy ny {product} nalainy?"],
    "clarify_who_amount": ["Iza ary ohatrinona?",
                           "Azafady, iza no nandoa, ary ohatrinona?"],
    "not_heard":        ["Tsy re tsara. Avereno azafady.",
                         "Miala tsiny, tsy henoko tsara. Averinao ve?"],
    "not_said":         ["Tsy re ny « {missing} ». Avereno azafady.",
                         "Azafady, lazao mazava indray ny « {missing} »."],
    "confirm_tail":     ["Marina ve?",
                         "Ekenao ve?"],
    "yes_or_no":        ["Eny sa tsia?",
                         "Azafady, lazao hoe « eny » na « tsia »."],
    "cancelled":        ["Tsy voasoratra.",
                         "Eny ary, tsy soratako."],
    "listening":        ["Henoiko…",
                         "Lazao fa mihaino aho…"],
    # --- voice commands (hands-free) ---
    "hello":            ["Manao ahoana! Henoiko ianao.",
                         "Miverina aho. Henoiko ianao."],
    "cmd_dash":         ["Ity ny tabilao. Varotra androany: {sales}. Trosa rehetra: {debt}.",
                         "Tabilao: varotra androany {sales}, trosa rehetra {debt}."],
    "cmd_statement":    ["Tatitra herinandro: varotra {sales}, trosa naloa {repaid}. {seal}",
                         "Ity ny tatitra herinandro: varotra {sales}, trosa naloa {repaid}. {seal}"],
    "seal_ok":          ["Tsy nisy nokitihina ny diary.",
                         "Madio ny diary."],
    "seal_bad":         ["Tandremo: nisy nanova ny diary.",
                         "Tandremo: simba ny diary."],
    "cmd_home":         ["Vonona aho. Mitenena.",
                         "Henoiko ianao."],
    "cmd_help":         ["Azonao lazaina hoe: Rabe naka vary telo kapoaka, nandoa roa arivo. "
                         "Na: iza no manana trosa? Na: asehoy ny tabilao. Na: foano ilay farany. Na: ajanony.",
                         "Lazao fotsiny izay nolafoina, na manontania hoe: iza no manana trosa? "
                         "Raha hijanona aho: ajanony."],
    "cmd_stop":         ["Mijanona aho. Tsindrio ny mikrô raha hiresaka indray.",
                         "Veloma tompoko. Tsindrio ny mikrô raha hiresaka indray."],
    "cmd_undo_ask":     ["Hofoanana ve ilay farany: « {what} »?",
                         "Azafady, foanako ve ilay « {what} »? Eny sa tsia?"],
    "cmd_undo_none":    ["Tsy misy azo foanana.",
                         "Mbola tsy misy voasoratra azo foanana."],
    "undone":           ["Nofoanana.",
                         "Vita, nofoanana ilay izy."],
    "not_undone":       ["Tsy nofoanana.",
                         "Eny ary, tazomina ilay izy."],
}


def say(state: str, variant: int = 0, **values) -> str:
    options = PHRASES[state]
    return options[min(variant, len(options) - 1)].format(**values)


def success(lines: list[dict], variant: int = 0) -> str:
    """After the ✓: what was written, with the numbers. Read aloud.
    [0] "Voasoratra: varotra 3000 Ar, trosa 1000 Ar."   [1] "Tafiditra ao amin'ny kahie: …, ary 1000 Ar no mbola trosa." """
    sales = sum(ln["total"] for ln in lines if ln["kind"] == "sale")
    debt = sum(ln["amount"] for ln in lines if ln["kind"] == "debt")
    paid = [(ln["customer"], ln["amount"]) for ln in lines if ln["kind"] == "payment"]
    stock = [ln for ln in lines if ln["kind"] == "stock"]
    parts = []
    if sales:
        parts.append(f"varotra {ar(sales)}")
    for who, amount in paid:
        parts.append(f"nandoa {ar(amount)} i {who}")
    if stock:
        parts.append("tahiry novaina")
    if variant == 0:
        if debt:
            parts.append(f"trosa {ar(debt)}")
        return "Voasoratra" + (": " + ", ".join(parts) if parts else "") + "."
    text = "Tafiditra ao amin'ny kahie" + (": " + ", ".join(parts) if parts else "")
    return text + (f", ary {ar(debt)} no mbola trosa." if debt else ".")
