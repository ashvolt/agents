# L0 — Raw API

Ten minutes of reading. Then write [exercise.py](exercise.py).

**Exit test:** you can estimate what a feature costs before you build it.

---

## Why this level exists

Every agent, however elaborate, is a loop around one HTTP call to `POST /v1/messages`.
Tool use, streaming, structured output, multi-agent orchestration — all of it is that one
endpoint with different parameters. Start anywhere higher and you will spend later levels
debugging things you never learned the shape of.

## The four things to understand

### 1. A request is tools, then system, then messages

Three parts, rendered in that order:

- `system` — a **top-level parameter**, not a message with `role: "system"`. This trips up
  everyone arriving from other APIs. Getting it wrong means your instructions silently
  become a user turn.
- `messages` — the conversation, alternating user and assistant.
- `max_tokens` — **required**. A hard ceiling the model does not know about, so hitting it
  truncates output mid-sentence. Don't lowball it: roughly 16K is a sane default for
  non-streaming work, 64K when streaming. Go low only deliberately — classification,
  a cost cap, or this exercise.

### 2. A response is a list of blocks, not a string

`response.content` is a list. Today it holds text blocks; from L1 onward it will hold
`tool_use` blocks too. Code that does `response.content[0].text` works right up until the
model thinks before answering, and then it breaks.

### 3. `stop_reason` is the control flow

| value | meaning |
|---|---|
| `end_turn` | finished normally |
| `max_tokens` | ran into your ceiling — output is truncated |
| `tool_use` | wants a tool result before continuing (this is the whole of L1) |
| `refusal` | safety classifier declined |

`stop_details` is populated **only** on a refusal and is `None` for every other reason.
Reading `.category` without a guard is a real production crash, which is why a test here
checks for it.

### 4. `usage` is the bill

`response.usage` carries `input_tokens` and `output_tokens`, and later
`cache_read_input_tokens` and `cache_creation_input_tokens`. Pricing is per million tokens,
and output costs roughly five times input on every Claude model — which is why "make the
model more verbose" is an expensive instruction.

To count tokens *before* sending, use the token-counting endpoint. Not `tiktoken`, which is
a different tokenizer and gives a wrong answer, and not character count, which gives a
wronger one.

## Cost, concretely

The capstone's eval sweep from [PLAN.md](../../PLAN.md) section 8 is 200 cases at roughly
20K in and 2K out. Being able to work out what that costs on each model, in your head,
before committing to it, is the skill this level is actually teaching.

## Reference

- Messages API: https://docs.claude.com/en/api/messages
- Pricing: https://claude.com/pricing#api
- Token counting: https://docs.claude.com/en/docs/build-with-claude/token-counting

Look the pricing up. Do not fill `MODEL_PRICING` from memory — yours or mine.

---

## Doing the work

```bash
pytest levels/L0_raw_api -m "not integration"    # free, run constantly
pytest levels/L0_raw_api                         # hits the API, costs cents
```

Start with `MODEL_PRICING` and `estimate_cost`, then `summarize_stop`. Those are pure
functions and the whole first group of tests goes green without spending a penny. Only
then touch the network.

When the free tests pass, write [NOTES.md](NOTES.md) before moving on.
