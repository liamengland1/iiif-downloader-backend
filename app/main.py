import os
import shutil
from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, StreamingResponse
from iiif_download import IIIFManifest
import uuid
import img2pdf
import ocrmypdf
from pathlib import Path
from starlette.background import BackgroundTask

app = FastAPI()


@app.get("/iiif")
async def iiif_endpoint(manifestURL: str, response: Response, ocr: bool = True, pctSize: float = 0.35):
    # Load the IIIF manifest
    # manifest_url = "https://dlg.usg.edu/record/guan_1633_040-017/presentation/manifest.json"
    # Long document
    # manifest_url = "https://dlg.usg.edu/record/guan_1633_088-017/presentation/manifest.json"
    # http://localhost:8000/iiif?manifestURL=https%3A%2F%2Fdlg.usg.edu%2Frecord%2Fguan_1633_040-017%2Fpresentation%2Fmanifest.json
    manifest_url = manifestURL
    if not manifest_url or not manifest_url.startswith("http") or "manifest" not in manifest_url:
        response.status_code = 400
        return {"error": "Invalid manifest URL"}
    manifest = IIIFManifest(manifest_url, pct_size=pctSize)
    temp_dir = f"{str(uuid.uuid4())}/"
    # Download the manifest
    # await manifest.load()
    # Return the manifest data
    await manifest.download(temp_dir)

    # https://gitlab.mister-muffin.de/josch/img2pdf
    temp_dir = Path("img").joinpath(temp_dir)
    pdf_dir = temp_dir.joinpath("pdf")
    pdf_dir.mkdir(parents=True, exist_ok=True)
    image_files = sorted(temp_dir.glob("*.jpg"))
    with open(pdf_dir.joinpath("out.pdf"), "wb") as f:
        f.write(img2pdf.convert([str(img) for img in image_files]))

    if ocr:
        # OCR the PDF
        ocrmypdf.ocr(
            pdf_dir.joinpath("out.pdf"),
            pdf_dir.joinpath("out_ocr.pdf"),
            language="eng",
            deskew=False,
            rotate_pages=False,
            output_type="pdf",
            optimize=0
        )
        # Return the PDF file
        return FileResponse(pdf_dir.joinpath("out_ocr.pdf"), media_type="application/pdf")
    else:
        # Return the PDF file
        return FileResponse(pdf_dir.joinpath("out.pdf"), media_type="application/pdf")


@app.get("/iiif2")
async def iiif_endpoint_eventstream(manifestURL: str, response: Response, ocr: bool = True, pctSize: float = 0.35):
    manifest_url = manifestURL
    if not manifest_url or not manifest_url.startswith("http") or "manifest" not in manifest_url:
        response.status_code = 400
        response.headers["Content-Type"] = "application/json"
        return {"error": "Invalid manifest URL"}

    async def event_stream():
        # Step 1: Load the IIIF manifest
        yield "data: Downloading images...\n\n"
        manifest = IIIFManifest(manifest_url, pct_size=pctSize)
        temp_dir = f"{str(uuid.uuid4())}/"
        await manifest.download(temp_dir)
        yield "data: Downloading images... done.\n\n"

        # Step 2: Convert images to PDF
        yield "data: Converting images to PDF...\n\n"
        temp_dir = Path("img").joinpath(temp_dir)
        pdf_dir = temp_dir.joinpath("pdf")
        pdf_dir.mkdir(parents=True, exist_ok=True)
        image_files = sorted(temp_dir.glob("*.jpg"))
        with open(pdf_dir.joinpath("out.pdf"), "wb") as f:
            f.write(img2pdf.convert([str(img) for img in image_files]))
        yield "data: PDF created successfully.\n\n"

        # Step 3: Perform OCR if requested
        if ocr:
            yield "data: Performing OCR on the PDF...\n\n"
            ocrmypdf.ocr(
                pdf_dir.joinpath("out.pdf"),
                pdf_dir.joinpath("out_ocr.pdf"),
                language="eng",
                deskew=False,
                rotate_pages=False,
                output_type="pdf",
                optimize=0
            )
            yield "data: OCR completed successfully.\n\n"
            yield f"data:pdfurl:/tmp/{str(pdf_dir.joinpath('out_ocr.pdf')).replace('img/', '')}\n\n"
        else:
            yield f"data:pdfurl:/tmp/{str(pdf_dir.joinpath('out.pdf')).replace('img/', '')}\n\n"

        yield "data: Process completed.\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/tmp/{file_path:path}")
async def get_pdf(file_path: str, response: Response):
    file_location = Path("img").joinpath(file_path)
    if not file_location.exists():
        response.status_code = 404
        response.headers["Content-Type"] = "application/json"
        return {"error": "File not found"}
    return FileResponse(file_location, media_type="application/pdf", background=BackgroundTask(cleanup, file_location))


def cleanup(path: Path):
    target_dir = path.parent.parent
    if target_dir.exists():
        shutil.rmtree(target_dir)
    else:
        os.remove(path)
