"""
Pulls a shipment's label + packing slip out of a wall set's master PDF, for
the "print this one shipment's paperwork" case (packer finishes a
shipment, or an admin re-prints it after the fact).

TikTok's export alternates: the label image page is immediately followed
by that same shipment's packing slip text page (product names, quantities,
order ID, tracking number) -- see order_ingest.index_label_pages_by_tracking.
Shipment.pdf_label_page_index stores only the label page, so the packing
slip is always label_page_index + 1.
"""
import fitz  # PyMuPDF


def extract_label_and_packing_slip(pdf_bytes: bytes, label_page_index: int) -> bytes:
    """Returns a standalone two-page PDF (as bytes): the label page at
    `label_page_index`, followed by its packing slip at label_page_index + 1,
    both from the master PDF given as `pdf_bytes`."""
    source = fitz.open(stream=pdf_bytes, filetype="pdf")
    output = fitz.open()
    output.insert_pdf(source, from_page=label_page_index, to_page=label_page_index + 1)
    data = output.tobytes()
    output.close()
    source.close()
    return data
