"""Open the local log viewer on the fault-injection schema `audit_faults` (Phase 9 step 8, part 4).

    .venv\\Scripts\\streamlit run eval\\interface\\view_faults.py

`app/review.py` reads schema `audit` unless its session holds another `ReviewContext`; this launcher sets
one for `audit_faults` and runs the viewer unchanged. It only reads.
"""
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT / "python"), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import review  # noqa: E402

if "review" not in st.session_state:
    st.session_state["review"] = review.ReviewContext(schema="audit_faults")
review.main()
