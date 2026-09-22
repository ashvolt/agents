# L0 notes

Written after the tests pass, in my own words. Not copied from the README.

## What I got wrong first

<!-- The bug, what I expected, what actually happened. Be specific. -->
Trying to figure out the cost calculation, like the cost for the input and output per million requests, understanding the venv.

## The thing I would not have guessed
The control system, with stop details.
<!-- -->

## Cost, from memory
 The method which computed by my own estimate_cost

opus-5    5 / 25    → 5×
sonnet-5  2 / 10    → 5×
haiku-4-5 1 /  5    → 5×
So the  output_price = 5 × input_price:


cost = 4.0 × I  +  0.4 × (5 × I)
     = 4.0 × I  +  2.0 × I
     = 6 × I
One sweep costs 6× the model's input price per MTok. T.



<!--
Without looking it up:
- One eval sweep, 200 cases, 20K in / 2K out per case, on Opus 5: $
- Same sweep on Haiku 4.5: $
- Roughly how many times more expensive is an output token than an input token?

Then check. Record the gap between your guess and the truth — that gap is the point.
-->

## Still unclear

<!-- Carry these into L1 rather than pretending they are resolved. -->
Understading the image token countring, like how caching actually helps , growing toolloop , what is the cost per turn