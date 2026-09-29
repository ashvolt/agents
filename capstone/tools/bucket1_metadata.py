"""Bucket 1 — checks computable from file metadata. Exact, offline, ~free.

Every function here is pure: open the file, measure, compare to the spec, return issues.
No model, no network, no randomness. SC-008 requires these to be bit-identical across
runs, which is why nothing in this module samples or rounds loosely.

**A note on decoupling.** `LOW_RESOLUTION` and `MISSING_BLEED` both look like "the file is
too small", and a naive implementation makes one fire whenever the other does. They are
separated as follows:

  - resolution  = pixels per inch the file *declares* (embedded DPI), i.e. how sharp the
                  artwork is at its intended size.
  - bleed       = the file's *physical* size (pixels / declared DPI) against trim + bleed.

A file that lies about its DPI — 10 pixels claiming 300 DPI — passes the resolution check
and is caught by the bleed check, because its physical size collapses to nothing. The pair
is sound together; neither is sound alone.
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from capstone.src.schemas import (
    CustomerAdvice,
    Evidence,
    Issue,
    IssueCode,
    OrderMetadata,
    ProductSpec,
    Severity,
)

# Embedded DPI round-trips imprecisely through PNG (150 -> 150.0124), so comparisons
# allow a small relative slack. Anything inside this is metadata noise, not a defect.
DPI_EPSILON_RATIO = 0.005

# Physical size tolerance for the bleed check. A file 1% under is a rounding artefact;
# a file 10% under has no bleed.
BLEED_EPSILON_RATIO = 0.02

SUPPORTED_SUFFIXES = frozenset({".png", ".tif", ".tiff", ".jpg", ".jpeg"})

# Pillow modes mapped to the colour families a spec can accept.
_MODE_FAMILY = {
    "CMYK": "CMYK",
    "RGB": "RGB",
    "RGBA": "RGB",
    "P": "RGB",
    "L": "GRAY",
    "LA": "GRAY",
    "1": "GRAY",
}


class FileMetadata:
    """What we could read off the file. Immutable, cheap, and the only IO in bucket 1."""

    __slots__ = ("path", "mode", "width_px", "height_px", "declared_dpi", "has_alpha", "error")

    def __init__(
        self,
        path: Path,
        *,
        mode: str | None = None,
        width_px: int = 0,
        height_px: int = 0,
        declared_dpi: float | None = None,
        has_alpha: bool = False,
        error: str | None = None,
    ) -> None:
        self.path = path
        self.mode = mode
        self.width_px = width_px
        self.height_px = height_px
        self.declared_dpi = declared_dpi
        self.has_alpha = has_alpha
        self.error = error

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def physical_width_in(self) -> float | None:
        if not self.declared_dpi:
            return None
        return self.width_px / self.declared_dpi

    @property
    def physical_height_in(self) -> float | None:
        if not self.declared_dpi:
            return None
        return self.height_px / self.declared_dpi

    @property
    def aspect_ratio(self) -> float | None:
        return self.width_px / self.height_px if self.height_px else None


def read_metadata(path: str | Path) -> FileMetadata:
    """Open a file and read what preflight needs. Never raises.

    FR-004 and Constitution Principle IV: a corrupt upload must become an ESCALATE, never
    an exception that some caller's `except` turns into a default. So every failure mode
    here returns a FileMetadata carrying an `error` string.
    """
    p = Path(path)

    if not p.exists():
        return FileMetadata(p, error="file does not exist")
    if p.stat().st_size == 0:
        return FileMetadata(p, error="file is empty")
    if p.suffix.lower() not in SUPPORTED_SUFFIXES:
        return FileMetadata(p, error=f"unsupported format {p.suffix!r}")

    try:
        # A decompression bomb (a small file that decodes to hundreds of megapixels) is
        # an unreadable upload, not a crash. Pillow raises past ~179 MP and only warns
        # past ~89 MP; the warning is promoted so neither size gets decoded.
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(p) as probe:
                probe.verify()  # catches truncation and structural corruption
            with Image.open(p) as img:
                img.load()  # catches truncated pixel data that verify() misses
                mode = img.mode
                width, height = img.size
                dpi_pair = img.info.get("dpi")
                has_alpha = mode in ("RGBA", "LA") or "transparency" in img.info
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        return FileMetadata(p, error=f"{type(exc).__name__}: {exc}")

    dpi: float | None = None
    if dpi_pair:
        try:
            dpi = float(dpi_pair[0])
        except (TypeError, ValueError, IndexError):
            dpi = None
    if dpi is not None and dpi <= 0:
        dpi = None

    return FileMetadata(
        p,
        mode=mode,
        width_px=width,
        height_px=height,
        declared_dpi=dpi,
        has_alpha=has_alpha,
    )


# --------------------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------------------


def check_readable(meta: FileMetadata) -> list[Issue]:
    if meta.ok:
        return []
    return [
        Issue(
            code=IssueCode.UNREADABLE_FILE,
            severity=Severity.BLOCKING,
            message="The uploaded file could not be opened.",
            evidence=Evidence(note=meta.error or "unknown read failure"),
            advice=CustomerAdvice(
                headline="We could not open your file, so we have not printed anything yet.",
                action=(
                    "Send it again as a PNG, TIFF or JPEG, exported from the program you "
                    "designed it in."
                ),
                avoid=(
                    "renaming a file to .png or .jpg - that changes the name but not what "
                    "is inside, and it will fail to open again"
                ),
            ),
        )
    ]


def check_color_mode(meta: FileMetadata, spec: ProductSpec) -> list[Issue]:
    if not meta.ok or meta.mode is None:
        return []
    family = _MODE_FAMILY.get(meta.mode, meta.mode)
    if family in spec.accepted_color_modes:
        return []
    accepted = "/".join(spec.accepted_color_modes)
    if family in spec.converted_color_modes:
        return [
            Issue(
                code=IssueCode.WRONG_COLOR_MODE,
                severity=Severity.ADVISORY,
                message=(
                    f"Artwork is {family}; we convert it to {accepted} for printing. Very "
                    "bright colours, especially neon greens and blues, may print slightly duller."
                ),
                evidence=Evidence(
                    note=f"file mode {meta.mode} -> {family}, converted to {accepted}"
                ),
                advice=CustomerAdvice(
                    headline=(
                        "Very bright colours may print a little duller than they look on "
                        "screen."
                    ),
                    action=(
                        "Nothing to do - we handle the conversion for you. If an exact "
                        "colour matters (a brand colour, for instance), ask us for a "
                        "printed sample before the full run."
                    ),
                ),
            )
        ]
    return [
        Issue(
            code=IssueCode.WRONG_COLOR_MODE,
            severity=Severity.BLOCKING,
            message=(
                f"Artwork is {family}; {spec.display_name} is printed in {accepted}. "
                "Converting at the press shifts colour unpredictably."
            ),
            evidence=Evidence(note=f"file mode {meta.mode} -> {family}, requires {accepted}"),
            advice=CustomerAdvice(
                headline=(
                    "Your colours could come out noticeably different from what you see "
                    "on screen."
                ),
                action=(
                    f"Re-export the artwork in {accepted} from the program you designed it "
                    f"in - it is usually a colour mode or colour profile setting in the "
                    f"export window."
                ),
                avoid=(
                    "converting it with a free online converter - those generally guess at "
                    "the colour profile and shift the colours further"
                ),
            ),
        )
    ]


def effective_dpi(meta: FileMetadata, spec: ProductSpec, order: OrderMetadata) -> float | None:
    """Resolution at the size this file is meant to print.

    Prefer the declared DPI. When there is none, fall back to pixels over the required
    canvas — a file with no resolution metadata will be scaled to fit, so that is exactly
    what it will resolve to.
    """
    if not meta.ok:
        return None
    if meta.declared_dpi:
        return meta.declared_dpi
    required_w = order.width_in + 2 * spec.bleed_in
    return meta.width_px / required_w if required_w else None


# How far below the minimum the file sits, and what that looks like on the printed piece.
# Three bands, because 9% short and 50% short are the same issue code and completely
# different conversations: "138 against 150" reads as badly wrong when it is 64 pixels.
_SOFTNESS_BANDS = ((0.85, "a little softer than it should"), (0.60, "noticeably soft"))
_SOFTNESS_WORST = "blurry"


# Spoken forms that start with a vowel sound, so "an 8x10 in sticker" reads correctly.
# Order sizes are validated to 120 in, so the integer part is all that has to be covered.
_AN_PREFIXES = ("8", "11", "18", "80", "81", "82", "83", "84", "85", "86", "87", "88", "89")


def _article(width_in: float) -> str:
    """"a" or "an" for a size phrase. Cosmetic, but "an 5x3 in sticker" reads as a typo,
    and a letter a customer is meant to trust cannot afford to look machine-assembled."""
    whole = str(int(width_in))
    return "an" if whole in _AN_PREFIXES else "a"


def _softness(dpi: float, min_dpi: int) -> str:
    ratio = dpi / min_dpi if min_dpi else 0.0
    for floor, phrasing in _SOFTNESS_BANDS:
        if ratio >= floor:
            return phrasing
    return _SOFTNESS_WORST


def _resolution_advice(
    meta: FileMetadata, spec: ProductSpec, order: OrderMetadata, dpi: float
) -> CustomerAdvice:
    """The resolution finding in the customer's register.

    Three branches, because one DPI number has three causes and a single shared fix would
    be false in two of them:

    1. **No DPI in the file.** `effective_dpi` fell back to pixels over the required
       canvas, so pixels are the honest unit and the only one the customer can act on.
    2. **A DPI tag, and the pixels are already sufficient.** The artwork is fine; the file
       merely says to print it at the wrong size. Telling this customer to "re-export
       larger" sends them to find detail they already have.
    3. **A DPI tag, and the pixels really are short.**

    Every branch names pixels or inches, never a bare ratio. DPI is pixels over inches: it
    is meaningless without the print size, and it is not reusable, because `min_dpi` is 72
    on a banner and 300 on a roll label. A customer who learns "150" here carries it wrong
    to their next order.

    Branches 1 and 3 carry the anti-upscale warning; branch 2 carries its own variant.
    Without it, the most obvious reading of "needs at least 150 DPI" is to set the DPI to
    150, which resamples the file, passes `check_resolution` next upload, and prints
    exactly as soft - a false approve (brief.md S6 rank 1) produced by our own wording.
    """
    name = spec.display_name.lower()
    size = f"{order.width_in:g}x{order.height_in:g} in"
    ordered = f"{_article(order.width_in)} {size}"
    canvas_w = order.width_in + 2 * spec.bleed_in
    canvas_h = order.height_in + 2 * spec.bleed_in
    need_w = math.ceil(spec.min_dpi * canvas_w)
    need_h = math.ceil(spec.min_dpi * canvas_h)
    headline = f"Your {name} will print {_softness(dpi, spec.min_dpi)}."
    no_upscale = (
        f"enlarging this copy, or typing {spec.min_dpi} into a DPI box - that adds pixels "
        "without adding detail, and it will still print soft"
    )

    if meta.declared_dpi is None:
        short_by = math.ceil((spec.min_dpi / dpi - 1) * 100) if dpi > 0 else 100
        return CustomerAdvice(
            headline=headline,
            action=(
                f"The image is {meta.width_px}x{meta.height_px} pixels; {ordered} {name} "
                f"needs {need_w}x{need_h}. If you still have the file you designed it in, "
                f"export it again about {short_by}% larger."
            ),
            avoid=no_upscale,
        )

    physical_w = meta.width_px / meta.declared_dpi
    physical_h = meta.height_px / meta.declared_dpi
    if meta.width_px >= need_w and meta.height_px >= need_h:
        return CustomerAdvice(
            headline=(
                "Your file is set up to print much larger than the size you ordered, so we "
                "have held it rather than guess at what you wanted."
            ),
            action=(
                f"The image has plenty of detail ({meta.width_px}x{meta.height_px} pixels), "
                f"but the file says to print it at {physical_w:.1f}x{physical_h:.1f} in "
                f"rather than {size}. Export it again for {ordered} print - the artwork "
                "itself does not need to change, only the size the file is saved for."
            ),
            avoid=(
                "re-saving it at a bigger pixel size - the detail is already there, it is "
                "only the print size recorded in the file that is wrong"
            ),
        )

    return CustomerAdvice(
        headline=headline,
        action=(
            f"The file is set to {dpi:.0f} DPI at {physical_w:.1f}x{physical_h:.1f} in. For "
            f"{ordered} print we need {need_w}x{need_h} pixels or more, so export the artwork "
            f"again at {spec.min_dpi} DPI for that size."
        ),
        avoid=no_upscale,
    )


def check_resolution(meta: FileMetadata, spec: ProductSpec, order: OrderMetadata) -> list[Issue]:
    dpi = effective_dpi(meta, spec, order)
    if dpi is None:
        return []
    if dpi >= spec.min_dpi * (1 - DPI_EPSILON_RATIO):
        return []
    return [
        Issue(
            code=IssueCode.LOW_RESOLUTION,
            severity=Severity.BLOCKING,
            message=(
                f"Artwork is {dpi:.0f} DPI at the ordered size of "
                f"{order.width_in:g}x{order.height_in:g} in. "
                f"{spec.display_name} needs at least {spec.min_dpi} DPI or the print "
                "will look soft."
            ),
            evidence=Evidence(measured=round(dpi, 2), required=float(spec.min_dpi), unit="dpi"),
            advice=_resolution_advice(meta, spec, order, dpi),
        )
    ]


def measure_bleed_in(meta: FileMetadata, order: OrderMetadata) -> float | None:
    """Bleed actually present, in inches per edge. The minimum of the two axes."""
    actual_w = meta.physical_width_in
    actual_h = meta.physical_height_in
    if actual_w is None or actual_h is None:
        return None
    return min((actual_w - order.width_in) / 2, (actual_h - order.height_in) / 2)


def check_bleed(meta: FileMetadata, spec: ProductSpec, order: OrderMetadata) -> list[Issue]:
    """Bleed present against bleed required.

    Measured as bleed directly rather than as total physical size, because the quantity
    of interest is small relative to the sheet: a 10% bleed shortfall on a 2 in sticker is
    a 1% size difference, which a size-based tolerance cannot see.

    Only *undersize* is a defect. Artwork larger than required is fine — the extra is
    trimmed away, which is what bleed is for.
    """
    if not meta.ok or spec.bleed_in <= 0:
        return []
    present = measure_bleed_in(meta, order)
    if present is None:
        return []

    floor = spec.bleed_in * (1 - BLEED_EPSILON_RATIO)
    if present >= floor:
        return []

    return [
        Issue(
            code=IssueCode.MISSING_BLEED,
            severity=Severity.BLOCKING,
            message=(
                f"Artwork provides {max(0.0, present):.3f} in of bleed; "
                f"{spec.display_name} needs {spec.bleed_in:g} in on every edge. Without "
                "it the cut can expose the substrate along an edge."
            ),
            evidence=Evidence(
                measured=round(max(0.0, present), 4), required=spec.bleed_in, unit="in"
            ),
            advice=CustomerAdvice(
                headline=(
                    "A thin unprinted strip could show along one or more edges after "
                    "cutting."
                ),
                action=(
                    f"Export the artwork {spec.bleed_in:g} in larger on every side, so "
                    f"{_article(order.width_in)} "
                    f"{order.width_in:g}x{order.height_in:g} in design is exported at "
                    f"{order.width_in + 2 * spec.bleed_in:g}x"
                    f"{order.height_in + 2 * spec.bleed_in:g} in. Design tools call this "
                    "extra margin 'bleed'; let the background run out into it."
                ),
                avoid=(
                    "scaling the whole design up to fill the larger size - that pushes your "
                    "artwork out past the cut line, so the edges of it get trimmed off"
                ),
            ),
        )
    ]


def check_aspect(meta: FileMetadata, spec: ProductSpec, order: OrderMetadata) -> list[Issue]:
    """Proportions against the required canvas — trim plus bleed on every edge.

    The comparison is against the *canvas* aspect, not the trim aspect, because the
    canvas is what the file is supposed to be. Note these are different whenever the
    order is not square: a 4x2 in order with 0.125 in bleed wants a 4.25x2.25 canvas,
    which is 1.889:1, not 2:1.

    **This check is suppressed when bleed is missing** — see `inspect_file`. Without a
    trim box in the file, bleed and proportion are entangled: a file short on bleed has a
    different canvas ratio purely because of the missing margin, and reporting that as a
    proportion defect tells the customer to fix something that is not wrong. Real
    preflight tools behave the same way: correct the size first, then re-check.
    """
    if not meta.ok:
        return []
    actual = meta.aspect_ratio
    if actual is None:
        return []

    expected = (order.width_in + 2 * spec.bleed_in) / (order.height_in + 2 * spec.bleed_in)
    deviation = abs(actual / expected - 1.0)
    if deviation <= spec.aspect_tolerance:
        return []
    return [
        Issue(
            code=IssueCode.ASPECT_MISMATCH,
            severity=Severity.BLOCKING,
            message=(
                f"Artwork proportions are {actual:.3f}:1 but the ordered size with bleed "
                f"is {expected:.3f}:1. Printing it will stretch or crop the design."
            ),
            evidence=Evidence(
                measured=round(deviation, 4),
                required=spec.aspect_tolerance,
                unit="ratio deviation",
            ),
            advice=CustomerAdvice(
                headline=(
                    "Your design is a different shape from the size ordered, so printing it "
                    "as-is would stretch it or cut parts off."
                ),
                action=(
                    f"Export the artwork at "
                    f"{order.width_in + 2 * spec.bleed_in:g}x"
                    f"{order.height_in + 2 * spec.bleed_in:g} in - that is the "
                    f"{order.width_in:g}x{order.height_in:g} in you ordered plus "
                    f"{spec.bleed_in:g} in on every side. If the shape of the design is the "
                    "part you want to keep, change the order size to match it instead."
                ),
                avoid=(
                    "stretching the image to those proportions - add background around the "
                    "design instead, so nothing in it is distorted"
                ),
            ),
        )
    ]


# --------------------------------------------------------------------------------------
# The bucket
# --------------------------------------------------------------------------------------


def inspect_file(
    path: str | Path, spec: ProductSpec, order: OrderMetadata
) -> tuple[list[Issue], FileMetadata]:
    """Run every bucket-1 check. Returns issues and the metadata the rest of the run needs.

    An unreadable file short-circuits: there is nothing else meaningful to measure, and
    reporting five derived failures from one root cause would bury the actual problem in
    the escalation queue.
    """
    meta = read_metadata(path)
    if not meta.ok:
        return check_readable(meta), meta

    issues: list[Issue] = []
    issues += check_color_mode(meta, spec)
    issues += check_resolution(meta, spec, order)

    bleed_issues = check_bleed(meta, spec, order)
    issues += bleed_issues

    # Aspect is only meaningful once the file is the right physical size. See the note on
    # check_aspect: bleed and proportion are entangled without a trim box, and reporting
    # both would tell the customer to fix a proportion problem they do not have.
    if not bleed_issues:
        issues += check_aspect(meta, spec, order)

    return issues, meta


BUCKET1_CHECKS = (
    "check_readable",
    "check_color_mode",
    "check_resolution",
    "check_bleed",
    "check_aspect",
)

__all__ = [
    "BUCKET1_CHECKS",
    "FileMetadata",
    "check_aspect",
    "check_bleed",
    "check_color_mode",
    "check_readable",
    "check_resolution",
    "effective_dpi",
    "inspect_file",
    "read_metadata",
]
