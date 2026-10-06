"""Scene script for the product walkthrough.

One tuple per scene: (id, actions, narration). The narration is written for a mixed
audience — operations stakeholders and the technical team — and describes the product,
not the person who built it.

Actions are interpreted by record.py:
  ("card", "<card id>")                 navigate the card deck to that card
  ("app", "<path>")                     navigate the live app
  ("sample", "<file name>")             click a sample tile and wait for its verdict
  ("upload", "<file>", "<product>", w, h)  drive the real upload form
  ("zoom", "<css selector>", s)         animate a zoom onto that element
  ("unzoom",)                           ease back to 1.0
  ("scroll", <px>)                      smooth scroll by px
  ("point", "<css selector>")           move the on-screen cursor onto an element
  ("wait", <seconds>)                   hold

Actions run at the start of a scene; the recorder holds the frame for the rest of that
scene's narration. Roughly 180 words of narration costs a minute of video.
"""

SCENES = [
 ("title", [("card", "title"), ("wait", 0.6)],
  "This is Artwork Preflight: an automated print-readiness check for custom print orders. "
  "Every file a customer uploads gets measured against the product it was actually ordered on, in about a second, and "
  "comes back as one of three outcomes — cleared for production, returned to the customer with an explanation, or handed "
  "to a person with the findings already attached. "
  "Everything in this walkthrough is the running system, checked live."),

 ("problem", [("card", "problem"), ("wait", 0.4)],
  "The problem it solves sits here, in the middle of the pipeline. "
  "Before anything reaches a press, a person confirms the file will actually print: enough resolution at the size ordered, "
  "bleed where the blade cuts, the right colour mode, no text small enough to turn to mud, nothing important sitting in "
  "the trim margin. "
  "That review protects the print and scales one for one with orders, which makes it the largest variable cost in the "
  "operation. And most files are already fine, so most of that time confirms nothing is wrong — on the assumptions "
  "shown, thirty-three reviewer hours a day, around three hundred and forty thousand dollars a year. "
  "The aim is not to replace the reviewer. It is to stop the clean files reaching them, and to make the broken ones "
  "arrive already diagnosed."),

 ("metric", [("card", "metric"), ("wait", 0.4)],
  "Which sets an unusual target, because the two ways of being wrong cost completely different amounts. "
  "A file wrongly cleared becomes a bad print: a reprint, a reship, a support ticket, and a customer who stops trusting "
  "the proof. Around eighteen dollars, every time. At a one percent rate that eats over half the saving; at five percent "
  "the system destroys more value than it creates. "
  "So the product is tuned to clear as many files as it can under a hard ceiling on wrong clearances, and the release "
  "gate fails any build that buys coverage by raising that ceiling. Quick to escalate, slow to approve."),

 ("app_home", [("app", "/"), ("wait", 1.2), ("scroll", 240)],
  "Here is the review front end. Two inputs: the artwork, and the order context — the product, and the size the customer "
  "ordered. "
  "That second input is the one people underestimate. Resolution is not a property of a file, it is a property of a file "
  "at a size, and bleed and safe zones come from the product, not the image. "
  "Below are real illustrations put through the things customers genuinely do to artwork: screenshots, chat apps, "
  "background removers, repeated JPEG saves, GIF tools."),

 ("approve", [("sample", "print_ready.tif"), ("wait", 0.8), ("zoom", "#result .verdict, #result", 1.35)],
  "Start with a file that is already correct: a print-ready export with bleed and the right colour mode. "
  "Nothing to report, cleared for production, and no person was involved. That is the outcome the whole system exists to "
  "produce, and the only one that is fully automatic. "
  "Note the two figures on that line: about a second, and zero cost per file. The check runs on ordinary CPU with no model "
  "call behind it."),

 ("notes", [("unzoom",), ("sample", "messaging_app.jpg"), ("wait", 1.0), ("zoom", "#result .issues, #result", 1.4)],
  "Now a file sent through a chat app. The checker reports something, but it still clears the file. "
  "Findings are graded, and this one is a note rather than a blocker: the artwork is in screen colour and gets converted "
  "for print, with a warning that very bright greens and blues may come out duller. An earlier version rejected files for "
  "that outright, which sends work back to a customer over something the press can handle. Only a finding that genuinely "
  "stops the print blocks the order."),

 ("fix", [("unzoom",), ("sample", "screenshot.png"), ("wait", 1.0)],
  "This one is different: a customer sent a screenshot instead of the original artwork, which is one of the most common "
  "things that arrives in a real queue. "
  "The file does not clear. And the useful part is not the verdict — it is what sits underneath it."),

 ("fix_zoom", [("zoom", "#result .issues, #result", 1.6), ("wait", 0.5)],
  "Every finding carries the measurement that produced it. A hundred and thirty-seven point nine dots per inch at the "
  "ordered five by three inches, against a hundred and fifty required for this product. The measured value, the "
  "threshold, and the reason it matters in print. "
  "That is a deliberate product rule: nothing is reported without evidence a person can verify in two seconds. A reviewer "
  "can accept or overrule the finding without re-measuring the file, and a customer gets a reason rather than a verdict."),

 ("fix_msg", [("unzoom",), ("scroll", 420), ("zoom", "#result .message, #result", 1.45)],
  "And this is where the time actually goes in a real queue: writing to the customer. The message is drafted from the same "
  "measurements, with the numbers carried through rather than retyped. "
  "A reviewer approves it in one click before it sends. The system does the measuring and the drafting; the person keeps "
  "the judgement and the final say over anything that reaches a customer."),

 ("overlay", [("unzoom",), ("scroll", -420), ("zoom", "#result .preview, #result", 1.5)],
  "The artwork comes back marked up as well. Red is the cut line, blue is the safe zone, and any problem region is boxed. "
  "This is for whoever opens the file next. When something is escalated, the reviewer starts from a diagnosis and a "
  "picture of where the problem is, instead of a blank screen and a file name."),

 ("transparency", [("unzoom",), ("scroll", 300), ("sample", "background_removed.png"), ("wait", 1.0),
                   ("zoom", "#result .issues, #result", 1.45)],
  "Here is artwork run through an automatic background remover — extremely common, and it leaves damage that file "
  "metadata cannot see. "
  "The checker flags transparency the customer almost certainly did not intend. That finding comes from reading the "
  "pixels: alpha analysis, stroke widths, contrast against the material. It costs compute rather than tokens, and it is "
  "still a measurement, not an opinion."),

 ("bleed", [("unzoom",), ("sample", "trim_size_export.png"), ("wait", 1.0), ("zoom", "#result .issues, #result", 1.45)],
  "This one was exported at exactly the finished size, with no bleed. It looks perfect on screen and prints with white "
  "slivers along the edge, because no cutter is accurate to the millimetre. "
  "The required bleed comes from the product spec, so the same file passes on one product and fails on another. Adding a "
  "new product is a row in that spec table, not new code."),

 ("escalate", [("unzoom",), ("sample", "gif_upload.gif"), ("wait", 1.0), ("zoom", "#result .verdict, #result", 1.4)],
  "And this is the third outcome. Someone uploaded an animated GIF — a file the checker cannot reliably measure. "
  "It does not guess, and it does not clear it. It escalates to a person and says why. "
  "That is the rule the whole design rests on: it may never silently clear a file. If anything is unsupported, "
  "contradictory, or simply came back empty, the file goes to a human. The system is allowed to be unhelpful. It is not "
  "allowed to be wrong in the expensive direction."),

 ("upload_size", [("unzoom",), ("upload", "print_ready.tif", "vinyl-banner", 24, 14), ("wait", 1.0),
                  ("zoom", "#result .issues, #result", 1.45)],
  "One more, to show why order context is an input rather than a detail. This is the same print-ready file that cleared at "
  "the start — the exact same pixels — now ordered as a twenty-four by fourteen inch vinyl banner. "
  "Three new findings. The bleed that was sufficient for a label is short of what a banner needs. Lines that printed "
  "cleanly at five inches are now under the minimum printable stroke width. And lettering that was comfortably readable is "
  "now below the minimum text size for the product. "
  "Nothing about the file changed — the requirement did. This is also the upload path, which runs exactly the same code as "
  "the samples."),

 ("buckets", [("card", "buckets"), ("wait", 0.5)],
  "So how does it decide? Checks fall into three kinds, and sorting them correctly is the core of the design. "
  "The first is arithmetic on file metadata: resolution against ordered size, bleed and aspect against the product spec, "
  "colour mode, whether the file decodes at all. Exact, instant, free. "
  "The second kind needs the pixels: hairlines too thin to print, elements that vanish against the material, unintended "
  "transparency, text below the minimum readable size. Measured with computer vision on CPU. "
  "The third kind is genuine judgement — whether ink inside the cut margin is a logo about to lose its top third or a "
  "background bleeding off the edge on purpose. The measurement is trivial; the question is not. "
  "An early version put five checks in that third group. Four were measurement problems in disguise, and moving them made "
  "the system faster, cheaper and more accurate at once."),

 ("verdicts", [("card", "verdicts"), ("wait", 0.5)],
  "Those checks produce the three outcomes you have seen, and only one of them is automatic. "
  "Approve goes straight to production, and only fires when nothing blocking came back and every check that ran is one the "
  "system is trusted to make alone. "
  "Request fix returns the file to the customer with a drafted message a reviewer approves first. "
  "Escalate puts it in front of a person with the findings attached. "
  "Two boundaries worth stating plainly: the system never edits artwork and never contacts a customer on its own, and "
  "uploaded files are treated strictly as data — text rendered inside an image is described, never followed as an "
  "instruction."),

 ("results", [("app", "/reports"), ("wait", 1.4), ("scroll", 420)],
  "Validation is published inside the product, not kept in a slide. This page is generated from the evaluation runs "
  "themselves. "
  "Each round was sealed before scoring, scored once, and reproduces from a manifest and a seed. On a thousand unseen real "
  "illustrations the system clears seventy-eight point seven percent with zero wrong clearances in five hundred; on four "
  "hundred and eighty files damaged by those customer processes, eighty-seven percent, again with none wrong. Earlier "
  "rounds, including the ones that went backwards, stay on the page."),

 ("evaluation", [("card", "evaluation"), ("wait", 0.5)],
  "Those runs also settled the product's biggest open question: how much of this needs an AI model at all. "
  "A control arm ran the same cases with the deterministic checks disabled, so the model's contribution could be measured "
  "instead of assumed. It changed one check out of ten, and reduced hand-offs by three and a half points. "
  "Letting the model choose its own tools made things worse, not better — removing that cut wrong clearances from "
  "thirty-three percent to eighteen, at well under half the cost, because it was second-guessing measurements it could "
  "simply be handed. "
  "And an early result that looked perfect on eighty-eight cases breached the ceiling on six hundred, which is why sample "
  "size and an upper bound are now reported beside every number. "
  "So the shipped decider is computer vision plus a small statistical model, with no model call at inference. Measurement "
  "chose that, not preference."),

 ("cost", [("app", "/reports#cost"), ("wait", 1.4), ("zoom", "table, main", 1.3)],
  "Which is why the running cost line reads zero. The shipped check uses CPU and finishes in about a second, so cost per "
  "file is a hosting cost, not a per-file one. "
  "The comparison is published beside it: a vision model on every file would run around nine and a half thousand dollars a "
  "year at four thousand files a day, or seventeen thousand if it were allowed to loop over tools. "
  "That is not an argument against using a model. It is the product knowing exactly what the model would buy before anyone "
  "commits to paying for it."),

 ("value", [("card", "value"), ("wait", 0.5)],
  "As a business case it is simple. The value is in clearing clean files automatically. Broken files still reach a person "
  "— but with the measurements taken, the problem localised and the customer reply already drafted, which is where the "
  "rest of the saving comes from. "
  "The one number that can destroy the case is the wrong-clearance rate, which is exactly why it is a ceiling rather than "
  "a nice-to-have. And the optional model is treated as a reversible experiment: worth about a hundred and ninety dollars "
  "a day of reviewer time for twenty-six dollars of tokens, measured across only three cases, so it gets proven on live "
  "traffic or dropped."),

 ("lettering", [("app", "/lettering"), ("wait", 1.6), ("scroll", 520)],
  "This page covers the hardest check in the product: text too small to print. "
  "Regions the detector flagged were put in front of a person, and the product records both sides of that comparison. The "
  "system's confidence is a real measurement — text height as a share of the minimum for the product — rather than a fixed "
  "number that looks like confidence but is not. "
  "And the human answers are measured for consistency too, including repeats answered differently. That is the honest "
  "position on this check: human labels are a signal with an error rate, not ground truth, and the page says plainly that "
  "no rule was built from them. Where the system and a person disagree, that is a question for the queue, not a silent "
  "decision."),

 ("integration", [("card", "integration"), ("wait", 0.5)],
  "Fitting it into an operation. The checker is a service: artwork plus order context in, a structured verdict out, with "
  "every finding, its evidence, the drafted message and a confidence value. "
  "The page in this walkthrough is one front end over that service, and the same check is exposed as a tool an existing "
  "support or operations agent can call directly. It needs no GPU and no API key at inference, and nothing is stored — an "
  "uploaded file lives in a temporary directory for the length of one request. "
  "Each check logs its inputs, measurements, verdict, latency and cost, which are the inputs to a queue-depth and "
  "wrong-clearance dashboard."),

 ("limits", [("card", "limits"), ("wait", 0.5)],
  "What it does not do yet, stated plainly, because a checking tool that hides its blind spots gets trusted exactly where "
  "it should not be. "
  "It has not been tested on real customer uploads, which carry colour profiles, layers and fonts that do not render as "
  "expected. Known misses: grey artwork the text detector reads as text, near-white art on white stock, low-resolution "
  "art upscaled to look sharp. "
  "Zero wrong clearances in five hundred is not a zero rate; the exact upper bound is zero point six percent — inside the "
  "ceiling, but not zero. There is no adversarial test suite yet. "
  "And some things are out of scope on purpose: it does not fix artwork, generate proofs, screen for intellectual property, "
  "or touch anything downstream of approval."),

 ("close", [("card", "close"), ("wait", 0.5)],
  "So where it goes next. "
  "Run it in shadow mode beside a live queue: same files, no automatic clearance, and compare its verdicts against what "
  "reviewers decided. That produces the one number this dataset cannot settle — the wrong-clearance rate on real uploads. "
  "Then build the adversarial suite, and switch automatic clearance on for the product types where the measured rate "
  "supports it, one at a time. "
  "The outcome it is built for is straightforward: clean work clears itself, broken work arrives explained, and people "
  "spend their judgement on the files that actually need it."),
]
