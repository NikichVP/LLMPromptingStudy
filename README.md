# PDF → TXT Extractor

This repo contains a small script that extracts *all available information* from PDFs into `.txt` files:

- Page text (`[TEXT]`)
- Page links (`[LINKS]`)
- Page annotations (`[ANNOTATIONS]`)
- Optional embedded image extraction (`[IMAGES]`)
- Optional span-level dump with coordinates (`[SPANS]`) for maximum fidelity
- Optional OCR (`[OCR]`) when a page has no extractable text (requires `tesseract`)

## Setup (recommended: Python 3.11)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Run

Extract all PDFs in the current folder into `./extracted_txt/`:

```bash
python extractor.py
```

More complete extraction:

```bash
python extractor.py --include-spans --images --ocr auto
```

## OCR (optional)

OCR is only used when a page’s extracted text is empty (`--ocr auto`) or on every page (`--ocr force`).

Install `tesseract` on macOS:

```bash
brew install tesseract
```

## Output

Outputs go to `./extracted_txt/` by default:

- `extracted_txt/<pdf_stem>.txt`
- `extracted_txt/<pdf_stem>_images/...` (when `--images` is enabled)

