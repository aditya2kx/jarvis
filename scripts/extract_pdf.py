#!/usr/bin/env python3
"""Download a PDF from Google Drive and extract text with pdfplumber."""

import os
import sys
import urllib.request
import pdfplumber

from config_loader import refresh_access_token, project_dir


def download_pdf(file_id, dest_path, access_token):
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req) as resp:
        with open(dest_path, "wb") as f:
            f.write(resp.read())
    print(f"Downloaded to {dest_path} ({os.path.getsize(dest_path)} bytes)")


def extract_text(pdf_path, output_path):
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        print(f"PDF has {len(pdf.pages)} pages")
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            pages.append(f"--- PAGE {i + 1} ---\n{text}")
    full_text = "\n\n".join(pages)
    with open(output_path, "w") as f:
        f.write(full_text)
    print(f"Extracted {len(pages)} pages -> {output_path} ({len(full_text)} chars)")
    return full_text


def main():
    if len(sys.argv) < 2:
        print(
            "Usage: extract_pdf.py <drive_file_id> [output_pdf_basename]",
            file=sys.stderr,
        )
        sys.exit(1)
    file_id = sys.argv[1]
    pdf_name = sys.argv[2] if len(sys.argv) > 2 else "2024-federal-return.pdf"

    extracted_dir = os.path.join(project_dir(), "extracted")
    os.makedirs(extracted_dir, exist_ok=True)

    pdf_path = os.path.join(extracted_dir, pdf_name)
    txt_path = os.path.splitext(pdf_path)[0] + ".txt"

    token = refresh_access_token()

    download_pdf(file_id, pdf_path, token)
    extract_text(pdf_path, txt_path)


if __name__ == "__main__":
    main()
