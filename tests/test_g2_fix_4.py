"""G2 fix build 4 (G2 fix design 4, D-110 to D-113): the foreign-law pre-check refusal and the
`norm/7` rule-check section lists.

Questions written here are the design session's probes (written for design 4; batch 1 in-sample,
batch 2 written after prototype 3, batch 3 after prototype 4, sections 2.4) or the build session's
own words; tuning, dev and test questions are named by id only, and test-split questions are counted,
never shown. eval/guardrails/adversarial_v4.jsonl is never opened (a fixture fails any test that
tries). No database, key or model call is needed."""
import builtins
import importlib.util
import json
import pathlib
import sys
import threading

import pytest

from generation import generate as gen, guardrails, llm, note_tagger, verify
from retrieval.retrieve import Retrieved

ROOT = gen.ROOT
STORE = gen.FileCorpusStore()
CORPUS = STORE.sections()
POLICY = guardrails.load_guardrails_policy()
RULES = POLICY.foreign_law
POLICY_PATH = ROOT / "data" / "curated" / "guardrails_policy.yml"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return sys.modules[name]


run_adversarial = load_module("run_adversarial", ROOT / "eval" / "guardrails" / "run_adversarial.py")
check_foreign_law = load_module("check_foreign_law", ROOT / "eval" / "guardrails" / "check_foreign_law.py")


@pytest.fixture(autouse=True)
def never_open_v4(monkeypatch):
    """D-105, D-106: the build session never opens the v4 questions file. Any open of it fails the test."""
    real_open, real_path_open = builtins.open, pathlib.Path.open

    def guard(path):
        if "adversarial_v4" in str(path):
            raise AssertionError("a test tried to open the adversarial_v4 questions file")

    def checked_open(file, *args, **kwargs):
        guard(file)
        return real_open(file, *args, **kwargs)

    def checked_path_open(self, *args, **kwargs):
        guard(self)
        return real_path_open(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", checked_open)
    monkeypatch.setattr(pathlib.Path, "open", checked_path_open)


def fires(question):
    return guardrails.foreign_law_check(question, RULES).fired


def check(question):
    return guardrails.pre_check(question, POLICY, CORPUS)


# The policy block (D-110, D-107 ruling) ---------------------------------------------------------

REFUSAL_TEXT = ("This tool answers only from Nigerian federal tax law: the Nigeria Tax Act 2025 and selected parts "
                "of the Nigeria Tax Administration Act 2025. The question asks about the tax law of another country, "
                "which is outside its corpus. If the question is about how Nigerian tax applies to a business or "
                "transaction abroad, please say so.")


def test_refusal_text_is_pinned():
    assert RULES is not None and RULES.refusal_text == REFUSAL_TEXT


def test_list_sizes_are_those_measured():
    import yaml

    block = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))["foreign_law"]
    sizes = {k: len(v) for k, v in block.items() if isinstance(v, list)}
    assert sizes == {"countries": 183, "countries_cased": 22, "demonyms": 105, "cities": 101, "authorities": 6,
                     "taxpayers": 31, "located_taxpayers": 26, "taxing_verbs": 24, "located_verbs": 8,
                     "there_verbs": 9, "counterparty_words": 24, "first_person": 3, "hooks": 33}


def test_a_policy_without_the_block_turns_the_check_off(tmp_path):
    text = POLICY_PATH.read_text(encoding="utf-8")
    path = tmp_path / "policy.yml"
    path.write_text(text[:text.index("\nforeign_law:")] + "\n", encoding="utf-8")
    policy = guardrails.load_guardrails_policy(path)
    assert policy.foreign_law is None
    result = guardrails.pre_check("What is the VAT rate in Kenya?", policy, CORPUS)
    assert result.refusal is None and result.foreign_law is None


# Measured on every visible question (design section 2.4) ----------------------------------------

FIRED_IDS = {"ADV-025", "ADV-026", "ADV-027", "ADV-028", "ADV-029", "ADV-030", "ADV-031", "ADV2-013", "ADV2-014",
             "ADV2-015", "ADV3-013", "ADV3-014", "ADV3-015", "G01"}


def test_fires_on_exactly_the_measured_questions():
    m = check_foreign_law.measure()
    assert m["visible"] == 305 and set(m["fired_ids"]) == FIRED_IDS and len(m["fired_ids"]) == 14
    assert m["fired_expected"] == ["refuse"]
    assert (m["expected_answer"], m["expected_answer_fired"]) == (158, [])
    assert (m["in_scope"], m["in_scope_fired"]) == (54, [])
    assert m["test"] == {"questions": 30, "foreign_place": 0, "fired": 0}  # counts only (D-29)


def test_fired_visible_questions_are_refused_by_the_pre_check_and_nothing_else():
    rows = {r["id"]: r["question"] for r in check_foreign_law.visible_questions()}
    for id_ in FIRED_IDS:
        result = check(rows[id_])
        assert (result.refusal, result.refusal_text) == ("foreign_law", REFUSAL_TEXT), id_
        assert result.foreign_law.fired and not result.foreign_law.hooks, id_
    for id_, question in rows.items():
        if id_ not in FIRED_IDS:
            assert check(question).refusal != "foreign_law", id_


# Probe batches (design section 2.4; prototype 4 with the narrowed S2) ---------------------------

BATCH1_FIRE = [
    'What is the VAT rate in Kenya?',
    'How much corporate tax does a company pay in France?',
    'What are the transfer pricing rules in India?',
    'When are company tax returns due in South Africa?',
    'Is there VAT on food in the United Kingdom?',
    'What does SARS charge for late filing?',
    'Does Ghana tax capital gains on shares?',
    'Under Kenyan law, how long must a company keep its books?',
    "What is Senegal's withholding tax rate on interest?",
    'How does HMRC treat late VAT payments?',
    'My brother runs a bakery in Lomé. Does he need to register for VAT?',
    'What penalty does the Ghana Revenue Authority impose for late returns?',
    'What is the corporate income tax rate for small companies in Egypt?',
    'How are dividends taxed in Ghana?',
    'What is the standard VAT rate in the EU?',
    'Is there a digital services tax in France?',
    'What withholding tax applies to interest paid by a bank in Kenya?',
    'How long do companies in Ghana have to keep their accounting records?',
    'Do small businesses in Uganda pay VAT?',
    'What is the penalty for late VAT registration in Rwanda?',
    "Can a company in Côte d'Ivoire deduct entertainment expenses?",
    'What VAT does a supermarket in Accra charge?',
    'In Ghana, what is the VAT registration threshold?',
    'Is the Kenyan VAT rate 16%?',
    'How is a company in America taxed on its profits?',
    'What is the tax on dividends in Germany?',
    'My friend owns a restaurant in Dakar. When must she file her tax return?',
    'We are setting up a company in Ghana. What tax will it pay there?',
    'What is the corporate tax rate in Dubai?',
    "Does the UK charge VAT on children's clothes?",
    'How much is company tax in Togo for a new business?',
    'What are the filing deadlines for a limited company in Ireland?',
]
BATCH1_NOT = [
    'A company in the UK sells us software subscriptions. Do we have to account for VAT on them?',
    'Our parent company in France charges us a management fee. Is VAT due on it?',
    'We pay a consultant in Kenya for remote IT support. Is that service taxable for VAT?',
    'A buyer in Ghana pays us in dollars for cashew we send. Is the sale zero-rated?',
    'We sell handbags to customers in Togo through our website. Do we charge VAT?',
    'Our shareholder lives in London. Is withholding tax due on the dividend we pay her?',
    "The company's directors are based in Dubai. Where is the company resident for tax?",
    'Is a company registered in South Africa taxed on profits from a branch in Lagos?',
    'A US company sends staff to install equipment for us. Does that create a fixed base?',
    'We hold our board meetings in Accra. Does that make the company resident outside the country?',
    'Our supplier in China invoices us in dollars. Which exchange rate do we use for VAT?',
    'A tech firm in Germany streams videos to our customers. Must it register for VAT?',
    'How is a dividend from our subsidiary in Ghana taxed?',
    'Do we pay VAT on legal services bought from a law firm in London?',
    'A company in Benin Republic delivers goods to our warehouse. Who pays the VAT?',
    'What tax applies when a company in Ghana pays royalties to us?',
    'My company hired a designer in Kenya. Is withholding tax due on the fee?',
    'A small company sells crafts to tourists from France. Is VAT charged?',
    'Is VAT charged on an airline ticket from Abuja to London?',
    'A Chinese company is building a factory for a client. How is its profit taxed?',
    'A Ghanaian trader sells fabrics in Kano market. Must she register for VAT?',
    'What is the VAT on hotel rooms booked by guests from Cameroon?',
    'Our company pays rent for an office in London. Is the rent deductible?',
    'Can we claim input VAT on goods bought in Dubai?',
    'Does a company resident in the UK pay tax on profits earned from a contract here?',
    'A business in Ghana provides training to our staff online. Is VAT due?',
    'How long must a company keep its records if its auditors are in London?',
    "Is a company with a branch in Ghana taxed on the branch's profits?",
    'Is income a company earns in Ghana taxable?',
    'Do companies in Ghana that sell to us need a Tax ID?',
    'The shop is owned by an American. What rate of company income tax applies?',
    'How is VAT charged on goods a shop in Accra buys from us?',
    'What is the VAT rate on goods sold to a customer in Ghana?',
    'We paid income tax in Ghana on our branch profits. Can we claim a credit for it?',
    'Our UK client asks us to charge VAT. Do we charge VAT on consulting for them?',
    'We bought a machine from a German supplier. Can we claim capital allowances on it?',
    'We are a Lebanese-owned trading company. What company income tax rate do we pay?',
    'Our Indian parent lends us money. Is the interest we pay it deductible?',
    'A Kenyan company has a fixed base in our city. Is it taxable on its profits?',
    'We have a warehouse in Togo for goods we sell to customers there. Do we charge VAT on those sales?',
    'Our staff travel to Ghana for a project. Is the per diem part of our deductible costs?',
    'A firm in Ghana does our bookkeeping remotely. Do we have to deal with VAT on its fees?',
]
BATCH2_FIRE = [
    'How much VAT does a restaurant in Johannesburg add to a bill?',
    'What is the capital gains tax rate on shares sold in Canada?',
    'My aunt has a hair salon in Douala. Does she pay a monthly business tax?',
    'Which taxes does a start-up in Singapore pay in its first year?',
    'Is GST charged on software sold in Australia?',
    'What happens if a business files its VAT return late with the Uganda Revenue Authority?',
    'Is there a minimum tax on companies in Morocco?',
    'Do Egyptian companies pay tax on dividends from their subsidiaries?',
    'How long must records be kept under German tax law?',
    "What is Ghana's corporate tax rate for mining companies?",
    'Can an Irish company carry forward its trading losses?',
    'In Kenya, are bank charges subject to excise duty?',
    'Does South Africa tax foreign dividends received by its companies?',
    'What VAT threshold applies to small traders in Tanzania?',
    'How does a company in Zambia pay its provisional tax?',
    'What is the withholding tax rate on rent in Rwanda?',
    'Under Senegalese law, when is the annual tax return due?',
    'Is there a sugar tax in the United Kingdom?',
    "A friend's pharmacy in Cotonou sells medicines. Are they exempt from VAT there?",
    'What rate of company tax do small businesses pay in Botswana?',
]
BATCH2_NOT = [
    'We sell spare parts to a garage in Accra and ship them by road. Is the sale zero-rated?',
    'Our director lives in Canada and attends board meetings by video. Are his fees taxable?',
    'A company in Ghana sends us an invoice for consulting. Do we self-account for VAT?',
    'Our sister company in Togo pays us a royalty. How is it taxed?',
    'We booked a hotel in Kigali for a staff retreat. Can we claim the cost?',
    'A British investor bought shares in our company. Do we deduct tax from her dividends?',
    'Our accounting software is from a firm in India. Is VAT due on the licence fee?',
    'We exhibit at a trade fair in Dubai. Is the stand fee an allowable expense?',
    'Is a company that is managed from London but registered locally resident for tax?',
    'Our supplier in Benin Republic delivers rice to our shop. Is rice exempt from VAT?',
    'My company buys raw cocoa from farmers in Cameroon. Is VAT charged on the purchase?',
    'How should we treat VAT on a training course a Kenyan firm runs for our staff?',
    'We pay an agent in Senegal a commission on sales. Is withholding tax due on it?',
    'What records must we keep for services we buy from a company in Germany?',
    'A Chinese contractor is building our factory. Must we withhold tax from its payments?',
    'Our parent in South Africa lent us money. Is the interest we pay deductible?',
    'I sell clothes online to buyers in Ghana. Do I charge VAT on those sales?',
    'We rent a warehouse from a company in Lagos owned by a French group. Is the rent subject to VAT?',
    'A company in the US provides cloud storage to our firm. How do we account for VAT?',
    'Our branch in Ghana made a loss. Can we set it against our profits?',
]
BATCH3_FIRE = [
    'What is the VAT charged on electricity bills in Ghana?',
    "How much tax does a freelancer's company pay on profits in Spain?",
    'When do companies in the Netherlands file their annual returns?',
    'Is there an excise tax on soft drinks sold in Kenya?',
    'What is the tax rate on interest earned in Switzerland?',
    'How is VAT calculated on imported cars in Uganda?',
    'What is the stamp duty on property transfers in Ghana?',
    'Does Egypt charge VAT on medical services?',
    'Is income tax payable on lottery winnings in South Africa?',
    'What penalty applies for late tax payment by a shop in Lusaka?',
    'Under Ugandan rules, how often is VAT filed?',
    'What deductions are allowed for companies trading in Malaysia?',
    'How much VAT do supermarkets in Abidjan charge on rice?',
    'What is the company tax rate for start-ups in Rwanda?',
    'Are digital services taxed in India?',
]
BATCH3_NOT = [
    'Is VAT charged on consulting services supplied to a client in Ghana?',
    'A Chinese company is building a factory for a client. How is its profit taxed?',
    'How is VAT handled on goods sent by road to a buyer in Niger Republic?',
    'Does a company pay tax on interest it earns on a deposit with a bank in London?',
    'A company sells cocoa to a chocolate maker in Belgium. Is the sale taxable for VAT?',
    'What VAT applies to an airline selling tickets for flights to Accra?',
    'Is a dividend paid to a shareholder in France subject to withholding tax?',
    'A company pays a licence fee to a software house in Ireland. Is withholding tax due?',
    'Can a company claim capital allowances on a truck bought from a dealer in Japan?',
    'Is an entertainment expense for clients visiting from Kenya deductible?',
    'How is VAT charged when a company hires a marketing agency in South Africa?',
    "A company's head office is in London and it trades through a branch in Kano. Is the branch taxable?",
    'Does the tax on royalties paid to a company in the US differ from the rate for local companies?',
    'Is a company taxed on profit from a contract performed in Ghana for a Lagos client?',
    'What withholding applies to fees paid to a consultant in Germany for a feasibility study?',
]
MISSES = {
    "What are the filing deadlines for a limited company in Ireland?",
    "Do Egyptian companies pay tax on dividends from their subsidiaries?",
    "Can an Irish company carry forward its trading losses?",
    "Does South Africa tax foreign dividends received by its companies?",
    "What VAT threshold applies to small traders in Tanzania?",
    "How much tax does a freelancer's company pay on profits in Spain?",
    "How is VAT calculated on imported cars in Uganda?",
}


@pytest.mark.parametrize("batch,expected", [(BATCH1_FIRE, 31), (BATCH2_FIRE, 16), (BATCH3_FIRE, 13)])
def test_should_fire_probes(batch, expected):
    assert sum(fires(q) for q in batch) == expected
    assert {q for q in batch if not fires(q)} <= MISSES


@pytest.mark.parametrize("question", BATCH1_NOT + BATCH2_NOT + BATCH3_NOT)
def test_in_scope_probes_never_fire(question):
    assert not fires(question)


def test_probe_counts():
    assert (len(BATCH1_FIRE), len(BATCH1_NOT), len(BATCH2_FIRE), len(BATCH2_NOT), len(BATCH3_FIRE),
            len(BATCH3_NOT)) == (32, 42, 20, 20, 15, 15)


# The forms, hooks and guards (design section 2.3) -----------------------------------------------

@pytest.mark.parametrize("question,form", [
    ("Under Ghanaian law, what is the VAT rate on bread?", "S1"),
    ("What does the Uganda Revenue Authority charge for a late return?", "S2"),
    ("How does HMRC treat late VAT payments?", "S2"),
    ("Does Kenya tax capital gains on land?", "S3"),
    ("What is the VAT rate in Togo?", "S4"),
    ("What is the tax rate for small firms in Egypt?", "S5"),
    ("How are profits taxed in Senegal?", "S6"),
    ("In Rwanda, is VAT charged on rent?", "S7"),
])
def test_strong_forms(question, form):
    result = guardrails.foreign_law_check(question, RULES)
    assert result.fired and form in [name for name, _ in result.strong]


def test_s8_needs_a_foreign_place():
    assert "S8" in [n for n, _ in guardrails.foreign_law_check("We set up a shop in Accra. What tax do we pay there?",
                                                               RULES).strong]
    assert not guardrails.foreign_law_check("We hired a hall. Is VAT due there?", RULES).strong


@pytest.mark.parametrize("question", [
    # Narrowed S2 (build deviation, approved by Kene): ordinary Nigerian wording for a tax authority
    "Does the state revenue authority collect VAT on hotel rooms?",
    "Which federal tax authority handles company income tax?",
    "Do we file our VAT returns with the State IRS or the federal one?",
    "What penalty can the revenue authority charge for late filing?",
    "The Revenue Authority sent us a notice. How long do we have to object?",
])
def test_nigerian_authority_wording_never_fires(question):
    assert not fires(question)


def test_weak_forms_need_no_first_person_word():
    assert fires("How often must a shop in Kenya file VAT returns?")
    assert not fires("A shop in Kenya buys our goods. How often must we file VAT returns?")
    assert fires("My cousin runs a shop in Kenya. How often must she file VAT returns?")  # "my cousin" is not a business


def test_a_counterparty_word_blocks_w1_and_w2():
    assert not fires("Is VAT charged on consulting supplied to a client in Ghana?")
    assert not fires("Are services bought from a firm in Ghana subject to VAT?")


@pytest.mark.parametrize("question", [
    "Is Nigeria's VAT rate higher than the VAT rate in Ghana?",
    "Under section 31, how long must a company in Ghana keep records?",
    "Must a company in the UK that sells to customers here register for VAT?",
    "We import cloth; what is the VAT rate in Ghana on it?",
    "Can we claim a credit for income tax paid in Ghana?",
    "Does the NTA tax a company in Ghana?",
    "Is a company in Ghana with a branch in Lagos taxable?",
])
def test_hooks_block_every_form(question):
    result = guardrails.foreign_law_check(question, RULES)
    assert result.hooks and not result.fired


@pytest.mark.parametrize("text,foreign", [
    ("guinea fowl", False), ("Guinea", True), ("Benin City", False), ("Benin Republic", True),
    ("Niger State", False), ("Niger Delta", False), ("Niger Republic", True), ("chad", False), ("Chad", True),
    ("turkey", False), ("Turkey", True), ("Lagos", False), ("Abuja", False),
])
def test_cased_names_and_guards(text, foreign):
    assert bool(RULES.foreign_place.search(text)) is foreign


def test_dev_examples_of_design_section_4_2_never_fire():
    rows = {q["id"]: q["question"] for q in run_adversarial.run_eval.load_questions("dev")}
    assert not fires(rows["Q015"])  # a foreign online company supplying software services
    assert not fires("A Ghanaian company sells goods into Nigeria. Does it charge VAT?")


# Pre-check and generate() (design section 2.6) ---------------------------------------------------

def test_pre_check_keeps_notes_and_logs_the_check():
    result = check("Does Rwanda give tax holidays to new firms?")
    assert result.refusal == "foreign_law"
    assert [n.ref for n in result.notes] == ["scope:economic_development_incentives"]
    log = result.as_log()
    assert log["refusal"] == "foreign_law" and log["foreign_law"]["fired"] is True
    assert log["foreign_law"]["strong"] == [["S3", "Does Rwanda give"]] and log["labels"] == [
        "scope:economic_development_incentives"]  # foreign_law adds no note label (D-51, D-57)


def test_a_question_that_does_not_fire_is_logged_unfired():
    result = check("Is the development levy charged on a small company?")
    assert result.refusal is None and result.as_log()["foreign_law"]["fired"] is False


def test_a_reference_refusal_comes_first_and_skips_the_check():
    result = check("What does section 250 of the Nigeria Tax Act say about VAT in Ghana?")
    assert result.refusal == "no_such_section" and result.foreign_law is None


class CountingRetriever:
    def __init__(self):
        self.calls = 0

    def retrieve(self, question, k=5, filters=None, query_vector=None):
        self.calls += 1
        return [Retrieved("nta2025:s56", 1, 0.9, 0.1, ["vector"])]


class NoCallLLM:
    model = llm.MODEL

    def __init__(self):
        self.requests = []

    def create(self, params):
        self.requests.append(params)
        raise AssertionError("no model call expected")


class FakeTagger:
    model = note_tagger.TAGGER_MODEL

    def __init__(self, labels=()):
        self.labels, self.calls = list(labels), 0
        self._lock = threading.Lock()

    def create(self, params):
        with self._lock:
            self.calls += 1
        usage = {"input_tokens": 900, "output_tokens": 60, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 0}
        text = json.dumps({"reasoning": "fake", "labels": self.labels})
        return llm.LLMResponse([{"type": "text", "text": text}], "end_turn", None, usage, "req_tag", self.model, 7)


def test_generate_refuses_before_retrieval_and_any_model_call(tmp_path):
    retriever, model, verifier, tagger = CountingRetriever(), NoCallLLM(), NoCallLLM(), FakeTagger(["surcharge"])
    answer = gen.generate("What is the VAT rate in Kenya?", retriever=retriever, llm=model, verifier=verifier,
                          tagger=tagger, store=STORE, log_dir=tmp_path, guardrails_policy=POLICY)
    assert (answer.status, answer.refusal_reason) == ("refused", "foreign_law")
    assert retriever.calls == 0 and model.requests == [] and verifier.requests == []
    assert tagger.calls == 3 and "scope:surcharge" in [n.ref for n in answer.notes]  # the tagger still runs (D-44)
    assert answer.rendered.startswith(REFUSAL_TEXT + "\n\nNotes\n") and answer.rendered.endswith(gen.DISCLAIMER)
    record = json.loads(next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8"))
    assert record["refusal"] == {"status": "refused", "reason": "foreign_law", "stage": "pre_check"}
    assert record["retrieval"] is None and record["guardrails"]["pre_check"]["foreign_law"]["fired"] is True


# norm/7: the rule-check section lists (D-111; design section 3.1) -------------------------------

def sections(text):
    return sorted(t for t in verify.extract_tokens(text) if t.startswith("sec:"))


def test_norm7_version():
    assert verify.NORMALISER_VERSION == "norm/7"


@pytest.mark.parametrize("text,expected", [
    ("sections 163(1)(p), and 187", ["sec:163", "sec:187"]),
    ("sections 1, 2, and 3(3)", ["sec:1", "sec:2", "sec:3"]),
    ("section 20, or 21", ["sec:20", "sec:21"]),
    ("subject to section 67, 10% of the tax", ["sec:67"]),
    ("section 148, 7.5%", ["sec:148"]),
    ("section 56, 30 per cent", ["sec:56"]),
    ("section 56, 30 percent", ["sec:56"]),
    ("sections 20 and 21", ["sec:20", "sec:21"]),
    ("sections 20, 21 and 22", ["sec:20", "sec:21", "sec:22"]),
    ("sections 27 to 29", ["sec:27", "sec:29"]),
])
def test_norm7_section_lists(text, expected):
    assert sections(text) == expected


def test_norm7_recovers_the_schedule_heading_claim():
    chunk = STORE.chunks(["nta2025:sch13:table1"])["nta2025:sch13:table1"]
    assert "sec:187" in verify.source_tokens(chunk)
    check_ = verify.rule_check("The Thirteenth Schedule is headed as relating to sections 163(1)(p) and 187.", [chunk])
    assert check_.passed and check_.missing == []


def test_norm7_percent_claim_passes_against_section_65():
    chunk = STORE.chunks(["ntaa2025:s65"])["ntaa2025:s65"]
    claim = ("Where any tax is not paid within the period prescribed under the Act, and subject to section 67, 10% "
             "of the tax payable is added to it.")
    assert verify.rule_check(claim, [chunk]).passed


def test_norm7_still_removes_a_section_no_source_names():
    chunk = STORE.chunks(["ntaa2025:s65"])["ntaa2025:s65"]
    result = verify.rule_check("Section 65 is subject to sections 67, and 99.", [chunk])
    assert not result.passed and result.missing == ["sec:99"]
