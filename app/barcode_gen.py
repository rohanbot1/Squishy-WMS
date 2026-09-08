"""
Generates printable barcode labels for a wall set's manifest.

Encodes SquishyType.internal_code in the barcode, never the display name.
Label printers use limited built-in fonts that generally can't render
emoji glyphs, and the raw name isn't a great barcode payload anyway
(unnecessary length, special characters). The barcode just needs to
resolve back to a SquishyType on scan; the emoji name is a UI/display
concern only.

Printed on a Rollo direct-thermal printer: 3in x 1in label rolls fed one
label at a time through Rollo's own driver. No sheet/grid layout -- each
physical label is its own PDF page, sized to exactly match the roll.
"""
import io
import barcode
from barcode.writer import ImageWriter
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

LABEL_WIDTH = 3 * inch
LABEL_HEIGHT = 1 * inch

# Layout budget for a 216pt x 72pt page: margins for thermal-print edge
# safety, a barcode area sized to actually use the label's width (the
# binding constraint on scannability), and a single text line below it.
MARGIN_X = 6
MARGIN_Y = 5
TEXT_HEIGHT = 13
GAP = 3
FONT_NAME = "Helvetica-Bold"
FONT_SIZE = 9

BARCODE_BOX_WIDTH = LABEL_WIDTH - 2 * MARGIN_X
BARCODE_BOX_HEIGHT = LABEL_HEIGHT - 2 * MARGIN_Y - TEXT_HEIGHT - GAP
BARCODE_BOX_X = MARGIN_X
BARCODE_BOX_Y = MARGIN_Y + TEXT_HEIGHT + GAP


def generate_barcode_image(internal_code: str) -> io.BytesIO:
    buffer = io.BytesIO()
    code128 = barcode.get("code128", internal_code, writer=ImageWriter())
    code128.write(buffer, options={
        # The raw code is only for the scanner; write_text is skipped so
        # the barcode doesn't have to compete with the display name for
        # the label's very limited vertical space.
        "write_text": False,
        "quiet_zone": 1.5,
        "module_height": 10.0,
        # Wider than python-barcode's 0.2mm default -- at the box's
        # height-constrained fit, the default renders a barcode sized for
        # the old 2in label regardless of how wide the page actually is,
        # leaving it stranded in empty space on a 3in one. This is tuned
        # to actually use the wider label, verified by rendering a page.
        "module_width": 0.42,
    })
    buffer.seek(0)
    return buffer


def _truncate_to_width(c: canvas.Canvas, text: str, max_width: float) -> str:
    """Shortens text with a trailing ellipsis until it fits max_width at the
    label's font/size, instead of a fixed character count that made sense
    for the old, much larger sheet label but would overflow a 2in-wide one."""
    if c.stringWidth(text, FONT_NAME, FONT_SIZE) <= max_width:
        return text
    ellipsis = "…"
    truncated = text
    while truncated and c.stringWidth(truncated + ellipsis, FONT_NAME, FONT_SIZE) > max_width:
        truncated = truncated[:-1]
    return (truncated + ellipsis) if truncated else ellipsis


def generate_label_sheet_pdf(items: list[dict]) -> bytes:
    """items: [{"internal_code": str, "display_name": str, "quantity": int}]
    One label per physical unit (a qty-150 item makes 150 pages), each page
    exactly 3in x 1in to match the Rollo roll -- no grid, the printer feeds
    one label at a time regardless of what's on the page.

    Returns the PDF as bytes, built entirely in memory -- this is a
    generated, immediately-served artifact that nothing ever reads back
    after the request that asked for it, so it never touches storage at
    all (not local disk, not R2), in any deployment.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(LABEL_WIDTH, LABEL_HEIGHT))
    c.setFont(FONT_NAME, FONT_SIZE)

    first_page = True
    for item in items:
        barcode_buf = generate_barcode_image(item["internal_code"])
        barcode_image = ImageReader(barcode_buf)
        label_text = _truncate_to_width(c, item["display_name"], LABEL_WIDTH - 2 * MARGIN_X)

        for _ in range(item["quantity"]):
            if not first_page:
                c.showPage()
                c.setFont(FONT_NAME, FONT_SIZE)
            first_page = False

            c.drawImage(
                barcode_image,
                BARCODE_BOX_X, BARCODE_BOX_Y,
                width=BARCODE_BOX_WIDTH, height=BARCODE_BOX_HEIGHT,
                preserveAspectRatio=True, anchor="c",
            )
            c.drawCentredString(LABEL_WIDTH / 2, MARGIN_Y + 2, label_text)

    c.save()
    return buffer.getvalue()
