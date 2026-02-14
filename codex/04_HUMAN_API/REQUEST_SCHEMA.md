# Human Request Schema (strict)

HUMAN_REQUEST:
TYPE: {design_direction | decision | external_action | clarification | review}
URGENCY: {low | medium | high | interrupt_now}
TIME_REQUIRED_MIN: <int>
CONTEXT: <short>
OPTIONS:
  - <option A>
  - <option B>
RECOMMENDATION: <one of options or custom>
WHY: <1-3 bullets>
UNBLOCKS: <what changes after you answer>
REPLY_FORMAT: <exact expected reply>
