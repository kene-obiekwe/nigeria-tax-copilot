"""The app service layer (Phase 9; interface design, Sections 2, 3.6, 3.8, 7, 8 and 9).

- `screen`: the personal-data screen, a hard block before anything is stored or sent (D-160).
- `limits`: the request context, the limits and the gates (`LocalGate`, `TierGate`; D-162).
- `identity`: the identity abstraction and the HMAC user key (D-163).
- `startup`: the start-up check that the app runs the shipped configuration (D-154).
- `service`: one question from screen to logged answer: receipt, worker, temp-dir record, close,
  deliveries (D-150, D-153, D-157).

The Streamlit pages (Build B) call only `service`; nothing here imports Streamlit.
"""
