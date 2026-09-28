from tools.decision_synthesizer import run_synthesizer
from orchestrator import PriceLensState
import traceback
import json

with open("tests/eval_dataset.json") as f:
    eval_dataset = json.load(f)

for tc in eval_dataset:
    state = PriceLensState(**tc["input_state"], draft_verdict={}, final_verdict={}, errors=[])
    try:
        print(tc["test_case_id"], "->", run_synthesizer(state)["decision"])
    except Exception as e:
        print(tc["test_case_id"], "-> EXCEPTION")
        traceback.print_exc()
